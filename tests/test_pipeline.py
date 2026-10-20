"""End-to-end checks against independent python-can/cantools readers."""
from collections import defaultdict
import json
from pathlib import Path
import threading
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from asammdf import MDF
import can
import cantools
import numpy as np
import pytest

from blf_converter.decoder import DBCBinding
from blf_converter.pipeline import ConversionCancelled, ConversionOptions, convert_file
from tools.generate_demo import generate_demo


def reference_channels(blf_path, dbc_path):
    database = cantools.database.load_file(str(dbc_path))
    messages = {(m.frame_id, m.is_extended_frame): m for m in database.messages}
    channels = defaultdict(lambda: ([], []))
    unknown = 0
    with can.BLFReader(str(blf_path)) as reader:
        start = reader.start_timestamp
        for frame in reader:
            message = messages.get((frame.arbitration_id, frame.is_extended_id))
            if message is None:
                unknown += 1
                continue
            decoded = message.decode(frame.data, decode_choices=False)
            for signal_name, value in decoded.items():
                name = f"CAN{frame.channel + 1}.{message.name}.{signal_name}"
                times, values = channels[name]
                times.append(frame.timestamp - start)
                values.append(value)
    return channels, unknown


def test_complete_conversion_matches_reference_across_batches(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=22_000)
    expected, unknown = reference_channels(blf, dbc)
    output = tmp_path / "output"
    events = []
    result = convert_file(blf, output, [DBCBinding(dbc)],
                          ConversionOptions(chunk_mb=1, excel_max_cells=10_000),
                          progress=events.append)
    assert result.mf4_path.is_file()
    assert len(result.excel_paths) > 1
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["unknown_frames"] == unknown
    assert sum(item["frames"] for item in report["unknown_ids"]) == unknown
    assert result.decoded_samples == sum(len(values) for _, values in expected.values())
    with MDF(result.mf4_path) as mdf:
        assert mdf.version == "4.10"
        for name, (times, values) in expected.items():
            actual = mdf.get(name)
            # python-can's public timestamp adds an epoch float and therefore
            # rounds at about 0.24 us; the converter retains relative ns.
            np.testing.assert_allclose(actual.timestamps, times, rtol=0, atol=2.5e-7)
            if "Counter" in name or "EnergyAtTail" in name:
                np.testing.assert_array_equal(actual.samples, np.asarray(values, dtype=np.uint64))
            else:
                np.testing.assert_allclose(actual.samples, values, rtol=1e-12, atol=1e-12)
    assert events[-1]["phase"] == "done"
    assert not list(output.glob(".*work*"))
    assert not list(output.glob("*.lock"))

    # Read the produced OOXML independently. Check numeric sample count and
    # exact text preservation of the large uint64 CAN counters in every part.
    rows = 0
    exact_big_values = []
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for workbook in result.excel_paths:
        with ZipFile(workbook) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                    sheet = ET.fromstring(archive.read(name))
                    sheet_rows = sheet.findall("s:sheetData/s:row", ns)
                    if not sheet_rows:
                        continue
                    first = sheet_rows[0].find("s:c/s:is/s:t", ns)
                    if first is None or first.text != "Time [s]":
                        continue
                    for row in sheet_rows[1:]:
                        cells = row.findall("s:c", ns)
                        rows += len(cells) - 1
                        for cell in cells:
                            text = cell.find("s:is/s:t", ns)
                            if text is not None and text.text.isdecimal():
                                exact_big_values.append(int(text.text))
    assert rows == result.decoded_samples
    assert 2 ** 63 in exact_big_values
    assert 2 ** 63 + 1000 in exact_big_values


def test_no_overwrite_and_mf4_only(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=84)
    output = tmp_path / "output"
    options = ConversionOptions(write_excel=False)
    first = convert_file(blf, output, [DBCBinding(dbc)], options)
    original = first.mf4_path.read_bytes()
    second = convert_file(blf, output, [DBCBinding(dbc)], options)
    assert second.mf4_path != first.mf4_path
    assert first.mf4_path.read_bytes() == original
    assert first.excel_paths == second.excel_paths == []
    assert len(list(output.glob("*.report.json"))) == 2


def test_excel_only(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=84)
    result = convert_file(blf, tmp_path / "output", [DBCBinding(dbc)],
                          ConversionOptions(write_mf4=False))
    assert result.mf4_path is None
    assert result.excel_paths and all(p.is_file() for p in result.excel_paths)


def test_mf4_deflate_compression_roundtrip(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=84)
    result = convert_file(blf, tmp_path / "output", [DBCBinding(dbc)],
                          ConversionOptions(write_excel=False, compression=1))
    with MDF(result.mf4_path) as mdf:
        assert mdf.version == "4.10"
        np.testing.assert_array_equal(mdf.get("CAN1.ExtendedStatus.BigCounter").samples,
                                      np.asarray([2 ** 63 + i for i in range(12)], dtype=np.uint64))


def test_repeated_excel_only_with_brackets_in_log_name(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=84, name="drive[01]")
    output = tmp_path / "output"
    options = ConversionOptions(write_mf4=False)
    first = convert_file(blf, output, [DBCBinding(dbc)], options)
    original = {path: path.read_bytes() for path in first.excel_paths}
    second = convert_file(blf, output, [DBCBinding(dbc)], options)
    assert set(first.excel_paths).isdisjoint(second.excel_paths)
    assert first.report_path != second.report_path
    for path, contents in original.items():
        assert path.read_bytes() == contents
    assert len(list(output.glob("*.report.json"))) == 2


def test_decreasing_timestamps_are_rejected_without_outputs(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=7)
    database = cantools.database.load_file(str(dbc))
    payload = database.encode_message("Powertrain", {
        "Speed": 60, "Torque": -50, "BigEndian": 12, "Alive": 0,
    })
    with can.BLFWriter(str(blf)) as writer:
        for time in (1_700_000_001., 1_700_000_003., 1_700_000_002.):
            writer.on_message_received(can.Message(timestamp=time,
                arbitration_id=256, is_extended_id=False, data=payload, channel=0))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="Timestamp"):
        convert_file(blf, output, [DBCBinding(dbc)])
    assert list(output.iterdir()) == []


def test_cancel_during_finalize_cleans_staged_files(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=1000)
    cancel = threading.Event()

    def progress(event):
        if event["phase"] == "mf4":
            cancel.set()

    output = tmp_path / "output"
    with pytest.raises(ConversionCancelled):
        convert_file(blf, output, [DBCBinding(dbc)],
                     progress=progress, cancel=cancel)
    assert list(output.iterdir()) == []


def test_cancel_before_start_creates_no_output(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=7)
    cancel = threading.Event()
    cancel.set()
    output = tmp_path / "output"
    with pytest.raises(ConversionCancelled):
        convert_file(blf, output, [DBCBinding(dbc)], cancel=cancel)
    assert not output.exists()


def test_no_matching_dbc_cleans_all_outputs(tmp_path):
    blf, dbc = generate_demo(tmp_path / "input", frames=21)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="Không có tín hiệu"):
        convert_file(blf, output, [DBCBinding(dbc, channel=99)])
    assert list(output.iterdir()) == []
