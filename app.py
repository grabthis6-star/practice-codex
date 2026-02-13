import os
import uuid
import threading
from difflib import SequenceMatcher

import cv2
import pytesseract
from flask import (
    Flask,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB

JOBS = {}
JOBS_LOCK = threading.Lock()
THUMB_TIMESTAMPS = [0, 5, 10, 20, 30, 40]


def _extract_frame(video_path: str, timestamp_sec: float):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0, timestamp_sec * 1000))
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def _video_duration(video_path: str):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    if fps <= 0:
        return 0
    return total_frames / fps


def _build_thumbnail_list(job_id: str, video_path: str):
    duration = _video_duration(video_path)
    timestamps = [t for t in THUMB_TIMESTAMPS if t <= duration]
    if not timestamps:
        timestamps = [0]
    if len(timestamps) < 6 and duration > 0:
        step = max(duration / 6, 1)
        for i in range(6):
            candidate = round(i * step)
            if candidate <= duration and candidate not in timestamps:
                timestamps.append(candidate)
    timestamps = sorted(timestamps)[:6]

    thumbs = []
    for idx, ts in enumerate(timestamps):
        frame = _extract_frame(video_path, ts)
        if frame is None:
            continue
        thumb_name = f"thumb_{idx}_{int(ts)}.jpg"
        thumb_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, thumb_name)
        cv2.imwrite(thumb_path, frame)
        thumbs.append({"timestamp": ts, "path": f"uploads/{job_id}/{thumb_name}"})
    return thumbs


def _normalize_text(text: str):
    return " ".join(text.strip().split())


def _dedupe_lines(lines):
    results = []
    for line in lines:
        normalized = _normalize_text(line)
        if not normalized:
            continue
        if any(SequenceMatcher(None, normalized, existing).ratio() >= 0.88 for existing in results):
            continue
        results.append(normalized)
    return results


def _ocr_worker(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["status"] = "processing"
        job["result_text"] = ""

    video_path = job["video_path"]
    roi = job.get("roi")
    if not roi:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "ROI가 설정되지 않았습니다."
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "영상 파일을 열 수 없습니다."
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = int(total_frames / fps) if fps > 0 else 0

    x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
    lines = []

    for sec in range(0, max(duration, 1) + 1):
        with JOBS_LOCK:
            if JOBS[job_id].get("cancel_requested"):
                JOBS[job_id]["status"] = "cancelled"
                cap.release()
                return

        cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
        ok, frame = cap.read()
        if not ok:
            continue

        h_frame, w_frame = frame.shape[:2]
        x2, y2 = min(x + w, w_frame), min(y + h, h_frame)
        x1, y1 = max(0, x), max(0, y)
        if x2 <= x1 or y2 <= y1:
            continue

        roi_img = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
        denoise = cv2.GaussianBlur(gray, (3, 3), 0)
        proc = cv2.threshold(denoise, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

        text = pytesseract.image_to_string(proc, lang="kor+eng", config="--psm 6")
        text = _normalize_text(text)
        if text:
            lines.append(text)

    cap.release()

    deduped = _dedupe_lines(lines)
    result_text = "\n".join(deduped)
    result_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, "result.txt")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(result_text)

    with JOBS_LOCK:
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["result_text"] = result_text
        JOBS[job_id]["result_file"] = result_path


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/", methods=["GET"])
def index():
    job_id = request.args.get("job")
    job = JOBS.get(job_id) if job_id else None
    return render_template("index.html", job=job, job_id=job_id)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("video")
    if not file or file.filename == "":
        return redirect(url_for("index"))

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(app.config["UPLOAD_FOLDER"], job_id)
    os.makedirs(job_dir, exist_ok=True)

    video_path = os.path.join(job_dir, "input.mp4")
    file.save(video_path)

    thumbs = _build_thumbnail_list(job_id, video_path)
    JOBS[job_id] = {
        "video_path": video_path,
        "thumbnails": thumbs,
        "selected_timestamp": None,
        "selected_frame": None,
        "roi": None,
        "status": "uploaded",
        "result_text": "",
        "cancel_requested": False,
    }
    return redirect(url_for("index", job=job_id))


@app.route("/select_frame", methods=["POST"])
def select_frame():
    job_id = request.form.get("job_id")
    timestamp = float(request.form.get("timestamp", 0))
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))

    frame = _extract_frame(job["video_path"], timestamp)
    if frame is None:
        return redirect(url_for("index", job=job_id))

    frame_name = "selected_frame.jpg"
    frame_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, frame_name)
    cv2.imwrite(frame_path, frame)

    job["selected_timestamp"] = timestamp
    job["selected_frame"] = f"uploads/{job_id}/{frame_name}"
    job["roi"] = None
    job["status"] = "frame_selected"

    return redirect(url_for("index", job=job_id))


@app.route("/set_roi", methods=["POST"])
def set_roi():
    job_id = request.form.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))

    x = int(float(request.form.get("x", 0)))
    y = int(float(request.form.get("y", 0)))
    w = int(float(request.form.get("w", 0)))
    h = int(float(request.form.get("h", 0)))

    if w <= 0 or h <= 0:
        return redirect(url_for("index", job=job_id))

    job["roi"] = {"x": x, "y": y, "w": w, "h": h}
    job["status"] = "roi_set"
    return redirect(url_for("index", job=job_id))


@app.route("/start_ocr", methods=["POST"])
def start_ocr():
    job_id = request.form.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))

    job["cancel_requested"] = False
    thread = threading.Thread(target=_ocr_worker, args=(job_id,), daemon=True)
    thread.start()
    return redirect(url_for("index", job=job_id))


@app.route("/reset_roi", methods=["POST"])
def reset_roi():
    job_id = request.form.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))

    job["cancel_requested"] = True
    job["roi"] = None
    job["status"] = "frame_selected" if job.get("selected_frame") else "uploaded"
    return redirect(url_for("index", job=job_id))


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("result_file"):
        return redirect(url_for("index", job=job_id))
    return send_file(job["result_file"], as_attachment=True, download_name="ocr_result.txt")


if __name__ == "__main__":
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.run(debug=True, host="0.0.0.0", port=5000)
