from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import requests, os, uuid, shutil

app = FastAPI()

FILES_ROOT = "/app/files"
os.makedirs(FILES_ROOT, exist_ok=True)

app.mount("/files", StaticFiles(directory=FILES_ROOT), name="files")


@app.post("/register_frames")
def register_frames(payload: dict):
    """
    Input:  { "frame_urls": [ "https://video-server/.../scene_001.jpg", ... ] }
    Output: { "job_id": "...", "public_urls": [ "https://unzip-server/...jpg", ... ] }
    """

    frame_urls = payload.get("frame_urls", [])
    if not frame_urls:
        raise HTTPException(400, "frame_urls required")

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(FILES_ROOT, job_id)
    os.makedirs(job_dir, exist_ok=True)

    public_urls = []

    for i, url in enumerate(frame_urls):
        filename = f"frame_{i:03d}.jpg"
        local_path = os.path.join(job_dir, filename)

        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in r.iter_content(1024 * 512):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            raise HTTPException(400, f"Failed to fetch frame: {url} | {e}")

        public_urls.append(
            f"https://unzip-server-production-e061.up.railway.app/files/{job_id}/{filename}"
        )

    return {
        "job_id": job_id,
        "total": len(public_urls),
        "public_urls": public_urls
    }


@app.delete("/cleanup/{job_id}")
def cleanup(job_id: str):
    path = os.path.join(FILES_ROOT, job_id)
    if os.path.exists(path):
        shutil.rmtree(path)
        return {"status": "deleted"}
    return {"status": "not_found"}
