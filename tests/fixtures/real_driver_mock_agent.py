#!/usr/bin/env python3
"""Codex-JSONL shaped fixture for the real-driver ablation public seam."""

import json
import sys
import time
import uuid
import re
from pathlib import Path


task = sys.argv[1]
session_root = Path(sys.argv[2])
thread_id = str(uuid.uuid4())
session_root.mkdir(parents=True, exist_ok=True)
(session_root / f"rollout-{thread_id}.jsonl").write_text(
    json.dumps(
        {
            "type": "turn_context",
            "payload": {
                "model": "fixture-model",
            },
        }
    )
    + "\n",
    encoding="utf-8",
)
has_skill = "SKILL_CONTENT_SHA256=" in task
nonce_match = re.search(r"EVAL_NONCE=([A-Za-z0-9_-]+)", task)
if "OUTPUT_THEN_SLEEP" in task:
    print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    print(
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "EXPECTED_TOKEN"}}),
        flush=True,
    )
    time.sleep(2)
    raise SystemExit(0)
elif "WRITE_SIDE_EFFECT" in task:
    Path("eval-side-effect.txt").write_text("fixture side effect\n", encoding="utf-8")
    output = "EXPECTED_TOKEN"
elif "loader probe" in task.lower():
    output = nonce_match.group(1) if has_skill and nonce_match else "NO_NONCE"
elif has_skill:
    output = (
        "from google import genai\n"
        "client = genai.Client()\n"
        "client.interactions.create(model='gemini-3.5-flash', input='hi')"
    )
else:
    output = "legacy client.start_chat()"
print(json.dumps({"type": "thread.started", "thread_id": thread_id}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": output}}))
