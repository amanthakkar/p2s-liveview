#!/usr/bin/env python3
"""Refresh the liveview 'Now printing' card from the Bambu Cloud task list.

Runs on the VM (the Bambu Cloud credential lives in the Secure Vault and is
only usable here via the custom.legacy-bambu-lab connector's surrogate auth).
Fetches the latest task for the P2S, downloads its cover image (the signed
S3 URL expires after 30 min, so the image is stored locally), and ships
now_printing.json + cover.jpg to the Pi webroot, where index.html reads them.

Never prints or logs credentials. The connector never exposes the raw token.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/home/hatch/workspace/custom-connectors/custom.legacy-bambu-lab/bin")
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/templates/skill/bin")

import bambu  # noqa: E402  (reuses api_get + BROWSER_UA + surrogate auth)

P2S_DEV_ID = "22E8AJ642901539"
PI = "homelab-tailnet"
WEBROOT = "/home/aman/liveview/web"
SSH_CONFIG = "/home/hatch/.ssh/config"
WORKDIR = "/home/hatch/workspace/liveview-build"

STATUS_LABELS = {2: "completed", 4: "printing", 5: "paused", 6: "failed", 7: "cancelled"}


def fetch_latest_task():
    data = bambu.api_get("/v1/user-service/my/tasks?limit=5")
    for task in data.get("hits", []):
        if task.get("deviceId") == P2S_DEV_ID:
            return task
    return None


def download_cover(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": bambu.BROWSER_UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    if len(body) < 1024:
        raise ValueError(f"cover too small ({len(body)} bytes)")
    if body.startswith(b"\x89PNG"):
        ext = "png"
    elif body.startswith(b"\xff\xd8\xff"):
        ext = "jpg"
    else:
        raise ValueError("cover is not a JPEG/PNG image")
    with open(dest, "wb") as f:
        f.write(body)
    return len(body), ext


def ship(local_path, remote_name):
    tmp_remote = f"{WEBROOT}/.{remote_name}.tmp"
    subprocess.run(
        ["scp", "-F", SSH_CONFIG, "-q", local_path, f"{PI}:{tmp_remote}"],
        check=True,
    )
    # Webroot files must be world-readable: the nginx worker runs as the
    # nginx user, and a 600 file serves an empty 403.
    subprocess.run(
        ["ssh", "-F", SSH_CONFIG, PI,
         f"mv {tmp_remote} {WEBROOT}/{remote_name} && chmod 644 {WEBROOT}/{remote_name}"],
        check=True,
    )


def main():
    task = fetch_latest_task()
    if not task:
        print("no task found for P2S", file=sys.stderr)
        return 1

    status_code = task.get("status")
    design_id = task.get("designId") or 0
    payload = {
        "task_id": task.get("id"),
        "title": task.get("title") or "",
        "design_title": task.get("designTitle") or "",
        "design_id": design_id,
        "model_id": task.get("modelId") or "",
        "status_code": status_code,
        "status": STATUS_LABELS.get(status_code, str(status_code)),
        "mode": task.get("mode") or "",
        "start_time": task.get("startTime") or "",
        "weight_g": task.get("weight"),
        "cover_file": "cover.jpg",
        "makerworld_url": (
            f"https://makerworld.com/en/models/{design_id}" if design_id else ""
        ),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    cover_url = task.get("cover") or ""
    cover_file = payload["cover_file"]
    if cover_url:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".img", dir=WORKDIR) as tmp:
                tmp_path = tmp.name
            size, ext = download_cover(cover_url, tmp_path)
            cover_file = f"cover.{ext}"
            cover_path = os.path.join(WORKDIR, cover_file)
            os.replace(tmp_path, cover_path)
            payload["cover_file"] = cover_file
            print(f"cover downloaded ({size} bytes, {ext})")
        except Exception as e:
            print(f"cover download failed ({e}); keeping previous image", file=sys.stderr)
            cover_path = os.path.join(WORKDIR, payload["cover_file"])
    else:
        cover_path = os.path.join(WORKDIR, payload["cover_file"])

    json_path = os.path.join(WORKDIR, "now_printing.json")
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    ship(json_path, "now_printing.json")
    if os.path.exists(cover_path):
        ship(cover_path, payload["cover_file"])
    print(f"shipped now_printing.json: task {payload['task_id']} "
          f"'{payload['title']}' status={payload['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
