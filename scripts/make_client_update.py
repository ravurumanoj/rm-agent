"""Make apply_client_update.py: one self-contained script that brings the client laptop's rm-agent to this version.

Run on this laptop after every change (from any folder):
    python scripts/make_client_update.py

Then copy the generated apply_client_update.py (project root) to the client laptop's project root and run it there
(see its docstring). It carries every project file, so all the latest changes go across without keeping a list:
each file replaces the client's copy completely, and the files in DELETED_FILES are deleted on the client.
.env* files, app/config.py and the .claude/ folder are never shipped, written or deleted.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_NAME = "apply_client_update.py"

# Never shipped, written or deleted (any .env* file too); the generated script enforces this as well.
PROTECTED_FILES = {"app/config.py"}
PROTECTED_DIRS = {".claude"}

# Local, generated or machine-specific folders, skipped wherever they appear.
SKIP_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".idea", "node_modules", "logs", "data",
}
SKIP_SUFFIXES = (".pyc", ".pyo", ".log")
# Kept on this laptop only (see .gitignore), or managed by hand on the client. This builder stays here as well,
# so it cannot be run on the client by mistake.
LOCAL_ONLY_DIRS = {"docs", "tests"}
LOCAL_ONLY_FILES = {
    "app/services/entitlements.py",
    "app/services/guardrails.py",
    "pyproject.toml",
    "uv.lock",
    "scripts/make_client_update.py",
    OUTPUT_NAME,
}

# Removed from the project; the generated script deletes them on the client. Add a file here when you delete it.
DELETED_FILES = [
    # Code moved into other modules or no longer used.
    "app/prompts/admin_agent.py",
    "app/routes/llm.py",
    "app/schemas/llm.py",
    "app/services/llm.py",
    "app/services/llm_catalog.py",
    # Old update/replica tools (replaced by this script) and unused helper scripts.
    "replicate_rm_agent.py",
    "update_rm_agent.py",
    "scripts/build_replica.py",
    "scripts/build_update.py",
    "scripts/create_project_structure.ps1",
    "scripts/unique_event_socket_dropin.py",
    "scripts/webhook_sse_tap.py",
]


def _protected(rel: str) -> bool:
    parts = Path(rel).parts
    return parts[-1].startswith(".env") or rel in PROTECTED_FILES or parts[0] in PROTECTED_DIRS


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".venv") or name.endswith(".egg-info")


def _shipped(rel: str) -> bool:
    return not (
        _protected(rel)
        or Path(rel).parts[0] in LOCAL_ONLY_DIRS
        or rel in LOCAL_ONLY_FILES
        or rel.endswith(SKIP_SUFFIXES)
    )


def collect_files() -> list[str]:
    """Project-relative paths (posix) of every file the client should have."""
    files: list[str] = []
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(name for name in dirs if not _skip_dir(name))
        for name in sorted(names):
            rel = (Path(folder) / name).relative_to(ROOT).as_posix()
            if _shipped(rel):
                files.append(rel)
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


APPLY_CODE = r'''

def _protected(rel: str) -> bool:
    parts = Path(rel).parts
    return parts[-1].startswith(".env") or rel in PROTECTED_FILES or parts[0] in PROTECTED_DIRS


def _manifest() -> dict[str, bytes]:
    files = {rel: text.encode("utf-8") for rel, text in TEXT_FILES.items()}
    files.update({rel: base64.b64decode(data) for rel, data in BINARY_FILES.items()})
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Bring this rm-agent project to the embedded version.")
    parser.add_argument("--target", default=str(Path(__file__).resolve().parent), help="Project root to update (default: this script's folder).")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be created, updated or deleted.")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    files = _manifest()
    print(f"Target: {target}")
    print(f"Version of {GENERATED_AT}: {len(files)} files to write, {len(DELETED_FILES)} to delete")
    if not (target / "app").is_dir():
        print("Target does not look like the rm-agent project root (no app/ folder). Nothing was changed.")
        return 1

    created = updated = unchanged = 0
    for rel, data in sorted(files.items()):
        if _protected(rel):
            print(f"  skip   {rel} (protected)")
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

    deleted = already_gone = 0
    for rel in DELETED_FILES:
        if _protected(rel):
            print(f"  skip   {rel} (protected)")
            continue
        path = target / rel
        if not path.is_file():
            already_gone += 1
            continue
        print(f"  delete {rel}")
        if not args.dry_run:
            path.unlink()
        deleted += 1

    verb = "Would change" if args.dry_run else "Changed"
    print(f"{verb}: {created} created, {updated} updated, {deleted} deleted "
          f"({unchanged} already up to date, {already_gone} already deleted).")
    print("Not touched:", ", ".join((".env*",) + PROTECTED_FILES + tuple(f"{name}/" for name in PROTECTED_DIRS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def build() -> Path:
    files = collect_files()
    still_here = [rel for rel in DELETED_FILES if (ROOT / rel).exists()]
    if still_here:
        raise SystemExit(f"Files listed in DELETED_FILES still exist here; delete them or take them off the list: {still_here}")

    text_entries: list[str] = []
    binary_entries: list[str] = []
    for rel in files:
        data = (ROOT / rel).read_bytes()
        text = _as_text(data)
        if text is None:
            binary_entries.append(f"    {rel!r}: {base64.b64encode(data).decode('ascii')!r},")
        else:
            text_entries.append(f"    {rel!r}: {_triple_quoted(text)},")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f'''#!/usr/bin/env python3
"""Bring this rm-agent project to the version of {generated_at} ({len(files)} files to write, {len(DELETED_FILES)} to delete).

