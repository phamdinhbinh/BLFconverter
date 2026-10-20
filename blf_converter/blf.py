"""Bounded BLF container reader; no per-frame CAN Message allocations.

Only measurement CAN/CAN FD frames are emitted. Timestamps stay relative to the
BLF start. SYSTEMTIME has no timezone; UTC is assumed, matching python-can 4.6.1.
The logger's calendar-time convention must be checked for absolute-time use.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import struct
import zlib

import numpy as np


class BLFParseError(ValueError):
    pass


class BLFCancelled(Exception):
    pass


@dataclass(frozen=True)
class FrameBatch:
    channel: int
    arbitration_id: int
    is_extended: bool
    timestamps: np.ndarray
    data: np.ndarray
    lengths: np.ndarray


_FILE = struct.Struct("<4sLBBBBBBBBQQLL8H8H")
_BASE = struct.Struct("<4sHHLL")
_V1 = struct.Struct("<LHHQ")
_V2 = struct.Struct("<LBxHQ8x")
_CONTAINER = struct.Struct("<H6xL4x")
_CAN = struct.Struct("<HBBL8s")
_FD = struct.Struct("<HBBLLBBB5x64s")
_FD64 = struct.Struct("<BBBBLLLLLLLHBBL")
_RECORD = struct.Struct("<dB64s")
_DTYPE = np.dtype([("timestamp", "<f8"), ("length", "u1"), ("data", "u1", (64,))])
_MAX_OBJECT = 256 * 1024 * 1024


class BLFReader:
    def __init__(self, path: str | Path, batch_frames: int = 131072, cancel=None):
        if batch_frames < 1:
            raise ValueError("batch_frames must be positive")
        self.path = Path(path)
        self.file_size = self.path.stat().st_size
        self.batch_frames = batch_frames
        self._cancel = cancel
        self.stats = Counter()
        self.bytes_read = 0
        self._stream = self.path.open("rb", buffering=1024 * 1024)
        self._iterated = False
        try:
            header = _FILE.unpack(self._read_exact(_FILE.size))
            if header[0] != b"LOGG":
                raise BLFParseError("Không phải file BLF (thiếu LOGG).")
            if not _FILE.size <= header[1] <= min(self.file_size, 1024 * 1024):
                raise BLFParseError("Kích thước header BLF không hợp lệ.")
            self._read_exact(header[1] - _FILE.size)
            if header[10] and header[10] != self.file_size:
                raise BLFParseError("Dung lượng BLF khác header; file có thể bị cắt hoặc chưa đóng.")
            st = header[14:22]
            try:
                self.start_time = datetime(st[0], st[1], st[3], st[4], st[5], st[6], st[7] * 1000,
                                           tzinfo=timezone.utc)
            except ValueError:
                self.start_time = datetime(1970, 1, 1, tzinfo=timezone.utc)
                self.stats["invalid_start_time"] += 1
        except BaseException:
            self.close()
            raise

    def _read_exact(self, length):
        data = self._stream.read(length)
        self.bytes_read = self._stream.tell()
        if len(data) != length:
            raise BLFParseError("BLF bị cắt: thiếu dữ liệu trong object/container.")
        return data

    def close(self):
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def _batches(groups):
        for (channel, can_id, extended), raw in groups.items():
            records = np.frombuffer(raw, dtype=_DTYPE)
            yield FrameBatch(channel, can_id, extended, records["timestamp"],
                             records["data"], records["length"])

    def __iter__(self):
        if self._iterated:
            raise RuntimeError("BLFReader chỉ có thể đọc một lần.")
        self._iterated = True
        groups = {}
        count = 0
        tail = b""
        unpack_base = _BASE.unpack_from
        unpack_v1, unpack_v2 = _V1.unpack_from, _V2.unpack_from
        unpack_can, unpack_fd, unpack_fd64 = _CAN.unpack_from, _FD.unpack_from, _FD64.unpack_from
        pack = _RECORD.pack
        stats = self.stats
        while self.bytes_read < self.file_size:
            if self._cancel is not None and self._cancel.is_set():
                raise BLFCancelled("Đã hủy đọc BLF.")
            outer = self._read_exact(_BASE.size)
            signature, header_size, _, object_size, object_type = _BASE.unpack(outer)
            if signature != b"LOBJ" or not _BASE.size <= header_size <= object_size <= _MAX_OBJECT:
                raise BLFParseError("Header object BLF không hợp lệ.")
            body = self._read_exact(object_size - _BASE.size)
            self._read_exact(object_size % 4)
            if object_type != 10:
                stats["unsupported_objects"] += 1
                continue
            container_offset = header_size - _BASE.size
            if container_offset + _CONTAINER.size > len(body):
                raise BLFParseError("Header container BLF bị cắt.")
            method, expected_size = _CONTAINER.unpack_from(body, container_offset)
            if expected_size > _MAX_OBJECT:
                raise BLFParseError("Container BLF vượt giới hạn an toàn 256 MiB.")
            compressed = body[container_offset + _CONTAINER.size:]
            if method == 0:
                decoded = compressed
            elif method == 2:
                try:
                    decompressor = zlib.decompressobj()
                    decoded = decompressor.decompress(compressed, expected_size + 1)
                    if not decompressor.eof or decompressor.unconsumed_tail or decompressor.unused_data:
                        raise BLFParseError("Container zlib BLF bị cắt hoặc có dữ liệu thừa.")
                except zlib.error as exc:
                    raise BLFParseError(f"Container zlib BLF bị lỗi: {exc}") from exc
            else:
                raise BLFParseError(f"Không hỗ trợ kiểu nén BLF {method}.")
            if len(decoded) != expected_size:
                raise BLFParseError("Dung lượng giải nén container BLF không khớp header.")
            data = tail + decoded if tail else decoded
            position, limit = 0, len(data)
            while position < limit:
                # Object padding is at most three zero bytes; it can straddle containers.
                original = position
                while position < limit and data[position] == 0 and position - original < 4:
                    position += 1
                if position == limit or limit - position < _BASE.size:
                    position = original
                    break
                signature, size_header, version, size_object, kind = unpack_base(data, position)
                if signature != b"LOBJ" or not _BASE.size <= size_header <= size_object <= _MAX_OBJECT:
                    raise BLFParseError("Object trong container BLF không hợp lệ.")
                end = position + size_object
                if end > limit:
                    position = original
                    break
                stats["objects"] += 1
                if kind not in (1, 86, 100, 101):
                    stats["error_frames" if kind in (2, 73) else "unsupported_objects"] += 1
                    position = end
                    continue
                if version == 1 and size_header >= 32:
                    timestamp_flags, _, _, ticks = unpack_v1(data, position + 16)
                elif version == 2 and size_header >= 40:
                    timestamp_flags, _, _, ticks = unpack_v2(data, position + 16)
                else:
                    raise BLFParseError(f"Header CAN version {version} không được hỗ trợ.")
                time_type = timestamp_flags & 3
                if time_type not in (1, 2):
                    raise BLFParseError(f"Đơn vị timestamp BLF không hợp lệ: {timestamp_flags}.")
                timestamp = ticks * (1e-5 if time_type == 1 else 1e-9)
                payload_start = position + size_header
                payload_size = end - payload_start
                if kind in (1, 86):
                    if payload_size < _CAN.size:
                        raise BLFParseError("CAN object bị cắt.")
                    channel, flags, length, can_id, payload = unpack_can(data, payload_start)
                    if length > 8:
                        raise BLFParseError("Độ dài CAN cổ điển vượt 8 byte.")
                    remote = flags & 0x80
                elif kind == 100:
                    if payload_size < _FD.size:
                        raise BLFParseError("CAN FD object bị cắt.")
                    channel, flags, dlc, can_id, _, _, fd_flags, length, payload = unpack_fd(data, payload_start)
                    if length > 64 or dlc > 15:
                        raise BLFParseError("Độ dài CAN FD không hợp lệ.")
                    remote = flags & 0x80
                    stats["fd_frames"] += 1
                else:
                    if payload_size < _FD64.size:
                        raise BLFParseError("CAN FD64 object bị cắt.")
                    (channel, dlc, length, _, can_id, _, flags, _, _, _, _, _, _,
                     extra_offset, _) = unpack_fd64(data, payload_start)
                    if length > 64 or dlc > 15:
                        raise BLFParseError("Độ dài CAN FD64 không hợp lệ.")
                    available = (extra_offset or size_object) - size_header - _FD64.size
                    if available < 0 or available > payload_size - _FD64.size:
                        raise BLFParseError("Offset CAN FD64 không hợp lệ.")
                    if length > available:
                        # Keep actual byte count; decoder will omit unavailable signals.
                        stats["incomplete_payload_frames"] += 1
                        length = available
                    start = payload_start + _FD64.size
                    payload = data[start:start + length]
                    remote = flags & 0x10
                    stats["fd_frames"] += 1
                stats["total_frames"] += 1
                position = end
                if remote:
                    stats["remote_frames"] += 1
                    continue
                if channel < 1:
                    raise BLFParseError("Kênh CAN trong BLF phải bắt đầu từ 1.")
                extended = bool(can_id & 0x80000000)
                can_id &= 0x1fffffff
                if not extended and can_id > 0x7ff:
                    raise BLFParseError("CAN ID standard vượt 11 bit.")
                key = (channel, can_id, extended)
                record = pack(timestamp, length, payload)
                group = groups.get(key)
                if group is None:
                    groups[key] = bytearray(record)
                else:
                    group.extend(record)
                stats["frames"] += 1
                count += 1
                if count >= self.batch_frames:
                    if self._cancel is not None and self._cancel.is_set():
                        raise BLFCancelled("Đã hủy đọc BLF.")
                    yield from self._batches(groups)
                    groups = {}
                    count = 0
            tail = data[position:]
            if len(tail) > _MAX_OBJECT:
                raise BLFParseError("Object BLF chưa hoàn tất vượt giới hạn bộ nhớ.")
        if tail.strip(b"\x00"):
            raise BLFParseError("BLF kết thúc giữa object; dữ liệu bị cắt.")
        if groups:
            yield from self._batches(groups)
