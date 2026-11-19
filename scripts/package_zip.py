from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ARCHIVE = DIST / "DocuFlow.zip"
INCLUDE = [
    "app", "public", "scripts/package_zip.py", "requirements.txt", "README.md",
    ".env.example", ".gitignore", "Dockerfile", "start.sh", "start.bat",
]
EXCLUDE_PARTS = {"__pycache__", ".venv", ".git", "outputs", "work", "dist"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".mp4", ".mp3", ".m4a", ".log", ".zip"}


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    with ZipFile(ARCHIVE, "w", ZIP_DEFLATED, compresslevel=7) as archive:
        count = 0
        for item in INCLUDE:
            target = ROOT / item
            candidates = [target] if target.is_file() else sorted(target.rglob("*"))
            for path in candidates:
                if not path.is_file():
                    continue
                relative = path.relative_to(ROOT)
                if any(part in EXCLUDE_PARTS for part in relative.parts):
                    continue
                if path.suffix.lower() in EXCLUDE_SUFFIXES or path.name in {".DS_Store"}:
                    continue
                archive.write(path, relative.as_posix())
                count += 1
    print(f"Created {ARCHIVE} ({ARCHIVE.stat().st_size:,} bytes; {count} files)")


if __name__ == "__main__":
    main()
