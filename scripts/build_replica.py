"""Build replicate_rm_agent.py: one self-contained script that recreates this project's files on another machine.

Run from the project root (again after every code change):
    python scripts/build_replica.py

Copy the generated replicate_rm_agent.py to the other machine's project root and run it there
(see its docstring for options). .env, pyproject.toml, uv.lock and docs/ are never included; of tests/, only
the status check (test_health.py with conftest.py) is.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_NAME = "replicate_rm_agent.py"

# Local, generated or machine-specific content that must not be copied.
EXCLUDED_DIRS = {".git", ".pytest_cache", ".ruff_cache", ".mypy_cache", "__pycache__", "logs", "data", "node_modules", ".idea"}
EXCLUDED_DIR_PREFIXES = (".venv",)
EXCLUDED_DIR_SUFFIXES = (".egg-info",)
EXCLUDED_FILES = {".env", "pyproject.toml", "uv.lock", OUTPUT_NAME}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".log")
# Top-level folders left out of the replica.
EXCLUDED_TOP_LEVEL_DIRS = {"docs"}
# Only the status check test for now; conftest.py provides its `client` fixture.
INCLUDED_TEST_FILES = {"tests/__init__.py", "tests/conftest.py", "tests/test_health.py"}


def _excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIRS or name.startswith(EXCLUDED_DIR_PREFIXES) or name.endswith(EXCLUDED_DIR_SUFFIXES)


def collect_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(_excluded_dir(part) for part in rel.parts[:-1]) or not path.is_file():
            continue
        if path.name in EXCLUDED_FILES or path.name.endswith(EXCLUDED_SUFFIXES):
            continue
        if rel.parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
            continue
        if rel.parts[0] == "tests" and rel.as_posix() not in INCLUDED_TEST_FILES:
            continue
        files.append(path)
    return files


def _as_text(data: bytes) -> str | None:
    """File content as text, or None for binary files (embedded as base64 instead)."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _triple_quoted(text: str) -> str:
    """Readable ''' literal that evaluates back to exactly `text`."""
    body = text.replace("\\", "\\\\").replace("\r", "\\r").replace("'''", "''\\'")
    if body.endswith("'"):
        body = body[:-1] + "\\'"
    # The backslash after the opening quotes skips the first newline, so file content starts on its own line.
    return "'''\\\n" + body + "'''"


RESTORE_CODE = r'''

PROTECTED = {".env", "pyproject.toml"}
PRUNE_DIRS = ("app", "scripts")


def _manifest() -> dict[str, bytes]:
    files = {rel: text.encode("utf-8") for rel, text in TEXT_FILES.items()}
    files.update({rel: base64.b64decode(data) for rel, data in BINARY_FILES.items()})
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Recreate the rm-agent project files.")
    parser.add_argument("--target", default=str(Path(__file__).resolve().parent), help="Project root to write into (default: this script's folder).")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be created or updated.")
    parser.add_argument("--prune", action="store_true", help="Delete .py files under app/ and scripts/ that this version does not have.")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    files = _manifest()
    print(f"Target: {target}")
    print(f"Files in this script: {len(files)} (generated {GENERATED_AT})")
    if not target.is_dir():
        print("Target folder does not exist.")
        return 1

    created = updated = unchanged = 0
    for rel, data in sorted(files.items()):
        if Path(rel).name in PROTECTED:
            continue
        path = target / rel
        if path.is_file() and path.read_bytes() == data:
            unchanged += 1
            continue
        action = "update" if path.exists() else "create"
        print(f"  {action:<6} {rel}")
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if action == "create":
            created += 1
        else:
            updated += 1

    stale = sorted(
        path
        for folder in PRUNE_DIRS
        if (target / folder).is_dir()
        for path in (target / folder).rglob("*.py")
        if "__pycache__" not in path.parts and path.relative_to(target).as_posix() not in files
    )
    for path in stale:
        rel = path.relative_to(target).as_posix()
        if args.prune and not args.dry_run:
            path.unlink()
            print(f"  delete {rel}")
        else:
            print(f"  stale  {rel} (not in this version; --prune deletes it)")

    verb = "Would create/update" if args.dry_run else "Created/updated"
    print(f"{verb}: {created} created, {updated} updated, {unchanged} unchanged, {len(stale)} stale.")
    print(".env and pyproject.toml were not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def build() -> Path:
    files = collect_files()
    text_entries: list[str] = []
    binary_entries: list[str] = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        text = _as_text(data)
        if text is None:
            binary_entries.append(f"    {rel!r}: {base64.b64encode(data).decode('ascii')!r},")
        else:
            text_entries.append(f"    {rel!r}: {_triple_quoted(text)},")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f'''#!/usr/bin/env python3
"""Recreate the rm-agent project files ({len(files)} files, generated {generated_at} by scripts/build_replica.py).

Put this file in the project root (the folder that holds app/, .env and pyproject.toml), then run:
    python {OUTPUT_NAME}                  # create/overwrite all files next to this script
    python {OUTPUT_NAME} --dry-run        # only list what would change
    python {OUTPUT_NAME} --prune          # also delete .py files under app/ and scripts/ this version no longer has
    python {OUTPUT_NAME} --target PATH    # write into another project root

.env and pyproject.toml are never created or changed. Python 3.9+, standard library only.
"""

import argparse
import base64
import sys
from pathlib import Path

GENERATED_AT = {generated_at!r}

TEXT_FILES = {{
'''
    content = (
        header
        + "\n".join(text_entries)
        + "\n}\n\nBINARY_FILES = {\n"
        + "\n".join(binary_entries)
        + ("\n" if binary_entries else "")
        + "}\n"
        + RESTORE_CODE
    )
    output = ROOT / OUTPUT_NAME
    output.write_text(content, encoding="utf-8", newline="\n")

    # The generated script must reproduce every file byte for byte.
    namespace: dict = {"__name__": "replica_check"}
    exec(compile(content, str(output), "exec"), namespace)
    restored = namespace["_manifest"]()
    mismatched = [p.relative_to(ROOT).as_posix() for p in files if restored.get(p.relative_to(ROOT).as_posix()) != p.read_bytes()]
    if mismatched:
        raise SystemExit(f"Round-trip check failed for: {mismatched}")

    print(f"Wrote {output} ({len(files)} files: {len(text_entries)} text, {len(binary_entries)} binary; "
          f"{output.stat().st_size // 1024} KB). Round-trip check passed.")
    return output


if __name__ == "__main__":
    build()
