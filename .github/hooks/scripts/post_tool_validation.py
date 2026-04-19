#!/usr/bin/env python3
import json
import sys
from typing import Any


EDIT_TOOLS = {
    "functions.apply_patch",
    "functions.create_file",
    "functions.vscode_renameSymbol",
}


def find_tool_name(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("toolName"),
        payload.get("tool_name"),
        payload.get("tool", {}).get("name") if isinstance(payload.get("tool"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def main() -> int:
    raw = sys.stdin.read().strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    tool_name = find_tool_name(payload)

    if tool_name in EDIT_TOOLS:
        out = {
            "continue": True,
            "systemMessage": (
                "Code edit detected. Run a lightweight validation step (tests, lint, or direct script run) "
                "before finalizing."
            ),
        }
    else:
        out = {"continue": True}

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
