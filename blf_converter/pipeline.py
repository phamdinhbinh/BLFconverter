"""Single-pass conversion with bounded batches and transactional output files."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import os
import tempfile
import threading
import time
from typing import Callable
from types import SimpleNamespace

import numpy as np
from asammdf import MDF, Signal

from .blf import BLFReader
from .decoder import DBCBinding, DatabaseRegistry
from .excel import StreamingExcelWriter


class ConversionCancelled(Exception):
    """A requested cancellation; partial files are never published."""


@dataclass(frozen=True)
class ConversionOptions:
    chunk_mb: int = 32
    write_mf4: bool = True
    write_excel: bool = True
    compression: int = 0
    excel_max_cells: int = 8_000_000

    def validate(self):
        if not self.write_mf4 and not self.write_excel:
            raise ValueError("Chọn ít nhất một định dạng MF4 hoặc Excel.")
        if not 1 <= self.chunk_mb <= 512:
            raise ValueError("Kích thước khối phải trong khoảng 1–512 MB.")
        if self.compression not in (0, 1):
            raise ValueError("Chỉ hỗ trợ MDF không nén (0) hoặc DEFLATE (1).")
        if self.excel_max_cells < 100:
            raise ValueError("excel_max_cells phải >= 100.")


@dataclass
class ConversionResult:
    input_path: Path
    mf4_path: Path | None
    excel_paths: list[Path]
    report_path: Path
    elapsed_seconds: float
    frames: int
    decoded_samples: int
    warnings: list[str] = field(default_factory=list)


def _reserve_stem(output_dir: Path, requested: str) -> tuple[str, Path]:
    """Reserve a fresh name; don't overwrite results from another conversion."""
    def exists(stem):
        prefix = stem.casefold()
        return any(
            file.name.casefold().startswith(prefix + ".") or
            (file.name.casefold().startswith(prefix + "_") and file.suffix.casefold() == ".xlsx")
            for file in output_dir.iterdir()
        )

    for index in range(100_000):
        stem = requested if index == 0 else f"{requested}_{index:03d}"
        if exists(stem):
            continue
        lock = output_dir / f".{stem}.conversion.lock"
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError:
            continue
        # Recheck after taking the reservation, in case a previous run finished.
        if exists(stem):
            lock.unlink(missing_ok=True)
            continue
        return stem, lock
    raise FileExistsError("Không thể tạo tên file đầu ra mới.")


