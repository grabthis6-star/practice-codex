import os
import traceback
import uuid
import threading
from difflib import SequenceMatcher

import cv2
import numpy as np
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

    binary = cv2.morphologyEx(
        subtitle_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=2,
    )

    roi_h, roi_w = binary.shape[:2]
    roi_area = max(roi_h * roi_w, 1)

    min_area = max(12, int(roi_area * 0.00008))
    max_area = int(roi_area * 0.08)
    min_box_h = max(8, int(roi_h * 0.015))
    max_box_h = max(min_box_h + 1, int(roi_h * 0.35))
    min_box_w = 2
    max_box_w = max(min_box_w + 1, int(roi_w * 0.65))

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    filtered_mask = np.zeros_like(binary)

    for component in range(1, num_labels):
        width = stats[component, cv2.CC_STAT_WIDTH]
        height = stats[component, cv2.CC_STAT_HEIGHT]
        area = stats[component, cv2.CC_STAT_AREA]

        if area < min_area or area > max_area:
            continue
        if width < min_box_w or width > max_box_w:
            continue
        if height < min_box_h or height > max_box_h:
            continue

        aspect_ratio = width / max(height, 1)
        fill_ratio = area / max(width * height, 1)
        if aspect_ratio > 14 or aspect_ratio < 0.06:
            continue
        if fill_ratio < 0.08 or fill_ratio > 0.95:
            continue

        filtered_mask[labels == component] = 255

    return binary, filtered_mask


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


def _line_char_ratios(line: str):
    total = len(line)
    if total == 0:
        return {"kor": 0.0, "latin_num": 0.0, "special": 1.0}

    kor_count = sum(1 for ch in line if "가" <= ch <= "힣")
    latin_num_count = sum(1 for ch in line if ch.isascii() and ch.isalnum())
    special_count = sum(1 for ch in line if not ch.isspace() and not (("가" <= ch <= "힣") or (ch.isascii() and ch.isalnum())))
    return {
        "kor": kor_count / total,
        "latin_num": latin_num_count / total,
        "special": special_count / total,
    }


def _filter_subtitle_lines(raw_lines, korean_only=False, include_english=False):
    cleaned = []
    for raw_line in raw_lines:
        line = _normalize_text(raw_line)
        if not line or len(line) <= 3:
            continue

        ratios = _line_char_ratios(line)
        min_kor_ratio = 0.3
        max_latin_num_ratio = 0.55
        max_special_ratio = 0.45
        if korean_only:
            min_kor_ratio = 0.45
            max_latin_num_ratio = 0.35
            max_special_ratio = 0.35
        elif include_english:
            min_kor_ratio = 0.2
            max_latin_num_ratio = 0.75

        if ratios["kor"] < min_kor_ratio:
            continue
        if ratios["latin_num"] > max_latin_num_ratio:
            continue
        if ratios["special"] > max_special_ratio:
            continue

        cleaned.append(line)

    return cleaned


def _dedupe_lines(lines):
    results = []
    seen = set()
    for line in lines:
        normalized = _normalize_text(line)
        if not normalized:
            continue
        if normalized in seen:
            continue
        if any(SequenceMatcher(None, normalized, existing).ratio() >= 0.88 for existing in results):
            continue
        results.append(normalized)
        seen.add(normalized)
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
        raw_lines = []
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
            binary_before_filter, proc = _preprocess_subtitle_roi(roi_img)

            if app.debug:
                before_name = "debug_preprocessed_roi_before_filter.jpg"
                after_name = "debug_preprocessed_roi_after_filter.jpg"
                before_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, before_name)
                after_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, after_name)
                cv2.imwrite(before_path, binary_before_filter)
                cv2.imwrite(after_path, proc)
                with JOBS_LOCK:
                    if job_id in JOBS:
                        JOBS[job_id]["debug_preprocessed_roi_before"] = f"uploads/{job_id}/{before_name}"
                        JOBS[job_id]["debug_preprocessed_roi_after"] = f"uploads/{job_id}/{after_name}"

            psm_mode = int(job.get("psm_mode", 6))
            if psm_mode not in (6, 7):
                psm_mode = 6
            include_english = bool(job.get("include_english", False))
            lang = "kor+eng" if include_english else "kor"
            text = pytesseract.image_to_string(proc, lang=lang, config=f"--oem 1 --psm {psm_mode}")
            frame_lines = [ln for ln in text.splitlines() if ln and ln.strip()]
            raw_lines.extend(_normalize_text(ln) for ln in frame_lines if _normalize_text(ln))
            filtered_lines = _filter_subtitle_lines(
                frame_lines,
                korean_only=bool(job.get("korean_only", False)),
                include_english=include_english,
            )
            if filtered_lines:
                lines.extend(filtered_lines)
                print(f"[OCR][{job_id}] detected text on frame {idx}")

        raw_count = len(raw_lines)
        deduped = _dedupe_lines(lines)
        cleaned_count = len(deduped)
        result_text = "\n".join(deduped)
        result_path = os.path.join(app.config["UPLOAD_FOLDER"], job_id, "result.txt")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(result_text)

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result_text"] = result_text
            JOBS[job_id]["result_file"] = result_path
            JOBS[job_id]["raw_line_count"] = raw_count
            JOBS[job_id]["cleaned_line_count"] = cleaned_count
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
        "debug_preprocessed_roi_before": None,
        "debug_preprocessed_roi_after": None,
        "psm_mode": 6,
        "korean_only": False,
        "include_english": False,
        "raw_line_count": 0,
        "cleaned_line_count": 0,
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
    psm_mode = request.form.get("psm_mode", "6")
    try:
        parsed_psm = int(psm_mode)
    except ValueError:
        parsed_psm = 6
    job["psm_mode"] = parsed_psm if parsed_psm in (6, 7) else 6
    job["korean_only"] = request.form.get("korean_only") == "on"
    job["include_english"] = request.form.get("include_english") == "on"
    job["progress_current"] = 0
    job["progress_total"] = 0
    job["raw_line_count"] = 0
    job["cleaned_line_count"] = 0
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
