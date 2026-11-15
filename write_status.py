#!/usr/bin/env python3
"""Publish the printer's current print status for the liveview page.

Reads the bambu-bridge monitor state (local, no secrets) and writes a
small public JSON file into the liveview webroot. Run from cron every
minute. Atomic write via temp file + rename.
"""

import json
import os

SRC = "/home/aman/bambu-bridge/monitor_state.json"
DST = "/home/aman/liveview/web/status.json"

out = {
    "printing": False,
    "status": "unknown",
    "job": None,
    "percent": None,
    "layer": None,
    "updated": None,
}

try:
    with open(SRC) as f:
        st = json.load(f)
    prev = st.get("prev") or {}
    status = (prev.get("status") or "").upper()
    out = {
        "printing": status in ("RUNNING", "PAUSE", "PAUSED"),
        "status": prev.get("status"),
        "job": prev.get("job"),
        "percent": prev.get("percent"),
        "layer": prev.get("layer"),
        "updated": prev.get("time_utc"),
    }
except Exception:
    pass

tmp = DST + ".tmp"
with open(tmp, "w") as f:
    json.dump(out, f)
os.replace(tmp, DST)
