from datetime import datetime, timezone
import os
import struct
import time
import zlib

import can
import numpy as np
import pytest

from blf_converter.blf import BLFCancelled, BLFParseError, BLFReader


FILE = struct.Struct("<4sLBBBBBBBBQQLL8H8H")
BASE = struct.Struct("<4sHHLL")
FD64 = struct.Struct("<BBBBLLLLLLLHBBL")


def obj(kind, payload, ticks=123456789, flags=2, version=1):
    extension = (struct.pack("<LHHQ", flags, 0, 0, ticks) if version == 1 else
                 struct.pack("<LBxHQ8x", flags, 0, 0, ticks))
    header_size = BASE.size + len(extension)
    raw = BASE.pack(b"LOBJ", header_size, version, header_size + len(payload), kind) + extension + payload
    return raw + bytes(len(raw) % 4)


def write_file(path, parts, compress=True):
    containers = []
    for part in parts:
        data = zlib.compress(part) if compress else part
        body = struct.pack("<H6xL4x", 2 if compress else 0, len(part)) + data
        raw = BASE.pack(b"LOBJ", 16, 1, 16 + len(body), 10) + body
        containers.append(raw + bytes(len(raw) % 4))
    body = b"".join(containers)
    st = (2026, 10, 2, 20, 12, 30, 15, 123)
    header = FILE.pack(b"LOGG", 144, 5, 0, 0, 0, 2, 6, 8, 1,
                       144 + len(body), 0, 0, 0, *st, *st)
    path.write_bytes(header.ljust(144, b"\x00") + body)
    return path


def test_reader_matches_python_can_and_batches(tmp_path):
    path = tmp_path / "reference.blf"
    with can.BLFWriter(path, max_container_size=173) as writer:
        for i in range(500):
            fd = i % 3 == 0
            writer(can.Message(timestamp=1700000000 + i * 0.01,
                               channel=i % 2, arbitration_id=0x100,
                               is_extended_id=i % 5 == 0, is_fd=fd,
                               data=bytes([i % 256]) * (64 if fd else 8)))
    expected = list(can.BLFReader(path))
    with BLFReader(path, batch_frames=37) as reader:
        groups = list(reader)
        assert reader.bytes_read == path.stat().st_size
        assert reader.stats["frames"] == 500
        assert reader.stats["fd_frames"] == 167
        assert reader.start_time == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    actual = {}
    for group in groups:
        assert len(group.timestamps) <= 37
        key = group.channel, group.arbitration_id, group.is_extended
        actual.setdefault(key, []).extend(zip(group.timestamps, group.lengths, group.data))
    for key, rows in actual.items():
        reference = [m for m in expected if (m.channel + 1, m.arbitration_id, m.is_extended_id) == key]
        assert len(rows) == len(reference)
        for (time, length, data), message in zip(rows, reference):
            assert time == pytest.approx(message.timestamp - 1700000000, abs=3e-7)
            assert length == len(message.data)
            assert bytes(data[:length]) == message.data


@pytest.mark.parametrize("compressed", [False, True])
def test_split_headers_payload_and_padding(tmp_path, compressed):
    payload = struct.pack("<HBBL8s", 2, 0, 3, 0x80000123, b"abc")
    raw = obj(1, payload) + obj(86, payload, ticks=42, flags=1, version=2)
    path = write_file(tmp_path / "split.blf", [raw[i:i+7] for i in range(0, len(raw), 7)], compressed)
    with BLFReader(path) as reader:
        batch, = list(reader)
    assert batch.channel == 2 and batch.arbitration_id == 0x123 and batch.is_extended
    np.testing.assert_allclose(batch.timestamps, [0.123456789, 0.00042], rtol=1e-14)
    assert batch.lengths.tolist() == [3, 3]
    assert bytes(batch.data[0, :3]) == b"abc"


