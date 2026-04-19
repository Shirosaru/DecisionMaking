#!/usr/bin/env python3
import json
import re
import sys
from typing import Any


DANGEROUS_PATTERNS = [
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+checkout\s+--\b",
    r"\brm\s+-rf\s+/\b",
    r"\bsudo\s+rm\s+-rf\s+/\b",
]


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


def find_command(payload: dict[str, Any]) -> str:
    for key in ("toolInput", "input", "arguments", "args"):
        value = payload.get(key)
        if isinstance(value, dict):
            cmd = value.get("command")
            if isinstance(cmd, str):
                return cmd
    return ""


def is_dangerous(command: str) -> str | None:
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return pattern
    return None


def main() -> int:
    raw = sys.stdin.read().strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    tool_name = find_tool_name(payload)
    command = find_command(payload)

    if tool_name.endswith("run_in_terminal") and command:
        matched = is_dangerous(command)
        if matched:
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        "Command matches dangerous pattern and requires explicit user confirmation: "
                        + matched
                    ),
                }
            }
            print(json.dumps(out))
            return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "No dangerous command detected",
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
