"""Generate a reproducible classic CAN / CAN FD fixture without vehicle files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import can
import cantools


DEMO_DBC = Path(__file__).resolve().parents[1] / "examples" / "demo.dbc"


def generate_demo(directory: Path | str, frames: int = 100_000,
                  *, name: str = "demo", include_unknown: bool = True) -> tuple[Path, Path]:
    """Write a BLF and DBC; BLF bus numbers are CAN1 and CAN2.

    Frame spacing is 1 ms. Counter values intentionally exceed Excel's numeric
    precision so the exporter can be checked for exact text preservation.
    """
    if frames < 1:
        raise ValueError("frames must be positive")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    dbc_path = directory / f"{name}.dbc"
    if dbc_path.resolve() != DEMO_DBC.resolve():
        shutil.copyfile(DEMO_DBC, dbc_path)
    database = cantools.database.load_file(str(dbc_path))
    templates = []
    for n in range(64):
        templates.extend([
            (256, False, False, 0, database.encode_message("Powertrain", {
                "Speed": 60 + n / 4, "Torque": -50 + n / 2,
                "BigEndian": 12 + n / 4, "Alive": n})),
            (256, False, False, 1, database.encode_message("Powertrain", {
                "Speed": 90 + n / 4, "Torque": n / 2,
                "BigEndian": 24 + n / 4, "Alive": n + 64})),
            (256, True, False, 0, database.encode_message("ExtendedStatus", {
                "BigCounter": 2 ** 63 + n})),
            (1024, False, True, 0, database.encode_message("BatteryFD", {
                "PackVoltage": 350 + n / 4, "CellTemp": -10 + n / 2,
                "FloatFD": n / 8 - 3, "EnergyAtTail": 2 ** 63 + 1000 + n})),
            (512, False, False, 0, database.encode_message("Multiplexed", {
                "Selector": 1, "Common": n, "BranchA": -15 + n / 2})),
            (512, False, False, 0, database.encode_message("Multiplexed", {
                "Selector": 2, "Common": n, "BranchB": 50 + n / 4})),
        ])
        if include_unknown:
            templates.append((1792, False, False, 0, bytes([n]) * 8))
    blf_path = directory / f"{name}.blf"
    start = 1_700_000_000.0
    with can.BLFWriter(str(blf_path), compression_level=1) as writer:
        for index in range(frames):
            frame_id, extended, fd, channel, payload = templates[index % len(templates)]
            writer.on_message_received(can.Message(
                timestamp=start + index * .001,
                arbitration_id=frame_id,
                is_extended_id=extended,
                is_fd=fd,
                bitrate_switch=fd,
                channel=channel,
                data=payload,
            ))
    return blf_path, dbc_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("demo"))
    parser.add_argument("--frames", type=int, default=100_000)
    parser.add_argument("--name", default="demo")
    args = parser.parse_args()
    blf, dbc = generate_demo(args.directory, args.frames, name=args.name)
    print(json.dumps({"blf": str(blf), "dbc": str(dbc), "frames": args.frames,
                      "blf_bytes": blf.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
