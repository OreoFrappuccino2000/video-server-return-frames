import hashlib
import subprocess
import os
import math
import requests
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# --------------------------------------------------
# ✅ CONFIG
# --------------------------------------------------
FILES_ROOT = "/app/files"
CACHE_ROOT = "/tmp/cache"
BASE_URL = "https://video-server-return-frames-production.up.railway.app"

MAX_FRAMES = 20
SCENE_THRESHOLD = 0.35
BURST_FPS = 8
BURST_SECONDS = 2.5
AUDIO_DB_THRESHOLD = -10

os.makedirs(FILES_ROOT, exist_ok=True)
os.makedirs(CACHE_ROOT, exist_ok=True)

app.mount("/files", StaticFiles(directory=FILES_ROOT), name="files")

# --------------------------------------------------
# ✅ API
# --------------------------------------------------

@app.post("/run")
def run(video_url: str):

    video_url = video_url.strip()

    # --------------------------------------------------
    # ✅ 0️⃣ HASH + FOLDERS
    # --------------------------------------------------
    video_hash = hashlib.md5(video_url.encode()).hexdigest()
    job_id = video_hash

    job_dir = os.path.join(FILES_ROOT, job_id)
    scene_dir = os.path.join(job_dir, "scenes")
    burst_dir = os.path.join(job_dir, "bursts")
    fallback_dir = os.path.join(job_dir, "fallback")

    for d in [job_dir, scene_dir, burst_dir, fallback_dir]:
        os.makedirs(d, exist_ok=True)

    cached_video_path = os.path.join(CACHE_ROOT, f"{video_hash}.mp4")

    # --------------------------------------------------
    # ✅ 1️⃣ DOWNLOAD (CACHED)
    # --------------------------------------------------
    video_cached = os.path.exists(cached_video_path)

    if not video_cached:
        try:
            with requests.get(video_url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(cached_video_path, "wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
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
        raise HTTPException(400, "Failed to probe duration")

    # --------------------------------------------------
    # ✅ 2.5️⃣ OPTIONAL AUDIO PEAK DETECTION (SAFE)
    # --------------------------------------------------
    audio_event_times = []

    try:
        audio_cmd = [
            "ffmpeg", "-i", video_path,
            "-af", "astats,ametadata=print:key=lavfi.astats.Overall.Peak_level",
            "-f", "null", "-"
        ]

        proc = subprocess.Popen(
            audio_cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True
        )

        current_time = 0

        for line in proc.stderr:
            if "Peak level dB" in line:
                try:
                    db = float(line.strip().split(":")[-1])
                    if db > AUDIO_DB_THRESHOLD:
                        audio_event_times.append(current_time)
                except:
                    pass
            current_time += 0.5   # approx rolling window

    except:
        audio_event_times = []   # ✅ No audio present → safe fallback


    # --------------------------------------------------
    # ✅ 3️⃣ VISUAL SCENE DETECTION (PRIMARY)
    # --------------------------------------------------
    if not os.listdir(scene_dir):

        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
            "-vsync", "vfr",
            f"{scene_dir}/scene_%04d.jpg"
        ], check=True)

    # --------------------------------------------------
    # ✅ 4️⃣ MERGE VISUAL + AUDIO EVENT TIMES
    # --------------------------------------------------
    visual_count = len(os.listdir(scene_dir))
    visual_times = [
        duration * (i / max(visual_count, 1))
        for i in range(visual_count)
    ]

    all_event_times = sorted(set(
        visual_times + audio_event_times
    ))

    # --------------------------------------------------
    # ✅ 5️⃣ HIGH-FPS BURST EXTRACTION
    # --------------------------------------------------
    if not os.listdir(burst_dir):

        for i, t in enumerate(all_event_times[:MAX_FRAMES]):

            start = max(0, t - 1.2)
            burst_path = os.path.join(burst_dir, f"burst_{i:03d}_%03d.jpg")

            subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(BURST_SECONDS),
                "-vf", f"fps={BURST_FPS}",
                burst_path
            ], check=True)

    # --------------------------------------------------
    # ✅ 6️⃣ SAFETY FALLBACK (UNIFORM SAMPLING)
    # --------------------------------------------------
    if not os.listdir(burst_dir):

        interval = max(duration / MAX_FRAMES, 2)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps=1/{interval}",
            "-frames:v", str(MAX_FRAMES),
            f"{fallback_dir}/fallback_%03d.jpg"
        ], check=True)

        active_dir = fallback_dir
    else:
        active_dir = burst_dir

    # --------------------------------------------------
    # ✅ 7️⃣ FINAL URL RESPONSE
    # --------------------------------------------------
    frame_urls = []

    for f in sorted(os.listdir(active_dir)):
        frame_urls.append(
            f"{BASE_URL}/files/{job_id}/{os.path.basename(active_dir)}/{f}"
        )

    frame_urls = frame_urls[:MAX_FRAMES]

    return {
        "job_id": job_id,
        "duration": duration,
        "total_frames": len(frame_urls),
        "frame_urls": frame_urls,
        "cached": video_cached
    }
