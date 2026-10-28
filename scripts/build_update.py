"""Build update_rm_agent.py: one self-contained script that brings another machine's project up to this version.

Run from the project root after editing UPDATED_FILES and DELETED_FILES:
    python scripts/build_update.py

Copy the generated update_rm_agent.py to the other machine's project root and run it there (see its docstring).
It replaces every file in UPDATED_FILES completely and deletes every file in DELETED_FILES. .env, .env.example,
app/config.py and the .claude/ folder are never shipped, written or deleted.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

from build_replica import _as_text, _triple_quoted

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_NAME = "update_rm_agent.py"

# Never shipped, written or deleted, even if listed below; the generated script enforces this too.
PROTECTED_FILES = {".env", ".env.example", "app/config.py"}
PROTECTED_DIRS = {".claude"}

# Changed or new since the last update; the other machine's copy of each one is replaced completely.
UPDATED_FILES = [
    ".gitignore",
    "PROJECT_GUIDE.md",
    "README.md",
    "app/agents/admin_agent.py",
    "app/agents/base.py",
    "app/agents/final_agent.py",
    "app/agents/orchestrator/agent.py",
    "app/agents/react_agent.py",
    "app/agents/sufficiency.py",
    "app/components/react/models.py",
    "app/components/react/runner.py",
    "app/components/react/tool_executor.py",
    "app/components/resilience.py",
    "app/constants.py",
    "app/graph/nodes/answer_nodes.py",
    "app/graph/nodes/common.py",
    "app/graph/nodes/execution_nodes.py",
    "app/graph/runner.py",
    "app/graph/tasks.py",
    "app/main.py",
    "app/observability/__init__.py",
    "app/observability/conventions.py",
    "app/observability/llm.py",
    "app/observability/setup.py",
    "app/observability/spans.py",
    "app/prompts/agent_catalog.py",
    "app/prompts/common.py",
    "app/prompts/final_agent.py",
    "app/prompts/orchestrator/clarification_followup.py",
    "app/prompts/orchestrator/replan.py",
    "app/prompts/orchestrator/routing.py",
    "app/prompts/react.py",
    "app/prompts/sufficiency.py",
    "app/routes/agent.py",
    "app/routes/webhook.py",
    "app/schemas/agent.py",
    "app/schemas/agent_io.py",
    "app/services/chat_history.py",
    "app/services/llm_core.py",
    "app/services/llm_router.py",
    "app/services/map_reduce.py",
    "app/services/observability.py",
    "app/services/sse_listener.py",
    "app/services/streaming.py",
    "app/services/unique_runtime.py",
    "app/services/unique_webhook.py",
    "scripts/build_update.py",
    "scripts/create_project_structure.ps1",
]

# Removed from the project (moved into other modules or no longer used); the generated script deletes them.
DELETED_FILES = [
    "app/prompts/admin_agent.py",
    "app/routes/llm.py",
    "app/schemas/llm.py",
    "app/services/llm.py",
    "app/services/llm_catalog.py",
]


def _protected(rel: str) -> bool:
    parts = Path(rel).parts
    return rel in PROTECTED_FILES or parts[-1] in PROTECTED_FILES or parts[0] in PROTECTED_DIRS


RESTORE_CODE = r'''

def _protected(rel: str) -> bool:
    parts = Path(rel).parts
    return rel in PROTECTED_FILES or parts[-1] in PROTECTED_FILES or parts[0] in PROTECTED_DIRS


def _manifest() -> dict[str, bytes]:
    files = {rel: text.encode("utf-8") for rel, text in TEXT_FILES.items()}
    files.update({rel: base64.b64decode(data) for rel, data in BINARY_FILES.items()})
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Bring an rm-agent project up to this version.")
    parser.add_argument("--target", default=str(Path(__file__).resolve().parent), help="Project root to update (default: this script's folder).")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be created, updated or deleted.")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    files = _manifest()
    print(f"Target: {target}")
    print(f"This update: {len(files)} files to write, {len(DELETED_FILES)} to delete (generated {GENERATED_AT})")
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
    print("Not touched:", ", ".join(PROTECTED_FILES + tuple(f"{name}/" for name in PROTECTED_DIRS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def build() -> Path:
    listed = [rel for rel in UPDATED_FILES if not _protected(rel)]
    removed = [rel for rel in DELETED_FILES if not _protected(rel)]
    missing = [rel for rel in listed if not (ROOT / rel).is_file()]
    if missing:
        raise SystemExit(f"Listed files not found: {missing}")
    still_here = [rel for rel in removed if (ROOT / rel).exists()]
    if still_here:
        raise SystemExit(f"Files listed for deletion still exist here: {still_here}")

    text_entries: list[str] = []
    binary_entries: list[str] = []
    for rel in listed:
        data = (ROOT / rel).read_bytes()
        text = _as_text(data)
        if text is None:
            binary_entries.append(f"    {rel!r}: {base64.b64encode(data).decode('ascii')!r},")
        else:
            text_entries.append(f"    {rel!r}: {_triple_quoted(text)},")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    never_touched = ", ".join(sorted(PROTECTED_FILES) + [f"{name}/" for name in sorted(PROTECTED_DIRS)])
    header = f'''#!/usr/bin/env python3
"""Update rm-agent to this version ({len(listed)} files to write, {len(removed)} to delete; generated {generated_at}
by scripts/build_update.py).

Put this file in the project root (the folder that holds app/ and .env), then run:
    python {OUTPUT_NAME}                  # replace the files below and delete the removed ones
    python {OUTPUT_NAME} --dry-run        # only list what would change
    python {OUTPUT_NAME} --target PATH    # update another project root

Each embedded file replaces the existing one completely; the files in DELETED_FILES are deleted.
{never_touched} are never created, changed or deleted. Python 3.9+, standard library only.
"""

import argparse
import base64
import sys
from pathlib import Path

GENERATED_AT = {generated_at!r}
PROTECTED_FILES = {tuple(sorted(PROTECTED_FILES))!r}
PROTECTED_DIRS = {tuple(sorted(PROTECTED_DIRS))!r}
DELETED_FILES = {removed!r}

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

    # The generated script must reproduce every file byte for byte and carry the same delete list.
    namespace: dict = {"__name__": "update_check"}
    exec(compile(content, str(output), "exec"), namespace)
    restored = namespace["_manifest"]()
    mismatched = [rel for rel in listed if restored.get(rel) != (ROOT / rel).read_bytes()]
    if mismatched or set(restored) != set(listed):
        raise SystemExit(f"Round-trip check failed for: {mismatched or sorted(set(restored) ^ set(listed))}")
    if namespace["DELETED_FILES"] != removed:
        raise SystemExit("Round-trip check failed for DELETED_FILES")

    print(f"Wrote {output} ({len(listed)} files to write: {len(text_entries)} text, {len(binary_entries)} binary; "
          f"{len(removed)} to delete; {output.stat().st_size // 1024} KB). Round-trip check passed.")
    for rel in listed:
        print(f"  write  {rel}")
    for rel in removed:
        print(f"  delete {rel}")
    return output


if __name__ == "__main__":
    build()
