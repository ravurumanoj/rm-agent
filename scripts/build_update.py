"""Build update_rm_agent.py: one self-contained script that copies only the listed changed files to another machine.

Run from the project root after editing UPDATED_FILES:
    python scripts/build_update.py

Copy the generated update_rm_agent.py to the other machine's project root and run it there (see its docstring).
Unlike replicate_rm_agent.py it carries only UPDATED_FILES and never deletes anything. .env, .env.example and
app/config.py are never included or written.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

from build_replica import _as_text, _triple_quoted

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_NAME = "update_rm_agent.py"

# Files changed for the admin-agent improvements (scope, query rewrite, CLM references, steps, prompts).
UPDATED_FILES = [
    "README.md",
    "app/agents/admin_agent.py",
    "app/agents/final_agent.py",
    "app/agents/formatting.py",
    "app/agents/sufficiency.py",
    "app/constants.py",
    "app/graph/nodes/answer_nodes.py",
    "app/graph/nodes/common.py",
    "app/graph/nodes/execution_nodes.py",
    "app/graph/nodes/orchestrator_node.py",
    "app/prompts/admin_agent.py",
    "app/prompts/agent_catalog.py",
    "app/prompts/common.py",
    "app/prompts/final_agent.py",
    "app/prompts/orchestrator/routing.py",
    "app/prompts/sufficiency.py",
    "app/schemas/orchestrator.py",
    "app/services/unique_chat.py",
    "app/tools/admin_client.py",
]

# Never shipped, even if listed above; the generated script refuses to write them too.
PROTECTED = {".env", ".env.example", "app/config.py"}


RESTORE_CODE = r'''

PROTECTED = {".env", ".env.example", "app/config.py"}


def _manifest() -> dict[str, bytes]:
    files = {rel: text.encode("utf-8") for rel, text in TEXT_FILES.items()}
    files.update({rel: base64.b64decode(data) for rel, data in BINARY_FILES.items()})
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy the changed rm-agent files into a project.")
    parser.add_argument("--target", default=str(Path(__file__).resolve().parent), help="Project root to write into (default: this script's folder).")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be created or updated.")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    files = _manifest()
    print(f"Target: {target}")
    print(f"Files in this update: {len(files)} (generated {GENERATED_AT})")
    if not (target / "app").is_dir():
        print("Target does not look like the rm-agent project root (no app/ folder). Nothing was changed.")
        return 1

    created = updated = unchanged = 0
    for rel, data in sorted(files.items()):
        if rel in PROTECTED or Path(rel).name in PROTECTED:
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

    verb = "Would create/update" if args.dry_run else "Created/updated"
    print(f"{verb}: {created} created, {updated} updated, {unchanged} unchanged.")
    print(".env, .env.example and app/config.py were not touched; no file was deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def build() -> Path:
    listed = [rel for rel in UPDATED_FILES if rel not in PROTECTED and Path(rel).name not in PROTECTED]
    missing = [rel for rel in listed if not (ROOT / rel).is_file()]
    if missing:
        raise SystemExit(f"Listed files not found: {missing}")

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
    header = f'''#!/usr/bin/env python3
"""Update the changed rm-agent files ({len(listed)} files, generated {generated_at} by scripts/build_update.py).

Put this file in the project root (the folder that holds app/ and .env), then run:
    python {OUTPUT_NAME}                  # create/overwrite the files below
    python {OUTPUT_NAME} --dry-run        # only list what would change
    python {OUTPUT_NAME} --target PATH    # write into another project root

Only the files embedded below are written; nothing is deleted. .env, .env.example and app/config.py are never
created or changed. Python 3.9+, standard library only.
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
    namespace: dict = {"__name__": "update_check"}
    exec(compile(content, str(output), "exec"), namespace)
    restored = namespace["_manifest"]()
    mismatched = [rel for rel in listed if restored.get(rel) != (ROOT / rel).read_bytes()]
    if mismatched or set(restored) != set(listed):
        raise SystemExit(f"Round-trip check failed for: {mismatched or sorted(set(restored) ^ set(listed))}")

    print(f"Wrote {output} ({len(listed)} files: {len(text_entries)} text, {len(binary_entries)} binary; "
          f"{output.stat().st_size // 1024} KB). Round-trip check passed.")
    for rel in listed:
        print(f"  {rel}")
    return output


if __name__ == "__main__":
    build()
