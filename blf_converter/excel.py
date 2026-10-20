"""Bounded-memory XLSX archive writer with fast, lightly compressed OOXML.

Rows are written once to temporary XML streams.  No shared-string table,
dataframe, or workbook-sized Python object is constructed.  A data sheet keeps
the original timestamps of one CAN message/multiplexer signal group.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
import shutil
import tempfile
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np


_XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_SHEET_START = (_XML_HEADER + f'<worksheet xmlns="{_MAIN_NS}"><sheetData>\n').encode()
_SHEET_END = b"</sheetData></worksheet>"
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")


class ExcelCancelled(Exception):
    """Writing stopped at the caller's request."""


def _text(value: Any) -> str:
    # Inline strings cannot become formulas, even for DBC strings beginning '='.
    value = _INVALID_XML.sub("", str(value))[:32767]
    return '<c t="inlineStr"><is><t xml:space="preserve">' + escape(value) + "</t></is></c>"


def _value(value: Any) -> str:
    if isinstance(value, (int, np.integer)):
        number = int(value)
        # Excel stores only 15 significant decimal digits. Keep CAN counters and
        # identifiers with more digits as text instead of silently rounding them.
        if abs(number) >= 1_000_000_000_000_000:
            return _text(number)
        return f"<c><v>{number}</v></c>"
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return f"<c><v>{number:.17g}</v></c>" if math.isfinite(number) else "<c/>"
    if value is None:
        return "<c/>"
    return _text(value)


def _row(values: Any, *, strings: bool = False) -> str:
    encode = _text if strings else _value
    return "<row>" + "".join(encode(value) for value in values) + "</row>\n"


@dataclass
class _Sheet:
    name: str
    path: Path
    rows: int
    group: Any = None


