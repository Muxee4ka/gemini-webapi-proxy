#!/usr/bin/env python3
"""Generate a video through the Gemini WebAPI proxy (Veo via Gemini Web).

Starts a job at POST {GEMINI_PROXY_URL}/videos, polls it, downloads the mp4 and
prints `VIDEO_PATH=<file>`. Exit codes: 0 done, 2 Gemini refused / limit / glitch,
1 usage or network error. Never retries on its own: a re-sent prompt burns the
account's daily video limit.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

POLL_SECONDS = 15
# Cloudflare in front of the proxy blocks the default "Python-urllib" signature (error 1010).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) gemini-video-skill/1.0"


def api(method: str, url: str, key: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def reference(value: str) -> str:
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value).expanduser()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt", required=True, help="what happens in the video")
    ap.add_argument("--image", help="reference image to animate: file path or URL")
    ap.add_argument("--seconds", default="8")
    ap.add_argument("--size", help="e.g. 1280x720 or 720x1280")
    ap.add_argument("--out-dir", default=os.environ.get("GEMINI_VIDEO_OUT", "video-output"))
    ap.add_argument("--timeout", type=int, default=900, help="max wait, seconds")
    args = ap.parse_args()

    base = os.environ.get("GEMINI_PROXY_URL", "").rstrip("/")
    key = os.environ.get("GEMINI_PROXY_KEY", "")
    if not base or not key:
        print("GEMINI_PROXY_URL / GEMINI_PROXY_KEY are not set", file=sys.stderr)
        return 1

    body = {"prompt": args.prompt, "seconds": args.seconds}
    if args.size:
        body["size"] = args.size
    if args.image:
        body["input_reference"] = reference(args.image)

    try:
        job = json.loads(api("POST", f"{base}/videos", key, body))
    except urllib.error.HTTPError as exc:
        print(f"start failed: HTTP {exc.code} {exc.read()[:300]!r}", file=sys.stderr)
        return 1
    job_id = job["id"]
    print(f"job {job_id} started, waiting (usually 1-3 min)...", flush=True)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            job = json.loads(api("GET", f"{base}/videos/{job_id}", key))
        except (urllib.error.URLError, TimeoutError) as exc:  # transient; the job keeps running
            print(f"poll error, retrying: {exc}", file=sys.stderr)
            continue
        if job["status"] == "completed":
            out = Path(args.out_dir).expanduser().resolve()
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{job_id}.mp4"
            path.write_bytes(api("GET", f"{base}/videos/{job_id}/content", key, timeout=300))
            print(f"VIDEO_PATH={path}")
            return 0
        if job["status"] == "failed":
            err = job.get("error") or {}
            print(f"FAILED code={err.get('code')} message={err.get('message')}")
            return 2
    print(f"FAILED code=timeout message=job {job_id} not finished in {args.timeout}s")
    return 2


if __name__ == "__main__":
    sys.exit(main())
