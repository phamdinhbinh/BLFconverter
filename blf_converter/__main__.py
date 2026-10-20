"""Desktop by default; optional CLI for unattended batch conversion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading


def main():
    if len(sys.argv) == 1 or sys.argv[1:] == ["--gui"]:
        from .gui import run
        run()
        return 0
    parser = argparse.ArgumentParser(description="BLF + DBC → MDF 4.10 / Excel; streaming CAN/CAN FD")
    parser.add_argument("inputs", nargs="+", type=Path, help="File .blf")
    parser.add_argument("--dbc", action="append", required=True, metavar="[CANn=]FILE", help="Lặp lại cho nhiều DBC. VD: CAN1=powertrain.dbc")
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--format", choices=["both", "mf4", "excel"], default="both")
    parser.add_argument("--chunk-mb", type=int, default=32)
    parser.add_argument("--compress", action="store_true", help="Nén MF4 DEFLATE; mặc định không nén để ưu tiên tốc độ")
    args = parser.parse_args()
    from .decoder import DBCBinding
    from .pipeline import ConversionCancelled, ConversionOptions, convert_file
    bindings = []
    for value in args.dbc:
        channel = None
        if "=" in value:
            prefix, path = value.split("=", 1)
            if prefix.upper().startswith("CAN") and prefix[3:].isdigit():
                channel, value = int(prefix[3:]), path
        bindings.append(DBCBinding(Path(value), channel))
    options = ConversionOptions(args.chunk_mb, args.format != "excel", args.format != "mf4", int(args.compress))
    cancel = threading.Event()
    import signal
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    failures = 0
    for path in args.inputs:
        try:
            result = convert_file(path, args.output, bindings, options,
                                  progress=lambda event: print(event["message"], file=sys.stderr), cancel=cancel)
            print(json.dumps({"input": str(path), "mf4": str(result.mf4_path) if result.mf4_path else None,
                              "excel": [str(p) for p in result.excel_paths], "report": str(result.report_path),
                              "seconds": result.elapsed_seconds}, ensure_ascii=False))
        except ConversionCancelled as exc:
            print(str(exc), file=sys.stderr)
            return 130
        except Exception as exc:
            failures += 1
            print(f"{path}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