Made on the development laptop by scripts/make_client_update.py. Put this file in the project root (the folder that
holds app/ and .env), then run:
    python {OUTPUT_NAME} --dry-run        # only list what would change
    python {OUTPUT_NAME}                  # replace the files below and delete the removed ones
    python {OUTPUT_NAME} --target PATH    # update another project root

Each embedded file replaces the existing one completely; the files in DELETED_FILES are deleted.
.env* files, app/config.py and the .claude/ folder are never created, changed or deleted.
Python 3.9+, standard library only.
"""

import argparse
import base64
import sys
from pathlib import Path

GENERATED_AT = {generated_at!r}
PROTECTED_FILES = {tuple(sorted(PROTECTED_FILES))!r}
PROTECTED_DIRS = {tuple(sorted(PROTECTED_DIRS))!r}
DELETED_FILES = {DELETED_FILES!r}

TEXT_FILES = {{
'''
    content = (
        header
        + "\n".join(text_entries)
        + "\n}\n\nBINARY_FILES = {\n"
        + "\n".join(binary_entries)
        + ("\n" if binary_entries else "")
        + "}\n"
        + APPLY_CODE
    )
    output = ROOT / OUTPUT_NAME
    output.write_text(content, encoding="utf-8", newline="\n")

    # The generated script must reproduce every file byte for byte and carry the same delete list.
    namespace: dict = {"__name__": "apply_check"}
    exec(compile(content, str(output), "exec"), namespace)
    restored = namespace["_manifest"]()
    mismatched = [rel for rel in files if restored.get(rel) != (ROOT / rel).read_bytes()]
    if mismatched or set(restored) != set(files):
        raise SystemExit(f"Round-trip check failed for: {mismatched or sorted(set(restored) ^ set(files))}")
    if namespace["DELETED_FILES"] != DELETED_FILES:
        raise SystemExit("Round-trip check failed for DELETED_FILES")

    print(f"Wrote {output}")
    print(f"Version of {generated_at}: {len(files)} files to write ({len(text_entries)} text, {len(binary_entries)} binary), "
          f"{len(DELETED_FILES)} to delete; {output.stat().st_size // 1024} KB. Round-trip check passed.")
    return output


if __name__ == "__main__":
    build()
