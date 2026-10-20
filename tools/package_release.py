"""Create the source/Windows-launcher distribution without bundling a venv."""
from pathlib import Path
import hashlib
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    files = [root / name for name in (
        "README_VI.md", "BENCHMARK.md", "Run_Windows.bat", "Build_Windows.bat",
        "desktop_entry.py", "pyproject.toml", "requirements.txt",
    )]
    for folder, pattern in (("blf_converter", "*.py"), ("tests", "*.py"),
                            ("tools", "*.py"), ("examples", "*"), ("verification", "*")):
        files.extend(p for p in sorted((root / folder).glob(pattern)) if p.is_file())
    destination = output / "BLFConverter_Windows.zip"
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            data = path.read_bytes()
            if path.suffix == ".bat":
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            archive.writestr("BLFConverter/" + path.relative_to(root).as_posix(), data)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert "BLFConverter/Run_Windows.bat" in archive.namelist()
        assert "BLFConverter/blf_converter/blf.py" in archive.namelist()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    print(f"{destination}\n{destination.stat().st_size:,} bytes; {len(files)} files\nSHA256 {digest}")


if __name__ == "__main__":
    main()
