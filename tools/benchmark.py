"""Measure synthetic throughput; results are not a prediction for a 3 GB log."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time


def peak_rss_mib():
    """Process high-water mark includes generation and all benchmark stages."""
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024 ** 2 if sys.platform == "darwin" else 1024)
    except (ImportError, AttributeError):
        return None

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import can
from blf_converter.blf import BLFReader
from blf_converter.decoder import DBCBinding
from blf_converter.pipeline import ConversionOptions, convert_file
from tools.generate_demo import generate_demo


def timed(call):
    start = time.perf_counter()
    result = call()
    return result, time.perf_counter() - start


def read_python_can(path):
    with can.BLFReader(str(path)) as reader:
        return sum(1 for _ in reader)


def read_streaming(path):
    with BLFReader(path) as reader:
        return sum(len(batch.timestamps) for batch in reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("benchmark-output"))
    parser.add_argument("--frames", type=int, default=100_000)
    parser.add_argument("--excel-frames", type=int, default=10_000)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.frames < 1 or args.excel_frames < 1:
        parser.error("Frame counts must be positive")
    args.directory.mkdir(parents=True, exist_ok=True)
    full_blf, dbc = generate_demo(args.directory / "input", args.frames, name="full")
    excel_blf, _ = generate_demo(args.directory / "input", min(args.excel_frames, args.frames), name="excel")
    baseline_count, baseline_time = timed(lambda: read_python_can(full_blf))
    fast_count, fast_time = timed(lambda: read_streaming(full_blf))
    if baseline_count != fast_count:
        raise AssertionError(f"Reader frame mismatch: python-can={baseline_count}, streaming={fast_count}")
    mf4, mf4_time = timed(lambda: convert_file(full_blf, args.directory / "mf4", [DBCBinding(dbc)],
                                              ConversionOptions(write_excel=False)))
    both, both_time = timed(lambda: convert_file(excel_blf, args.directory / "both", [DBCBinding(dbc)]))
    report = {
        "dataset": "Synthetic mixed classic CAN / 64-byte CAN FD; two channels; mux and 64-bit values",
        "caution": "Not a real 3 GB vehicle log. Synthetic BLF compresses strongly; throughput depends on CPU, SSD, DBC, frame mix, and Excel cell count.",
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
        "process_peak_rss_mib": peak_rss_mib(),
        "peak_rss_note": "Whole-process high-water mark includes fixture generation and every stage; not isolated per-conversion RAM.",
        "frames": args.frames, "input_bytes": full_blf.stat().st_size,
        "readers": {
            "python_can": {"seconds": baseline_time, "frames_per_second": baseline_count / baseline_time},
            "streaming": {"seconds": fast_time, "frames_per_second": fast_count / fast_time},
            "streaming_speedup": baseline_time / fast_time,
        },
        "mf4_only": {"seconds": mf4_time, "decoded_samples": mf4.decoded_samples,
                      "report": str(mf4.report_path)},
        "mf4_and_excel": {"frames": min(args.excel_frames, args.frames), "seconds": both_time,
                           "decoded_samples": both.decoded_samples, "xlsx_parts": len(both.excel_paths),
                           "report": str(both.report_path)},
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    destination = args.json or args.directory / "benchmark.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
