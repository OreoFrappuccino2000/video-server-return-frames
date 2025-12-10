import hashlib
import subprocess
import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# --------------------------------------------------
# ✅ CONFIG
# --------------------------------------------------
FILES_ROOT = "/app/files"
CACHE_ROOT = "/tmp/cache"

os.makedirs(FILES_ROOT, exist_ok=True)
os.makedirs(CACHE_ROOT, exist_ok=True)

app.mount("/files", StaticFiles(directory=FILES_ROOT), name="files")

MAX_FRAMES = 20
BASE_URL = "https://video-server-return-frames-production.up.railway.app"


@app.post("/run")
def run(video_url: str):
@@ -34,9 +37,11 @@ def run(video_url: str):
    # --------------------------------------------------
    # ✅ 1️⃣ VIDEO DOWNLOAD (CACHED)
    # --------------------------------------------------
    if not os.path.exists(cached_video_path):
    video_cached = os.path.exists(cached_video_path)

    if not video_cached:
        try:
            with requests.get(video_url, stream=True, timeout=120) as r:
            with requests.get(video_url, stream=True, timeout=120, allow_redirects=True) as r:
                r.raise_for_status()
                with open(cached_video_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
@@ -46,9 +51,6 @@ def run(video_url: str):
            raise HTTPException(400, f"Failed to download video: {e}")

    video_path = cached_video_path
    job_id = video_hash
    job_dir = os.path.join(FILES_ROOT, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # --------------------------------------------------
    # ✅ 2️⃣ PROBE DURATION
@@ -64,9 +66,9 @@ def run(video_url: str):
        raise HTTPException(400, "Failed to probe video")

    # --------------------------------------------------
    # ✅ 3️⃣ SMART PHASE SAMPLING
    # ✅ 3️⃣ SMART PHASE SAMPLING  ✅ FIXED INDENT
    # --------------------------------------------------
     phases = {
    phases = {
        "early": (0.05, 0.25),
        "mid":   (0.35, 0.60),
        "late":  (0.70, 0.90),
@@ -75,13 +77,15 @@ def run(video_url: str):

    frame_urls = []
    frames_per_phase = math.ceil(MAX_FRAMES / len(phases))
    frames_cached = True

    for phase, (start_r, end_r) in phases.items():
        phase_dir = os.path.join(job_dir, phase)
        os.makedirs(phase_dir, exist_ok=True)

        # ✅ Skip extraction if frames already exist
        if not os.listdir(phase_dir):
            frames_cached = False

            start_t = duration * start_r
            end_t = duration * end_r
            interval = max((end_t - start_t) / frames_per_phase, 1)
@@ -98,18 +102,18 @@ def run(video_url: str):
            subprocess.run(ffmpeg_cmd, check=True)

        for f in sorted(os.listdir(phase_dir)):
            url = f"/files/{job_id}/{phase}/{f}"
            url = f"{BASE_URL}/files/{job_id}/{phase}/{f}"
            frame_urls.append(url)

    frame_urls = frame_urls[:MAX_FRAMES]

    # --------------------------------------------------
    # ✅ 4️⃣ FINAL RESPONSE (DIRECT DOWNLOADABLE FRAME URLS)
    # ✅ 4️⃣ FINAL RESPONSE (DIRECT DOWNLOADABLE URLs)
    # --------------------------------------------------
    return {
        "job_id": job_id,
        "duration": duration,
        "total_frames": len(frame_urls),
        "frame_urls": frame_urls,
        "cached": False
        "cached": video_cached and frames_cached
    }
