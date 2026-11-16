# P2S Live View — https://liveview.amanthakkar.com

Public live stream of the Bambu Lab P2S chamber camera, live only while a
print is running. Built Nov 15, 2026 at Aman's direction ("build it, go crazy").

## Architecture

```
P2S camera (RTSPS :322, LAN access code)
  → go2rtc (Docker, :1984) — restreams + serves snapshots
  → liveview-web (nginx:alpine, :80) — serves index.html, status.json,
    proxies /api/* to go2rtc
  → Caddy (homelab network, :80) — routes liveview.amanthakkar.com
  → cloudflared-liveview (dedicated tunnel `liveview`,
    f6ca93e1-f282-457a-ac51-f62c8e221734) — Cloudflare Tunnel to the public
```

- The page plays smooth live video via MSE: the browser pulls a continuous
  fragmented-MP4 stream (`/api/stream.mp4?src=bambu`) into a MediaSource
  (fallback: native HLS on Safari, still snapshots as a last resort).
  A **Refresh stream** button restarts the stream and fetches fresh
  status on demand; a watchdog restarts it if the video clock stalls.
- Snapshot: `/api/frame.jpeg?src=bambu` (used when idle / as fallback).
- Print status: `status.json`, rewritten every minute by cron
  (`write_status.py` reads the bambu-bridge `monitor_state.json`).
  Page shows the live player when printing, else a still snapshot.
- No printer control is exposed: the browser only ever talks to nginx/go2rtc.
  The LAN access code lives only in `go2rtc/go2rtc.yaml` (0600) on the Pi,
  assembled from `/home/aman/bambu-bridge/.bridge_credentials` — never in
  the page, URLs, or chat.

## Containers (all `--restart unless-stopped`, network `homelab`)

- `go2rtc` — alexxit/go2rtc:1.9.15, config `go2rtc/go2rtc.yaml` (ro).
- `liveview-web` — nginx:alpine, webroot `web/` (ro), conf `nginx.conf` (ro).
- `cloudflared-liveview` — cloudflare/cloudflared, `--user root`, config
  `cloudflared.yml` (ro), creds `~/.cloudflared` (ro).

## Files in /home/aman/liveview/

- `index.html` — the page (in `web/` when served; edit the copy in `web/`).
- `nginx.conf` — nginx server (has `resolver 127.0.0.11` so go2rtc re-resolves).
- `write_status.py` — cron (every minute) → `web/status.json`.
- `cloudflared.yml` — dedicated-tunnel config (hostname + creds file).
- `go2rtc/go2rtc.yaml` — stream def (0600, contains the LAN access code).
  NEVER committed; `go2rtc.yaml.example` is the redacted template.

## Why MSE, not HLS? (Nov 16)

The first version played HLS video — and it kept freezing after a second
or two. First diagnosis was wrong: the HLS segments (one ~500 KB segment
plus one ~376-byte segment per second) were read as "the camera only
produces ~1 frame/sec", and the page was briefly rebuilt as a
per-second still refresh. Aman pushed back — the Bambu Handy app is
smooth — and a real measurement settled it: ffmpeg decoding the RTSP
stream counted **302 frames in 10 s, ~30 fps**. The fat HLS segments
were keyframes (GOP ~1 s), not the only frames. The camera was never the
problem; the HLS player was: go2rtc's HLS playlist holds only 2 × 0.5 s
segments, so the player had ~1 s of buffer and any tunnel latency made
the next segment roll off the playlist before it loaded — play 1 s,
stall, repeat. The fix is MSE: the page pulls a continuous fragmented-MP4
stream (`/api/stream.mp4?src=bambu`, verified: `ftypiso5` + `moov`,
~1.5 Mbps through the tunnel) into a MediaSource — no segment windows,
no 1-second buffer cliff. A watchdog restarts the stream if the video
clock stops advancing; the Refresh button restarts it on demand.
Verified smooth by Aman in the browser, Nov 16.

## Gotchas (learned the hard way, Nov 15–16)

1. **Bind-mount inode trap:** `sed -i` on a bind-mounted file (Caddyfile)
   replaces the inode; a running container keeps seeing the OLD file, so
   `caddy reload` reports "config is unchanged" and the new route never
   loads. After editing a bind-mounted config, `docker restart <container>`
   — reload is not enough. (Same applies to nginx.conf; restart, don't reload.)
2. **Nested subdomains + free Universal SSL:** `live.printing.amanthakkar.com`
   fails TLS (handshake failure) — the free cert covers only one subdomain
   level. Use a first-level subdomain (`liveview.amanthakkar.com`). The stale
   `live.printing.amanthakkar.com` CNAME (→ old tunnel) still exists in DNS;
   harmless, delete in the Cloudflare dashboard if desired.
3. **Split-config tunnel replicas do NOT work:** a second cloudflared on the
   SAME tunnel with a different ingress config is ignored by the edge — the
   first/root connector's config wins (requests flap 200/404, then solid 404).
   Use a DEDICATED tunnel per hostname set (`cloudflared tunnel create`).
4. **DNS CNAME retarget lag:** after `tunnel route dns -f` moves a hostname
   to another tunnel, allow ~2 min before trusting curl results.
5. **go2rtc HLS endpoint** is `/api/stream.m3u8?src=<name>`; `/api/hls.m3u8`
   and `/api/mjpeg` 404 on 1.9.x. `frame.jpeg` works and is the fastest
   proof the camera path is alive.
6. **nginx file perms:** webroot files must be world-readable (644) — the
   nginx worker runs as `nginx`, not aman. A 660 file gives an empty 403.
7. **Cloudflare Tunnel + video:** fine for a hobby stream with a few viewers;
   each viewer pulls ~2–4 Mbps from home upload. Not a CDN — don't expect it
   to survive going viral.
8. **HLS stalls on this setup (Nov 16):** go2rtc's HLS playlist holds
   only 2 × 0.5 s segments, so the player gets ~1 s of buffer and freezes
   whenever a segment fetch lags. The camera is fine (~30 fps, measured
   with ffmpeg) — don't blame it, and don't "fix" HLS with player tuning.
   Use the MSE endpoint (`/api/stream.mp4?src=bambu`) instead. Also note
   an HLS session 404s within seconds if nothing consumes its segments;
   that's idle reaping, not breakage. And don't infer frame rate from
   segment sizes: the ~500 KB segments are keyframes (GOP ~1 s), not the
   only frames — that misread cost a whole wrong rebuild.
9. **Printer file access (Nov 16):** FTPS on :990 (vsFTPd 3.0.5) works,
   but ONLY in active mode with TLS session reuse on the data channel;
   PASV data-channel TLS hangs, and PROT C is refused (522, data must be
   encrypted). It exposes only the SD card (empty here) — the current
   print file (`/data/Metadata/plate_1.gcode` per MQTT) lives in internal
   storage and RETR returns 550. So the printer cannot serve the current
   model/STL, and the job name is just the Bambu Studio project name —
   it lies when a project is reused. A "what's printing" model viewer
   must get its file from Bambu Studio or Bambu Cloud, not the printer.

## Useful commands (on the Pi)

- Restart everything: `docker restart go2rtc liveview-web cloudflared-liveview caddy`
- Check the stream: `curl -s http://127.0.0.1:1984/api/frame.jpeg?src=bambu -o /tmp/f.jpg`
- Check the public page: `curl -s https://liveview.amanthakkar.com/status.json`
- Logs: `docker logs --tail 20 <container>`
