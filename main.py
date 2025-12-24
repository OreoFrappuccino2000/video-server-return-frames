import hashlib
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import subprocess
import os
import math
import requests

app = FastAPI()

FILES_ROOT = "/app/files"
CACHE_ROOT = "/tmp/cache"

os.makedirs(FILES_ROOT, exist_ok=True)
os.makedirs(CACHE_ROOT, exist_ok=True)

app.mount("/files", StaticFiles(directory=FILES_ROOT), name="files")

MAX_FRAMES = 18
BASE_URL = "https://video-server-return-frames-production.up.railway.app"


@app.post("/run")
def run(video_url: str):
    video_url = video_url.strip()

    # --------------------------------------------------
    # ✅ 0️⃣ HASH KEY FOR CACHING
    # --------------------------------------------------
    video_hash = hashlib.md5(video_url.encode()).hexdigest()
    cached_video_path = os.path.join(CACHE_ROOT, f"{video_hash}.mp4")

    job_id = video_hash
    job_dir = os.path.join(FILES_ROOT, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # --------------------------------------------------
    # ✅ 1️⃣ VIDEO DOWNLOAD (CACHED)
    # --------------------------------------------------
    video_cached = os.path.exists(cached_video_path)

    if not video_cached:
        try:
            with requests.get(video_url, stream=True, timeout=120, allow_redirects=True) as r:
                r.raise_for_status()
                with open(cached_video_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            raise HTTPException(400, f"Failed to download video: {e}")

    video_path = cached_video_path

    # --------------------------------------------------
    # ✅ 2️⃣ PROBE DURATION
    # --------------------------------------------------
    try:
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1",
            video_path
        ]).decode().strip())
    except:
        raise HTTPException(400, "Failed to probe video")

    # --------------------------------------------------
    # ✅ 3️⃣ SMART PHASE SAMPLING  ✅ FIXED INDENT
    # --------------------------------------------------
    phases = {
        "early": (0.05, 0.25),
        "mid":   (0.35, 0.60),
        "late":  (0.70, 0.90),
        "final": (0.90, 0.98)
    }

    frame_urls = []
    frames_per_phase = math.ceil(MAX_FRAMES / len(phases))
    frames_cached = True

    for phase, (start_r, end_r) in phases.items():
        phase_dir = os.path.join(job_dir, phase)
        os.makedirs(phase_dir, exist_ok=True)

        if not os.listdir(phase_dir):
            frames_cached = False

            start_t = duration * start_r
            end_t = duration * end_r
            interval = max((end_t - start_t) / frames_per_phase, 1)

            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_t),
                "-i", video_path,
                "-vf", f"fps=1/{interval}",
                "-frames:v", str(frames_per_phase),
                f"{phase_dir}/scene_%03d.jpg"
            ]

            subprocess.run(ffmpeg_cmd, check=True)

        for f in sorted(os.listdir(phase_dir)):
            url = f"{BASE_URL}/files/{job_id}/{phase}/{f}"
            frame_urls.append(url)

    frame_urls = frame_urls[:MAX_FRAMES]

    # --------------------------------------------------
    # ✅ 4️⃣ FINAL RESPONSE (DIRECT DOWNLOADABLE URLs)
    # --------------------------------------------------
    return {
        "job_id": job_id,
        "duration": duration,
        "total_frames": len(frame_urls),
        "frame_urls": frame_urls,
        "cached": video_cached and frames_cached
    }
