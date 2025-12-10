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
BASE_URL = "https://video-server-return-frames-production.up.railway.app"

MAX_FRAMES = 20
SCENE_THRESHOLD = 0.35
BURST_FPS = 8
BURST_SECONDS = 2.5
AUDIO_DB_THRESHOLD = -10

# ✅ IMAGE QUALITY SETTINGS (CRITICAL)
JPG_QUALITY = "2"          # 2 = visually lossless, 31 = worst
JPG_PIXEL_FORMAT = "yuvj420p"  # full-range JPEG (prevents dull colors)

os.makedirs(FILES_ROOT, exist_ok=True)
os.makedirs(CACHE_ROOT, exist_ok=True)

app.mount("/files", StaticFiles(directory=FILES_ROOT), name="files")


def safe_run(cmd: list):
    """Run ffmpeg/ffprobe safely."""
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, f"ffmpeg failed: {e}")


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
    # ✅ 1️⃣ VIDEO DOWNLOAD (CACHED)
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

    if duration <= 0:
        raise HTTPException(400, "Invalid video duration")

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

        peak_indices = []
        for idx, line in enumerate(proc.stderr):
            if "Peak level dB" in line:
                try:
                    db = float(line.strip().split(":")[-1])
                    if db > AUDIO_DB_THRESHOLD:
                        peak_indices.append(idx)
                except:
                    pass

        n_peaks = min(len(peak_indices), MAX_FRAMES // 2)
        if n_peaks > 0:
            for i in range(n_peaks):
                t = duration * (i + 1) / (n_peaks + 1)
                audio_event_times.append(t)

    except:
        audio_event_times = []

    # --------------------------------------------------
    # ✅ 3️⃣ VISUAL SCENE DETECTION (HIGH QUALITY JPG)
    # --------------------------------------------------
    if not os.listdir(scene_dir):
        safe_run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"select='gt(scene,{SCENE_THRESHOLD})'",
            "-vsync", "vfr",
            "-q:v", JPG_QUALITY,
            "-pix_fmt", JPG_PIXEL_FORMAT,
            f"{scene_dir}/scene_%04d.jpg"
        ])

    visual_files = sorted(os.listdir(scene_dir))
    visual_count = len(visual_files)

    visual_event_times = []
    if visual_count > 0:
        for i in range(visual_count):
            t = duration * (i + 1) / (visual_count + 1)
            visual_event_times.append(t)

    # --------------------------------------------------
    # ✅ 4️⃣ MERGE & CLAMP EVENT TIMES
    # --------------------------------------------------
    all_event_times = sorted(set(visual_event_times + audio_event_times))

    epsilon = 0.1
    safe_times = []
    for t in all_event_times:
        if t < 0:
            continue
        if t > duration - epsilon:
            t = duration - epsilon
        safe_times.append(t)

    if not safe_times:
        safe_times = [duration / 2.0]

    safe_times = safe_times[:MAX_FRAMES]

    # --------------------------------------------------
    # ✅ 5️⃣ HIGH-FPS BURST EXTRACTION (BEST JPG QUALITY)
    # --------------------------------------------------
    if not os.listdir(burst_dir):
        for i, t in enumerate(safe_times):
            start = max(0, t - BURST_SECONDS / 2.0)
            burst_pattern = os.path.join(burst_dir, f"burst_{i:03d}_%03d.jpg")

            safe_run([
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(BURST_SECONDS),
                "-vf", f"fps={BURST_FPS}",
                "-q:v", JPG_QUALITY,
                "-pix_fmt", JPG_PIXEL_FORMAT,
                burst_pattern
            ])

    # --------------------------------------------------
    # ✅ 6️⃣ SAFETY FALLBACK (BEST JPG QUALITY)
    # --------------------------------------------------
    if not os.listdir(burst_dir):
        interval = max(duration / MAX_FRAMES, 2.0)

        safe_run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps=1/{interval}",
            "-frames:v", str(MAX_FRAMES),
            "-q:v", JPG_QUALITY,
            "-pix_fmt", JPG_PIXEL_FORMAT,
            f"{fallback_dir}/fallback_%03d.jpg"
        ])

        active_dir = fallback_dir
    else:
        active_dir = burst_dir

    # --------------------------------------------------
    # ✅ 7️⃣ FINAL URL RESPONSE
    # --------------------------------------------------
    frame_urls = [
        f"{BASE_URL}/files/{job_id}/{os.path.basename(active_dir)}/{f}"
        for f in sorted(os.listdir(active_dir))
    ][:MAX_FRAMES]

    return {
        "job_id": job_id,
        "duration": duration,
        "total_frames": len(frame_urls),
        "frame_urls": frame_urls,
        "cached": video_cached
    }
