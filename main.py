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

# ✅ LOSSLESS IMAGE FORMAT
IMAGE_EXT = "png"   # TRUE LOSSLESS OUTPUT

os.makedirs(FILES_ROOT, exist_ok=True)
os.makedirs(CACHE_ROOT, exist_ok=True)

app.mount("/files", StaticFiles(directory=FILES_ROOT), name="files")


def safe_run(cmd: list):
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
    # ✅ 3️⃣ VISUAL SCENE DETECTION (PNG)
    # --------------------------------------------------
    if not os.listdir(scene_dir):
        safe_run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"select='gt(scene,{SCENE_THRESHOLD})'",
            "-vsync", "vfr",
            f"{scene_dir}/scene_%04d.{IMAGE_EXT}"
        ])

    visual_files = sorted(os.listdir(scene_dir))
    visual_count = len(visual_files)

    visual_event_times = []
    if visual_count > 0:
        for i in range(visual_count):
            t = duration * (i + 1) / (visual_count + 1)
            visual_event_times.append(t)

   # --------------------------------------------------
# ✅ 4️⃣ INTELLIGENT TIME SCHEDULER (SPACED + WEIGHTED)
# --------------------------------------------------

# ---- A) BASELINE EVEN COVERAGE (50%)
baseline_slots = MAX_FRAMES // 2
baseline_times = [
    duration * (i + 1) / (baseline_slots + 1)
    for i in range(baseline_slots)
]

# ---- B) EVENT-FOCUSED DENSITY (50%)
event_slots = MAX_FRAMES - baseline_slots

# Prioritise both visual & audio events
priority_events = sorted(set(visual_event_times + audio_event_times))

# If too many events, spread them proportionally
if priority_events:
    step = max(1, len(priority_events) // event_slots)
    dense_times = priority_events[::step][:event_slots]
else:
    dense_times = []

# ---- C) MICRO-BURSTS AROUND EACH EVENT (±0.6s)
expanded_dense_times = []
for t in dense_times:
    for offset in (-0.6, 0.0, 0.6):
        expanded_dense_times.append(t + offset)

# ---- D) MERGE + CLAMP + DEDUPE
all_times = baseline_times + expanded_dense_times

safe_times = []
epsilon = 0.1
for t in sorted(all_times):
    if 0 <= t <= duration - epsilon:
        safe_times.append(round(t, 2))

safe_times = sorted(set(safe_times))

# ---- E) FINAL 20 MAX GUARANTEE
safe_times = safe_times[:MAX_FRAMES]


   # --------------------------------------------------
# ✅ 5️⃣ FRAME EXTRACTION AT SCHEDULED TIMES (PNG)
# --------------------------------------------------
if not os.listdir(burst_dir):

    for i, t in enumerate(safe_times):

        burst_path = os.path.join(
            burst_dir, f"frame_{i:03d}.{IMAGE_EXT}"
        )

        # Single-frame precise extraction (no blur, no duplicates)
        safe_run([
            "ffmpeg", "-y",
            "-ss", str(t),
            "-i", video_path,
            "-frames:v", "1",
            burst_path
        ])


    # --------------------------------------------------
    # ✅ 6️⃣ SAFETY FALLBACK (LOSSLESS PNG)
    # --------------------------------------------------
    if not os.listdir(burst_dir):
        interval = max(duration / MAX_FRAMES, 2.0)

        safe_run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps=1/{interval}",
            "-frames:v", str(MAX_FRAMES),
            f"{fallback_dir}/fallback_%03d.{IMAGE_EXT}"
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
