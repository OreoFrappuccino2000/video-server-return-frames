from fastapi import FastAPI, HTTPException
import requests, zipfile, tempfile, os, uuid

app = FastAPI()

@app.post("/unzip_local")
def unzip_local_files(payload: dict):

    files = payload.get("files", [])
    if not files:
        raise HTTPException(400, "files array is required")

    job_id = str(uuid.uuid4())
    work_dir = os.path.join(tempfile.gettempdir(), job_id)
    os.makedirs(work_dir, exist_ok=True)

    all_images = []

    for f in files:
        url = f.get("out_url")  # ✅ this is how we access the LOCAL GET file
        name = f.get("filename", "input.zip")

        if not url:
            continue

        local_zip_path = os.path.join(work_dir, name)

        # ✅ Download the LOCAL Dify file (tool storage)
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(local_zip_path, "wb") as wf:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            wf.write(chunk)
        except Exception as e:
            raise HTTPException(400, f"Local file fetch failed: {str(e)}")

        # ✅ Unzip
        unzip_dir = os.path.join(work_dir, "unzipped")
        os.makedirs(unzip_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(local_zip_path, "r") as z:
                z.extractall(unzip_dir)
        except Exception as e:
            raise HTTPException(400, f"Unzip failed: {str(e)}")

        # ✅ Collect images
        for root, _, fs in os.walk(unzip_dir):
            for img in fs:
                if img.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    all_images.append(os.path.join(root, img))

    return {
        "folder": work_dir,
        "count": len(all_images),
        "files_array": all_images
    }
