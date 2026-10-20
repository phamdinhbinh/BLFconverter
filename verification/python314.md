# Python 3.14 compatibility

Version 1.0.1 uses `asammdf==8.7.2`, `numpy==2.5.4` and `cantools==45.1.0`.

- CPython 3.14.8 / Linux x64: **55 tests passed**, `pip check` passed; demo BLF converted to MDF 4.10 and XLSX.
- CPython 3.12.14 / Linux x64 with the same runtime pins: **55 tests passed**.
- All 21 runtime dependency wheels resolved and downloaded for **Windows x64 / CPython 3.14**, using `--only-binary=:all:`. Wheel filenames and SHA-256 hashes are in `windows-python314-wheels.json`.
- The Windows launcher uses existing CPython 3.12–3.14 and a project-local environment. It does not install Python or require PowerShell script activation.
- `.github/workflows/windows.yml` checks the launcher, full test suite, real Tk GUI construction, and demo conversion on Windows / Python 3.14. Its run status is available in GitHub Actions.

The earlier pin, `asammdf==8.8.27`, requires `zstd`; no compatible Windows CPython 3.14 wheel was available when checked. The 8.7.2 pin avoids that dependency while retaining the MDF 4.10 writer API exercised by the tests. No library shim or local C/C++ compilation is used.

Windows wheel resolution command:

```text
python -m pip download -r requirements.txt --dest wheels --only-binary=:all: --platform win_amd64 --python-version 3.14 --implementation cp --abi cp314 --abi abi3 --abi none
```

Wheel availability and Linux checks alone do not verify a particular company's package policy, local Python installation or MDA 8.6. See the Windows Actions result for the native Windows checks.
