import os
import traceback
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
OCR_INTERVAL_SECONDS = 2
OCR_MAX_SECONDS_DEFAULT = 60


def _preprocess_subtitle_roi(roi_img):
    hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)

    v_channel = hsv[:, :, 2]
    bright_mask = cv2.inRange(v_channel, 180, 255)

    white_mask = cv2.inRange(hsv, (0, 0, 170), (180, 65, 255))
    yellow_mask = cv2.inRange(hsv, (15, 45, 120), (40, 255, 255))

    color_mask = cv2.bitwise_or(white_mask, yellow_mask)
    subtitle_mask = cv2.bitwise_and(color_mask, bright_mask)

    gray = subtitle_mask
    closed = cv2.morphologyEx(
        gray,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=2,
    )

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    cleaned = closed.copy()
    min_area = 25
    for component in range(1, num_labels):
        area = stats[component, cv2.CC_STAT_AREA]
        if area < min_area:
            cleaned[labels == component] = 0

    return cleaned


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
    cap = None
    try:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            job["status"] = "processing"
            job["result_text"] = ""
            job["error"] = ""

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
        if job.get("limit_to_60", True):
            duration = min(duration, OCR_MAX_SECONDS_DEFAULT)

        sample_seconds = list(range(0, max(duration, 1) + 1, OCR_INTERVAL_SECONDS))
        if not sample_seconds:
            sample_seconds = [0]
        total_samples = len(sample_seconds)

        with JOBS_LOCK:
            JOBS[job_id]["progress_current"] = 0
            JOBS[job_id]["progress_total"] = total_samples

        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        lines = []

        for idx, sec in enumerate(sample_seconds, start=1):
            print(f"[OCR][{job_id}] frame {idx}/{total_samples} at {sec}s")
            with JOBS_LOCK:
                current_job = JOBS.get(job_id)
                if not current_job:
                    return
                if current_job.get("cancel_requested"):
                    current_job["status"] = "cancelled"
                    return
                current_job["progress_current"] = idx

            cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
            ok, frame = cap.read()
            if not ok:
                print(f"[OCR][{job_id}] frame read failed at {sec}s")
                continue

            h_frame, w_frame = frame.shape[:2]
            x2, y2 = min(x + w, w_frame), min(y + h, h_frame)
            x1, y1 = max(0, x), max(0, y)
            if x2 <= x1 or y2 <= y1:
                print(f"[OCR][{job_id}] invalid ROI bounds at frame {idx}")
                continue

            roi_img = frame[y1:y2, x1:x2]
            proc = _preprocess_subtitle_roi(roi_img)

            if app.debug:
                debug_name = "debug_preprocessed_roi.jpg"
                debug_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, debug_name)
                cv2.imwrite(debug_path, proc)
                with JOBS_LOCK:
                    if job_id in JOBS:
                        JOBS[job_id]["debug_preprocessed_roi"] = f"uploads/{job_id}/{debug_name}"

            text = pytesseract.image_to_string(proc, lang="kor+eng", config="--psm 4")
            text = _normalize_text(text)
            if text:
                lines.append(text)
                print(f"[OCR][{job_id}] detected text on frame {idx}")

        deduped = _dedupe_lines(lines)
        result_text = "\n".join(deduped)
        result_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, "result.txt")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(result_text)

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result_text"] = result_text
            JOBS[job_id]["result_file"] = result_path
    except Exception as exc:
        print(f"[OCR][{job_id}] worker exception: {exc}")
        print(traceback.format_exc())
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = str(exc)
    finally:
        if cap is not None:
            cap.release()


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
        "limit_to_60": True,
        "progress_current": 0,
        "progress_total": 0,
        "error": "",
        "debug_preprocessed_roi": None,
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
    job["error"] = ""

    return redirect(url_for("index", job=job_id))


@app.route("/set_roi", methods=["POST"])
def set_roi():
    job_id = request.form.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))

    def _to_int_or_zero(value):
        if value in (None, ""):
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    x = _to_int_or_zero(request.form.get("x"))
    y = _to_int_or_zero(request.form.get("y"))
    w = _to_int_or_zero(request.form.get("w"))
    h = _to_int_or_zero(request.form.get("h"))

    if w <= 0 or h <= 0:
        job["error"] = "ROI를 드래그로 지정하세요."
        return redirect(url_for("index", job=job_id))

    job["roi"] = {"x": x, "y": y, "w": w, "h": h}
    job["status"] = "roi_set"
    job["error"] = ""
    return redirect(url_for("index", job=job_id))


@app.route("/start_ocr", methods=["POST"])
def start_ocr():
    job_id = request.form.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("index"))

    job["cancel_requested"] = False
    job["limit_to_60"] = request.form.get("limit_to_60") == "on"
    job["progress_current"] = 0
    job["progress_total"] = 0
    job["error"] = ""
    print(f"[OCR][{job_id}] starting background OCR thread")
    threading.Thread(target=_ocr_worker, args=(job_id,), daemon=True).start()
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
    job["error"] = ""
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