def convert_file(input_path: Path | str, output_dir: Path | str,
                 bindings: list[DBCBinding], options: ConversionOptions | None = None,
                 progress: Callable[[dict], None] | None = None,
                 cancel: threading.Event | None = None) -> ConversionResult:
    options = options or ConversionOptions()
    options.validate()
    started = time.perf_counter()
    source = Path(input_path).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".blf":
        raise ValueError(f"File BLF không hợp lệ: {source}")
    cancel = cancel or threading.Event()

    def check_cancel():
        if cancel.is_set():
            raise ConversionCancelled("Đã hủy; các file đang ghi đã được dọn dẹp.")

    last_update = [0.0]

    def emit(phase, fraction, message, force=False):
        if progress and (force or time.perf_counter() - last_update[0] >= 0.15):
            last_update[0] = time.perf_counter()
            progress({"phase": phase, "progress": min(1.0, max(0.0, fraction)),
                      "message": message})

    check_cancel()
    emit("dbc", 0.0, "Đang nạp và biên dịch DBC…", True)
    registry = DatabaseRegistry(bindings)
    target.mkdir(parents=True, exist_ok=True)
    stem, lock_path = _reserve_stem(target, source.stem)
    published: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=f".{stem}_work_", dir=target) as temp:
            work_dir = Path(temp)
            mdf = None
            excel = None
            timings = Counter()
            sample_count = 0
            unknown = Counter()
            groups: dict[tuple, int] = {}
            last_time: dict[tuple, float] = {}
            output_files: list[Path] = []
            warnings: list[str] = []
            try:
                # 96 bytes is a conservative raw frame slot estimate. Decoded
                # arrays and workbook buffers are additional bounded overhead.
                with BLFReader(source, batch_frames=max(1024, options.chunk_mb * 1024**2 // 96), cancel=cancel) as reader:
                    metadata = {
                        "source_blf": str(source), "source_bytes": source.stat().st_size,
                        "start_time": reader.start_time.isoformat(),
                        "start_time_basis": "BLF header interpreted as UTC, consistent with python-can 4.6.1. Confirm absolute clock with the logger configuration; relative sample times are unchanged.",
                        "time_axis": "Seconds relative to BLF header start time; original sample times; no resampling",
                        "dbc": [{"path": str(Path(b.path).resolve()), "channel": b.channel} for b in bindings],
                        "numeric_values": "Physical DBC values; enumerations kept numeric. Unscaled integers remain exact; nonidentity scale/offset uses float64 and may round large 64-bit values. Integers >15 decimal digits stored as text in XLSX.",
                    }
                    if options.write_mf4:
                        mdf = MDF(version="4.10", temporary_folder=str(work_dir))
                        mdf.header.start_time = reader.start_time
                    if options.write_excel:
                        excel = StreamingExcelWriter(work_dir, stem, metadata=metadata,
                                                     max_cells=options.excel_max_cells, cancel=cancel)
                    iterator = iter(reader)
                    while True:
                        check_cancel()
                        step = time.perf_counter()
                        try:
                            batch = next(iterator)
                        except StopIteration:
                            timings["read_seconds"] += time.perf_counter() - step
                            break
                        timings["read_seconds"] += time.perf_counter() - step
                        check_cancel()
                        step = time.perf_counter()
                        decoded = registry.decode(batch.channel, batch.arbitration_id,
                                                  batch.is_extended, batch.timestamps,
                                                  batch.data, batch.lengths)
                        if not decoded and registry.resolve(batch.channel, batch.arbitration_id, batch.is_extended) is None:
                            unknown[(batch.channel, batch.arbitration_id, batch.is_extended)] += len(batch.timestamps)
                        timings["decode_seconds"] += time.perf_counter() - step
                        for group in decoded:
                            check_cancel()
                            key = (group.channel, group.frame_id, group.is_extended_id,
                                   tuple(s.name for s in group.signals))
                            if np.any(np.diff(group.timestamps) < 0) or (
                                key in last_time and group.timestamps[0] < last_time[key]
                            ):
                                raise ValueError(
                                    f"Timestamp đi lùi trong CAN{group.channel}, ID 0x{group.frame_id:X}. "
                                    "Cần sắp xếp log theo thời gian trước khi chuyển để MDA đọc đúng."
                                )
                            last_time[key] = float(group.timestamps[-1])
                            sample_count += len(group.timestamps) * len(group.signals)
                            if mdf is not None:
                                step = time.perf_counter()
                                if key not in groups:
                                    signals = [Signal(samples=s.samples, timestamps=group.timestamps,
                                                      name=s.name, unit=s.unit, comment=s.comment)
                                               for s in group.signals]
                                    groups[key] = mdf.append(
                                        signals, common_timebase=True,
                                        acq_name=f"CAN{group.channel}.{group.message_name}",
                                        comment=f"DBC decoded CAN ID 0x{group.frame_id:X}; original timestamps",
                                    )
                                else:
                                    mdf.extend(groups[key], [(group.timestamps, None)] +
                                               [(s.samples, None) for s in group.signals])
                                timings["mf4_stage_seconds"] += time.perf_counter() - step
                            if excel is not None:
                                step = time.perf_counter()
                                excel.append(group)
                                timings["excel_stage_seconds"] += time.perf_counter() - step
                        emit("convert", 0.85 * reader.bytes_read / max(1, reader.file_size),
                             f"Đang xử lý • {registry.decoded_frames:,} frame khớp DBC • {sample_count:,} mẫu tín hiệu")
                    stats = dict(reader.stats)
                check_cancel()
                if sample_count == 0:
                    raise ValueError("Không có tín hiệu được giải mã. Kiểm tra DBC, CAN channel, ID và độ dài CAN FD.")
                if registry.unknown_frames:
                    warnings.append(f"{registry.unknown_frames:,} frame không khớp DBC (xem report).")
                if registry.truncated_frames:
                    warnings.append(f"{registry.truncated_frames:,} frame ngắn hơn độ dài DBC đã bị bỏ qua.")
                if stats.get("invalid_start_time"):
                    warnings.append("Header BLF thiếu thời gian bắt đầu hợp lệ; dùng mốc 1970-01-01, giữ thời gian tương đối.")
                if stats.get("incomplete_payload_frames"):
                    warnings.append(f"{stats['incomplete_payload_frames']:,} frame CAN FD không có đủ payload được khai báo (xem số frame ngắn hơn DBC).")
                warnings.extend(registry.warnings)
                for name in ("remote_frames", "error_frames", "unsupported_objects", "unsupported_can_objects"):
                    if stats.get(name):
                        warnings.append(f"{name}: {stats[name]:,}; không xuất thành tín hiệu.")
                mf4_name = None
                if mdf is not None:
                    emit("mf4", 0.86, "Đang hoàn tất MF4 4.10…", True)
                    check_cancel()
                    step = time.perf_counter()
                    mf4_temp = work_dir / f"{stem}.mf4"
                    class SaveProgress:
                        def __init__(self):
                            self.signals = SimpleNamespace(setValue=SimpleNamespace(emit=self.update))

                        @property
                        def stop(self):
                            return cancel.is_set()

                        def update(self, value):
                            emit("mf4", 0.86 + 0.06 * value / 100,
                                 f"Đang hoàn tất MF4 4.10 • {value}%")

                    mdf.save(mf4_temp, overwrite=False, compression=options.compression,
                             progress=SaveProgress())
                    check_cancel()
                    timings["mf4_finalize_seconds"] += time.perf_counter() - step
                    output_files.append(mf4_temp)
                    mf4_name = mf4_temp.name
                    mdf.close()
                    mdf = None
                check_cancel()
                excel_names = []
                if excel is not None:
                    emit("excel", 0.93, "Đang đóng gói Excel…", True)
                    check_cancel()
                    step = time.perf_counter()
                    excel_files = excel.close()
                    excel = None
                    timings["excel_finalize_seconds"] += time.perf_counter() - step
                    output_files.extend(excel_files)
                    excel_names = [p.name for p in excel_files]
                check_cancel()
                elapsed = time.perf_counter() - started
                report_temp = work_dir / f"{stem}.report.json"
                report = {
                    "tool_version": "1.0.0", "input": str(source), "metadata": metadata,
                    "options": asdict(options), "elapsed_seconds": elapsed,
                    "timings": dict(timings), "reader_stats": stats,
                    "decoded_frames": registry.decoded_frames, "unknown_frames": registry.unknown_frames,
                    "truncated_frames": registry.truncated_frames, "decoded_samples": sample_count,
                    "unknown_ids": [{"channel": k[0], "id_hex": f"0x{k[1]:X}", "extended": k[2], "frames": v}
                                    for k, v in sorted(unknown.items())],
                    "warnings": warnings, "mf4": mf4_name, "excel": excel_names,
                    "compatibility": "MDF 4.10 numeric channels, original timestamps. MDA 8.6 validation requires the target application.",
                    "storage_note": "Only DBC-decoded data exported; retain original BLF for raw frames and unrecognized data.",
                }
                report_temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                output_files.append(report_temp)
                for file in output_files:
                    destination = target / file.name
                    if destination.exists():
                        raise FileExistsError(f"File đầu ra đã tồn tại: {destination}")
                    # Hard links atomically fail if a competing writer created
                    # this name; unlink stage after publishing on same volume.
                    if os.name == "nt":
                        # Windows rename fails rather than overwriting an
                        # existing destination; also works on exFAT drives.
                        os.rename(file, destination)
                    else:
                        os.link(file, destination)
                    published.append(destination)
                    if os.name != "nt":
                        file.unlink()
                result = ConversionResult(
                    source, target / mf4_name if mf4_name else None,
                    [target / name for name in excel_names], target / report_temp.name,
                    elapsed, registry.decoded_frames, sample_count, warnings,
                )
                emit("done", 1.0, f"Hoàn tất trong {elapsed:.2f} giây", True)
                return result
            except BaseException:
                if cancel.is_set():
                    raise ConversionCancelled("Đã hủy; không giữ file đầu ra dở dang.") from None
                raise
            finally:
                if mdf is not None:
                    mdf.close()
                if excel is not None:
                    excel.abort()
    except BaseException:
        for file in published:
            file.unlink(missing_ok=True)
        raise
    finally:
        lock_path.unlink(missing_ok=True)
