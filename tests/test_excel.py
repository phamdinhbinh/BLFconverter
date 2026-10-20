from pathlib import Path
from threading import Event
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import pytest

from blf_converter.decoder import DecodedGroup, DecodedSignal
from blf_converter.excel import ExcelCancelled, StreamingExcelWriter


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def group(values, *, name="Engine", signal_name="CAN1.Engine.RPM", times=None):
    samples = np.asarray(values)
    return DecodedGroup(np.arange(len(samples), dtype=float) if times is None else np.asarray(times),
                        (DecodedSignal(signal_name, "rpm", samples, "=2+2 & <comment>"),),
                        name, 1, 0x123, False)


def sheet_rows(path, index):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in root.findall("s:sheetData/s:row", NS):
        result = []
        for cell in row:
            if cell.get("t") == "inlineStr":
                result.append(cell.find("s:is/s:t", NS).text or "")
            else:
                node = cell.find("s:v", NS)
                result.append(node.text if node is not None else None)
        rows.append(result)
    return rows


def test_split_and_interleave_preserves_all_samples(tmp_path):
    writer = StreamingExcelWriter(tmp_path, "log", {"Source": "vehicle.blf"}, max_rows=4, max_cells=16)
    writer.append(group([1, 2]))
    writer.append(group([7], name="Other", signal_name="CAN1.Other.Value"))
    writer.append(group([3, 4, 5], times=[2, 3, 4]))
    paths = writer.close()
    assert len(paths) == 2
    values = []
    for path in paths:
        with ZipFile(path) as archive:
            assert archive.testzip() is None
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets = workbook.find("s:sheets", NS)
            for i, sheet in enumerate(sheets, 1):
                if "Engine" in sheet.attrib["name"]:
                    values.extend(sheet_rows(path, i)[1:])
    assert values == [["0", "1"], ["1", "2"], ["2", "3"], ["3", "4"], ["4", "5"]]
    assert not list(tmp_path.glob(".xlsx-xml-*"))


def test_precision_nonfinite_and_formula_strings(tmp_path):
    writer = StreamingExcelWriter(tmp_path, "special", {"=formula": "=cmd|'/C bad'!A0"})
    writer.append(group(np.array([2**64 - 1, 123], dtype=np.uint64), name='A"&/B', signal_name="=formula"))
    writer.append(group([np.nan, np.inf, -np.inf, 0.12345678901234567], name="Float", signal_name="Float.value"))
    path, = writer.close()
    assert sheet_rows(path, 2)[1:] == [["0", str(2**64 - 1)], ["1", "123"]]
    assert sheet_rows(path, 3)[1:] == [["0", None], ["1", None], ["2", None], ["3", "0.12345678901234566"]]
    with ZipFile(path) as archive:
        xml = archive.read("xl/worksheets/sheet2.xml")
        assert b'<f>' not in xml
        root = ET.fromstring(xml)
        assert root.find("s:sheetData/s:row[2]/s:c[2]", NS).get("t") == "inlineStr"
        for item in archive.namelist():
            if item.endswith("xml") or item.endswith("rels"):
                ET.fromstring(archive.read(item))


def test_many_workbooks_no_retained_arrays(tmp_path):
    writer = StreamingExcelWriter(tmp_path, "small", max_rows=3, max_cells=6)
    writer.append(group(range(7)))
    assert len(writer.close()) == 4
    assert writer.close() == writer.paths
    with pytest.raises(ValueError, match="closed"):
        writer.append(group([1]))


def test_cancel_abort(tmp_path):
    event = Event()
    writer = StreamingExcelWriter(tmp_path, "cancel", max_cells=6, cancel=event)
    writer.append(group([1, 2, 3]))
    event.set()
    with pytest.raises(ExcelCancelled):
        writer.append(group([4]))
    writer.abort()
    assert list(tmp_path.iterdir()) == []


def test_empty_and_limits(tmp_path):
    writer = StreamingExcelWriter(tmp_path, "empty")
    assert writer.close() == []
    with pytest.raises(ValueError):
        StreamingExcelWriter(tmp_path, "bad", max_rows=1)
    writer = StreamingExcelWriter(tmp_path, "bad", max_cells=4)
    bad = group([1])
    bad = DecodedGroup(bad.timestamps, bad.signals * 3, "TooWide", 1, 1, False)
    with pytest.raises(ValueError, match="too small"):
        writer.append(bad)
    writer.abort()
