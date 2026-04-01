#!/usr/bin/env python3
"""
webhook_listener.py — Receive training completion callbacks from GPU instances.

Usage:
    uvicorn scripts.webhook_listener:app --host 0.0.0.0 --port 8080

The GPU training script should POST to http://your-vps:8080/callback with:
    {"id": "h003", "status": "done", "metrics": {"coverage": 0.62}}
"""

import subprocess
import sys
import yaml
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request

sys.path.insert(0, str(Path(__file__).parent))
from project_config import find_hypothesis

app = FastAPI()

HYPOTHESES_DIR = Path(__file__).parent.parent / "experiments" / "hypotheses"


@app.post("/callback")
async def training_callback(request: Request):
    data = await request.json()
    hid = data.get("id", "unknown")
    status = data.get("status", "unknown")
    metrics = data.get("metrics", {})

    print(f"[{datetime.now().isoformat()}] Callback: {hid} -> {status}")

    # Update hypothesis YAML (searches legacy dir + project overlays)
    hyp_path = find_hypothesis(hid, HYPOTHESES_DIR)
    if hyp_path:
        hyp = yaml.safe_load(hyp_path.read_text())
        hyp["actual_result"] = str(metrics) if metrics else status
        hyp["status"] = "validated" if status == "done" else "rejected"
        hyp_path.write_text(yaml.dump(hyp, default_flow_style=False, allow_unicode=True))
        print(f"  Updated {hid}: {hyp['status']}")

    # Optional: notify via a simple approach (write to a file Claude Code can check)
    notify_path = Path(__file__).parent.parent / "vault" / "notifications.md"
    with open(notify_path, "a") as f:
        f.write(f"\n- [{datetime.now().strftime('%Y-%m-%d %H:%M')}] "
                f"Experiment **{hid}** finished: {status}. Metrics: {metrics}\n")

    return {"received": True, "id": hid}


@app.get("/health")
async def health():
    return {"status": "ok"}
