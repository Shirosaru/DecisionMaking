#!/usr/bin/env python3
import json
import sys

# Read and ignore stdin payload; keep behavior deterministic and fast.
_ = sys.stdin.read()

out = {
    "continue": True,
    "systemMessage": (
        "Autonomous mode: gather minimal context, implement directly, validate after edits, "
        "and ask questions only for hard blockers or irreversible choices."
    ),
}
print(json.dumps(out))