class StreamingExcelWriter:
    """Create one or more XLSX files in ``directory`` without retaining samples.

    ``max_rows`` includes the column header. ``max_cells`` bounds each workbook's
    data cells and headers; the small metadata index is additional. Cancellation
    is checked every 2,048 rows and before packaging each XML sheet.
    """

    def __init__(
        self,
        directory: Path | str,
        stem: str,
        metadata: dict | None = None,
        max_rows: int = 1_048_576,
        max_cells: int = 8_000_000,
        cancel: Any = None,
    ):
        if not 2 <= max_rows <= 1_048_576:
            raise ValueError("Excel max_rows must be between 2 and 1048576")
        if max_cells < 4:
            raise ValueError("Excel max_cells must be at least 4")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stem = stem
        self.metadata = dict(metadata or {})
        self.max_rows = max_rows
        self.max_cells = max_cells
        self.cancel = cancel
        self.paths: list[Path] = []
        self._temporary = Path(tempfile.mkdtemp(prefix=".xlsx-xml-", dir=self.directory))
        self._sheets: list[_Sheet] = []
        self._active: dict[tuple, _Sheet] = {}
        self._cells = 0
        self._closed = False

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise ExcelCancelled("Excel export cancelled")

    @staticmethod
    def _key(group: Any) -> tuple:
        return (group.channel, group.frame_id, group.is_extended_id,
                tuple(signal.name for signal in group.signals))

    def _new_sheet(self, group: Any) -> _Sheet:
        number = len(self._sheets) + 1
        suffix = f"_{number}"
        name = _BAD_SHEET.sub("_", _INVALID_XML.sub("", f"CAN{group.channel}_{group.message_name}"))
        name = name.strip("'")[:31 - len(suffix)] + suffix
        path = self._temporary / f"sheet{number}.xml"
        with path.open("wb") as stream:
            stream.write(_SHEET_START)
            stream.write(_row(["Time [s]"] + [s.name for s in group.signals], strings=True).encode("utf-8"))
        # Do not retain group.timestamps/samples here: a single batch can be large.
        descriptors = [(s.name, s.unit, s.comment) for s in group.signals]
        description = (group.channel, group.message_name, group.frame_id,
                       group.is_extended_id, descriptors)
        sheet = _Sheet(name, path, 1, description)
        self._sheets.append(sheet)
        self._active[self._key(group)] = sheet
        self._cells += len(group.signals) + 1
        return sheet

    def append(self, group: Any) -> None:
        if self._closed:
            raise ValueError("Excel writer is closed")
        self._check_cancel()
        count = len(group.timestamps)
        if not count or not group.signals:
            return
        width = len(group.signals) + 1
        if width > 16_384:
            raise ValueError("A CAN group exceeds Excel's 16384-column limit")
        if width * 2 > self.max_cells:
            raise ValueError("Excel max_cells is too small for this signal group")
        if any(len(s.samples) != count for s in group.signals):
            raise ValueError("Excel signal lengths differ from timestamp length")
        key = self._key(group)
        offset = 0
        while offset < count:
            self._check_cancel()
            sheet = self._active.get(key)
            needs_header = sheet is None or sheet.rows == self.max_rows
            reserved = width if needs_header else 0
            if self._cells + reserved + width > self.max_cells:
                self._finish_workbook()
                sheet = None
            if sheet is None or sheet.rows == self.max_rows:
                sheet = self._new_sheet(group)
            available = min(self.max_rows - sheet.rows,
                            (self.max_cells - self._cells) // width,
                            count - offset)
            # Bound both rows and cells per encoding buffer. Converting these
            # small NumPy slices to native scalars in C avoids constructing one
            # NumPy scalar in Python per cell during the XML loop.
            with sheet.path.open("ab", buffering=1024 * 1024) as stream:
                stop = offset + available
                block_rows = max(1, min(2048, 65_536 // width))
                while offset < stop:
                    self._check_cancel()
                    end = min(offset + block_rows, stop)
                    columns = [group.timestamps[offset:end].tolist()]
                    columns.extend(s.samples[offset:end].tolist() for s in group.signals)
                    stream.write("".join(_row(values) for values in zip(*columns)).encode("utf-8"))
                    offset = end
            sheet.rows += available
            self._cells += available * width

    def _index_sheets(self) -> list[_Sheet]:
        sheets: list[_Sheet] = []

        def rows():
            yield ["Field", "Value / Sheet", "Channel", "Message", "CAN ID", "Signal", "Unit", "Comment"]
            for name, value in self.metadata.items():
                yield [name, value]
            yield ["Time", "Seconds from BLF log start; original sample times, no resampling"]
            yield ["Integer precision", "Integers with more than 15 digits are text to preserve exact values"]
            yield ["Floating point", "Excel uses IEEE-754 binary64, with up to 15 decimal digits in its UI"]
            yield ["Non-finite values", "NaN and +/-Infinity are empty cells; use MF4 for exact floating-point storage"]
            for sheet in self._sheets:
                channel, message, frame_id, extended, signals = sheet.group
                label = f"0x{frame_id:X}" + (" (extended)" if extended else " (standard)")
                yield ["Data sheet", sheet.name, channel, message, label, "", "", f"{sheet.rows - 1} samples"]
                for name, unit, comment in signals:
                    yield ["Signal", sheet.name, channel, message, label, name, unit, comment]

        stream = None
        try:
            for values in rows():
                if stream is None or sheets[-1].rows == 1_048_576:
                    if stream is not None:
                        stream.write(_SHEET_END)
                        stream.close()
                    number = len(sheets) + 1
                    path = self._temporary / f"index{number}.xml"
                    sheets.append(_Sheet("Index" if number == 1 else f"Index_{number}", path, 0))
                    stream = path.open("wb", buffering=1024 * 1024)
                    stream.write(_SHEET_START)
                stream.write(_row(values, strings=True).encode("utf-8"))
                sheets[-1].rows += 1
            if stream is not None:
                stream.write(_SHEET_END)
        finally:
            if stream is not None:
                stream.close()
        return sheets

    def _finish_workbook(self) -> None:
        if not self._sheets:
            return
        self._check_cancel()
        for sheet in self._sheets:
            with sheet.path.open("ab") as stream:
                stream.write(_SHEET_END)
        all_sheets = self._index_sheets() + self._sheets
        number = len(self.paths) + 1
        path = self.directory / f"{self.stem}_{number:03d}.xlsx"
        # XML compresses well at level 1. Higher levels add CPU cost for little
        # archival benefit on numeric logs. allowZip64 also handles XML >4 GiB.
        with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            archive.writestr("[Content_Types].xml", _XML_HEADER +
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
                "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                        for i in range(1, len(all_sheets) + 1)) + '</Types>')
            archive.writestr("_rels/.rels", _XML_HEADER +
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>')
            archive.writestr("xl/workbook.xml", _XML_HEADER +
                f'<workbook xmlns="{_MAIN_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' +
                "".join(f'<sheet name="{escape(s.name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                        for i, s in enumerate(all_sheets, 1)) + '</sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", _XML_HEADER +
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                        for i in range(1, len(all_sheets) + 1)) + '</Relationships>')
            for i, sheet in enumerate(all_sheets, 1):
                self._check_cancel()
                archive.write(sheet.path, f"xl/worksheets/sheet{i}.xml")
        self.paths.append(path)
        for sheet in all_sheets:
            sheet.path.unlink()
        self._sheets.clear()
        self._active.clear()
        self._cells = 0

    def close(self) -> list[Path]:
        if not self._closed:
            self._finish_workbook()
            self._closed = True
            shutil.rmtree(self._temporary, ignore_errors=True)
        return list(self.paths)

    def abort(self) -> None:
        self._closed = True
        shutil.rmtree(self._temporary, ignore_errors=True)
        for path in self.paths:
            path.unlink(missing_ok=True)
        # Also remove a workbook whose ZIP close was interrupted or failed.
        path = self.directory / f"{self.stem}_{len(self.paths) + 1:03d}.xlsx"
        path.unlink(missing_ok=True)