def test_fd64_actual_length_and_remote_skip(tmp_path):
    payload = FD64.pack(3, 15, 64, 0, 0x80001234, 0, 0x1000, 0, 0, 0, 0, 0, 0, 0, 0) + bytes(range(64))
    short = FD64.pack(3, 15, 64, 0, 0x80001234, 0, 0x1000, 0, 0, 0, 0, 0, 0, 0, 0) + bytes(range(12))
    remote = struct.pack("<HBBL8s", 1, 0x80, 8, 0x123, b"")
    path = write_file(tmp_path / "fd64.blf", [obj(101, payload) + obj(101, short) + obj(1, remote)])
    with BLFReader(path) as reader:
        batch, = list(reader)
        assert reader.stats["incomplete_payload_frames"] == 1
        assert reader.stats["remote_frames"] == 1
    assert batch.channel == 3 and batch.arbitration_id == 0x1234
    assert batch.lengths.tolist() == [64, 12]
    assert bytes(batch.data[0]) == bytes(range(64))
    assert bytes(batch.data[1, :12]) == bytes(range(12))


@pytest.mark.parametrize("missing", [1, 4, 16, 40])
def test_truncated_file_rejected(tmp_path, missing):
    raw = obj(1, struct.pack("<HBBL8s", 1, 0, 8, 0x123, b"12345678"))
    path = write_file(tmp_path / "truncated.blf", [raw])
    path.write_bytes(path.read_bytes()[:-missing])
    with pytest.raises(BLFParseError):
        with BLFReader(path) as reader:
            list(reader)


def test_incomplete_object_not_silently_accepted(tmp_path):
    raw = obj(1, struct.pack("<HBBL8s", 1, 0, 8, 0x123, b"12345678"))
    path = write_file(tmp_path / "tail.blf", [raw[:-1]])
    with pytest.raises(BLFParseError, match="kết thúc"):
        with BLFReader(path) as reader:
            list(reader)


def test_invalid_timestamp_flags_rejected(tmp_path):
    raw = obj(1, struct.pack("<HBBL8s", 1, 0, 8, 0x123, b"12345678"), flags=0)
    path = write_file(tmp_path / "timestamp.blf", [raw])
    with pytest.raises(BLFParseError, match="timestamp"):
        with BLFReader(path) as reader:
            list(reader)


def test_corrupt_compression_rejected(tmp_path):
    raw = obj(1, struct.pack("<HBBL8s", 1, 0, 8, 0x123, b"12345678"))
    path = write_file(tmp_path / "corrupt.blf", [raw])
    content = bytearray(path.read_bytes())
    content[180] ^= 127
    path.write_bytes(content)
    with pytest.raises(BLFParseError):
        with BLFReader(path) as reader:
            list(reader)


def test_unknown_compression_rejected(tmp_path):
    path = write_file(tmp_path / "compression.blf", [b""])
    content = bytearray(path.read_bytes())
    struct.pack_into("<H", content, 160, 7)
    path.write_bytes(content)
    with pytest.raises(BLFParseError, match="kiểu nén"):
        with BLFReader(path) as reader:
            list(reader)


def test_cancel_checked_between_unsupported_containers(tmp_path):
    class CancelOnSecondCheck:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 2

    path = write_file(tmp_path / "cancel.blf", [obj(96, b""), obj(96, b"")])
    with pytest.raises(BLFCancelled):
        with BLFReader(path, cancel=CancelOnSecondCheck()) as reader:
            list(reader)
    assert reader.stats["unsupported_objects"] == 1


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX timezone simulation requires tzset")
def test_utc_assumption_is_independent_of_machine_timezone(tmp_path):
    previous_timezone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "ICT-7"
        time.tzset()
        raw = obj(1, struct.pack("<HBBL8s", 1, 0, 8, 0x123, b"12345678"))
        path = write_file(tmp_path / "localtime.blf", [raw])
        with BLFReader(path) as reader:
            batch, = list(reader)
            assert reader.start_time.tzinfo == timezone.utc
            assert reader.start_time.astimezone(timezone.utc) == datetime(
                2026, 10, 20, 12, 30, 15, 123000, tzinfo=timezone.utc)
            assert batch.timestamps[0] == pytest.approx(0.123456789, abs=1e-15)
    finally:
        if previous_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_timezone
        time.tzset()
