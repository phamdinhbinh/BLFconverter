# Synthetic performance validation

Command: `.venv/bin/python tools/benchmark.py scratch/benchmark --frames 1000000 --excel-frames 100000 --json scratch/benchmark/benchmark.json`

This is a synthetic, highly compressible mixed classic CAN / CAN FD dataset, not a real 3 GB vehicle log. Results were measured in the Linux cloud workspace with Python 3.12.14 and 3 logical CPUs. Windows performance and MDA 8.6 import require validation on the target computer.

| Measurement | Result |
| --- | ---: |
| Reader frames | 1,000,000 |
| Compressed input bytes | 10,138,580 |
| python-can reader | 3.581 s |
| Streaming reader | 2.352 s |
| Reader speed ratio | 1.52x |
| Full MF4-only conversion, 1,000,000 frames | 2.725 s |
| Decoded MF4 signal samples | 2,714,287 |
| MF4 + Excel conversion, 100,000 frames | 1.199 s |
| Decoded MF4 + Excel samples | 271,431 |
| Whole-process peak RSS | 136.9 MiB |

The peak RSS includes fixture generation and all benchmark stages; it is not isolated conversion RAM. Signal sample count counts each decoded signal value, not CAN frames. Unknown ID 0x700 is deliberately included and reported rather than decoded.

The MF4-only run spent 2.497 s reading, 0.061 s in vectorized DBC decoding, 0.099 s staging MDF data and 0.043 s finalizing MF4. The MF4 + Excel run spent 0.938 s writing and packaging Excel.

Validation includes independent python-can/cantools comparison over 22,000 frames across batches: classic CAN and 64-byte CAN FD, Intel/Motorola and signed/scaled/IEEE signals, multiplexing, standard/extended ID collision, channels 1/2, exact unscaled uint64 values, original timestamps, archive cell counts and workbook splits, cancellation cleanup, safe repeated names, out-of-order time rejection, and optional MDF DEFLATE roundtrip. Nine pipeline tests passed.
