"""
YOLO Detection Web App — with Seat ROI Annotation
==================================================
- YOLOv8 預訓練模型偵測
- 座位 ROI（多邊形）標註工具
- 用「人 bbox ∩ 座位 polygon ÷ 座位面積」判定座位佔用
- 把座位狀態疊在 YOLO 結果影像上
"""

import base64
import csv
import io
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests as http_requests
from flask import Flask, jsonify, redirect, render_template, request, send_file, send_from_directory
from PIL import Image
from ultralytics import YOLO

# ---------- 路徑 / 設定 ----------
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

ROI_FILE = DATA_DIR / "rois.json"
REF_IMG_FILE = DATA_DIR / "reference.jpg"
DB_FILE = DATA_DIR / "history.db"

ALLOWED_EXTS = {"jpg", "jpeg", "png", "bmp", "webp"}
MAX_FILE_MB = 16

AVAILABLE_MODELS = {
    "nano":   "yolov8n.pt",
    "small":  "yolov8s.pt",
    "medium": "yolov8m.pt",
    "large":  "yolov8l.pt",
}

LIBRARY_FOCUS = {"person", "chair", "couch", "dining table", "laptop", "book",
                 "backpack", "bottle"}
BELONGING_CLASSES = {"laptop", "backpack", "book", "handbag", "suitcase",
                     "cell phone", "bottle"}
DEFAULT_OCCUPANCY_THRESHOLD = 0.15

# ---------- Occupancy engine (contract-conformant) ----------
from shared.seat_schema import Seat as SchemaSeat, Detection as SchemaDetection, Status
from occupancy.engine import OccupancyEngine

CONFIG_FILE = DATA_DIR / "config.json"

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"T_flag": 3600}

def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

_config = load_config()
occupancy_engine = OccupancyEngine(T_flag=_config.get("T_flag", 3600))


# ---------- 模型快取 ----------
_model_cache: dict[str, YOLO] = {}

def get_model(name: str = "nano") -> YOLO:
    if name not in AVAILABLE_MODELS:
        name = "nano"
    if name not in _model_cache:
        print(f"[YOLO] Loading {AVAILABLE_MODELS[name]} ...")
        _model_cache[name] = YOLO(AVAILABLE_MODELS[name])
    return _model_cache[name]

get_model("nano")  # 預載

# ---------- ROI 儲存 ----------
def load_rois() -> dict[str, Any]:
    if not ROI_FILE.exists():
        return {"image_size": None, "seats": [], "occupancy_threshold": DEFAULT_OCCUPANCY_THRESHOLD}
    try:
        with open(ROI_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("seats", [])
        data.setdefault("occupancy_threshold", DEFAULT_OCCUPANCY_THRESHOLD)
        return data
    except Exception as e:
        print(f"[ROI] load failed: {e}")
        return {"image_size": None, "seats": [], "occupancy_threshold": DEFAULT_OCCUPANCY_THRESHOLD}

def save_rois(data: dict[str, Any]) -> None:
    with open(ROI_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------- SQLite 歷史記錄 ----------
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model TEXT,
            num_detections INTEGER,
            persons INTEGER,
            total_seats INTEGER,
            occupied INTEGER,
            vacant INTEGER,
            occ_rate REAL,
            result_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_det_ts ON detections(ts);

        CREATE TABLE IF NOT EXISTS seat_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            seat_id TEXT NOT NULL,
            seat_label TEXT,
            occupied INTEGER,
            coverage REAL
        );
        CREATE INDEX IF NOT EXISTS idx_se_ts ON seat_events(ts);
        CREATE INDEX IF NOT EXISTS idx_se_seat ON seat_events(seat_id, ts);
        """)

init_db()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def log_detection_to_db(*, model, num_detections, persons, total_seats,
                         occupied, vacant, occ_rate, result_id, seat_status):
    """寫入一筆推論紀錄 + 對應的座位事件。"""
    ts = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO detections
               (ts, model, num_detections, persons, total_seats,
                occupied, vacant, occ_rate, result_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, model, num_detections, persons, total_seats,
             occupied, vacant, occ_rate, result_id),
        )
        det_id = cur.lastrowid
        if seat_status:
            conn.executemany(
                """INSERT INTO seat_events
                   (detection_id, ts, seat_id, seat_label, occupied, coverage)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(det_id, ts, s["id"], s.get("label", s["id"]),
                  1 if s["occupied"] else 0, s["coverage"])
                 for s in seat_status],
            )

def auto_bucket_minutes(start_dt: datetime, end_dt: datetime) -> int:
    hours = (end_dt - start_dt).total_seconds() / 3600
    if hours <= 2:    return 1
    if hours <= 24:   return 5
    if hours <= 168:  return 30
    return 60

def parse_time_range():
    """從 query string 解析 start/end，預設過去 24 小時。"""
    end_str = request.args.get("end")
    start_str = request.args.get("start")
    end_dt = datetime.fromisoformat(end_str) if end_str else datetime.now()
    start_dt = datetime.fromisoformat(start_str) if start_str else (end_dt - timedelta(hours=24))
    return start_dt, end_dt


# ---------- ROI 縮放（推論影像尺寸 ≠ 參考影像尺寸時） ----------
def scaled_rois(rois: dict[str, Any], target_w: int, target_h: int) -> dict[str, Any]:
    ref = rois.get("image_size") or {}
    ref_w, ref_h = ref.get("w"), ref.get("h")
    if not ref_w or not ref_h or (ref_w == target_w and ref_h == target_h):
        return rois
    sx, sy = target_w / ref_w, target_h / ref_h
    seats = []
    for s in rois.get("seats", []):
        seats.append({**s, "polygon": [[p[0] * sx, p[1] * sy] for p in s["polygon"]]})
    return {**rois, "seats": seats, "image_size": {"w": target_w, "h": target_h}}

# ---------- 座位佔用計算（使用 OccupancyEngine） ----------
def compute_seat_status(detections, rois, image_shape):
    """
    Convert pixel-space YOLO detections + pixel ROIs into normalized coordinates,
    feed into OccupancyEngine, return status list for the API.
    """
    h, w = image_shape[:2]
    now = time.time()

    # Convert YOLO detections to normalized schema Detection objects
    schema_dets = []
    for d in detections:
        x1, y1, x2, y2 = d["box"]
        schema_dets.append(SchemaDetection(
            bbox=(x1 / w, y1 / h, x2 / w, y2 / h),
            confidence=d["confidence"],
            cls=d["class"],
            frame_ts=now,
        ))

    # Convert pixel ROI seats to normalized schema Seat objects
    ref = rois.get("image_size") or {}
    ref_w = ref.get("w", w)
    ref_h = ref.get("h", h)
    schema_seats = []
    for seat in rois.get("seats", []):
        poly = seat.get("polygon", [])
        if len(poly) < 3:
            continue
        norm_poly = [(p[0] / ref_w, p[1] / ref_h) for p in poly]
        schema_seats.append(SchemaSeat(
            id=seat["id"],
            label=seat.get("label", seat["id"]),
            zone=seat.get("zone", "main"),
            roi_polygon=norm_poly,
        ))

    if not schema_seats:
        return []

    # Run the engine
    seat_states = occupancy_engine.update(schema_dets, schema_seats, now)

    # Convert back to dict format for API response
    status_list = []
    for ss in seat_states:
        away_seconds = None
        if ss.person_left_ts is not None and ss.status in (Status.AWAY, Status.FLAGGED):
            away_seconds = round(now - ss.person_left_ts)
        status_list.append({
            "id": ss.seat_id,
            "label": next((s.get("label", s["id"]) for s in rois.get("seats", [])
                          if s["id"] == ss.seat_id), ss.seat_id),
            "occupied": ss.status in (Status.OCCUPIED, Status.AWAY, Status.FLAGGED),
            "has_person": ss.status == Status.OCCUPIED,
            "coverage": round(ss.confidence, 4),
            "matched_class": "person" if ss.status == Status.OCCUPIED else None,
            "person_confidence": round(ss.confidence, 4),
            "belongings": list(ss.belongings),
            "state": ss.status.value,
            "away_seconds": away_seconds,
        })
    return status_list

# ---------- 座位疊圖繪製 ----------
def draw_seat_overlay(image_bgr, rois, seat_status):
    """在已標註的影像上疊加座位多邊形，依佔用狀態著色。"""
    if not rois.get("seats"):
        return image_bgr

    status_by_id = {s["id"]: s for s in seat_status}
    overlay = image_bgr.copy()

    # 第一遍：填色（透明）
    for seat in rois["seats"]:
        poly_np = np.array(seat["polygon"], dtype=np.int32)
        if len(poly_np) < 3:
            continue
        occ = status_by_id.get(seat["id"], {}).get("occupied", False)
        color = (94, 92, 255) if occ else (159, 255, 92)  # BGR: red-ish vs green-ish
        cv2.fillPoly(overlay, [poly_np], color)
    blended = cv2.addWeighted(overlay, 0.30, image_bgr, 0.70, 0)

    # 第二遍：描邊 + 標籤
    for seat in rois["seats"]:
        poly_np = np.array(seat["polygon"], dtype=np.int32)
        if len(poly_np) < 3:
            continue
        st = status_by_id.get(seat["id"], {})
        occ = st.get("occupied", False)
        color = (94, 92, 255) if occ else (159, 255, 92)
        cv2.polylines(blended, [poly_np], isClosed=True, color=color, thickness=2)

        # 標籤放在多邊形重心
        M = cv2.moments(poly_np)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            tag = "OCC" if occ else "VAC"
            text = f"{seat.get('label', seat['id'])} [{tag}]"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(blended,
                          (cx - tw // 2 - 4, cy - th // 2 - 5),
                          (cx + tw // 2 + 4, cy + th // 2 + 5),
                          color, -1)
            cv2.putText(blended, text,
                        (cx - tw // 2, cy + th // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (10, 14, 12), 1, cv2.LINE_AA)
    return blended

# ---------- Auto-detect seats from YOLO chair/table detections ----------
def auto_detect_seats(detections, image_shape):
    """When no ROIs defined, use detected chairs as dynamic seats.
    Returns (status_list, table_count)."""
    h, w = image_shape[:2]
    now = time.time()

    chairs = sorted(
        [d for d in detections if d["class"] in ("chair", "couch")],
        key=lambda d: d["box"][0],  # stable left-to-right order
    )
    tables = [d for d in detections if d["class"] == "dining table"]

    if not chairs:
        return [], len(tables)

    # Convert chair bboxes into Seat objects (padded slightly for person matching)
    schema_seats = []
    for i, chair in enumerate(chairs):
        x1, y1, x2, y2 = chair["box"]
        pad = 0.2
        dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
        nx1 = max(0, x1 - dx) / w
        ny1 = max(0, y1 - dy) / h
        nx2 = min(w, x2 + dx) / w
        ny2 = min(h, y2 + dy) / h
        schema_seats.append(SchemaSeat(
            id=f"auto-{i+1}",
            label=f"Seat {i+1}",
            zone="main",
            roi_polygon=[(nx1, ny1), (nx2, ny1), (nx2, ny2), (nx1, ny2)],
        ))

    # Normalize all detections for the engine
    schema_dets = [
        SchemaDetection(
            bbox=(d["box"][0] / w, d["box"][1] / h, d["box"][2] / w, d["box"][3] / h),
            confidence=d["confidence"], cls=d["class"], frame_ts=now,
        )
        for d in detections
    ]

    seat_states = occupancy_engine.update(schema_dets, schema_seats, now)

    status_list = []
    for ss in seat_states:
        away_sec = None
        if ss.person_left_ts and ss.status in (Status.AWAY, Status.FLAGGED):
            away_sec = round(now - ss.person_left_ts)
        idx = ss.seat_id.split("-")[1]
        status_list.append({
            "id": ss.seat_id,
            "label": f"Seat {idx}",
            "occupied": ss.status in (Status.OCCUPIED, Status.AWAY, Status.FLAGGED),
            "has_person": ss.status == Status.OCCUPIED,
            "coverage": round(ss.confidence, 4),
            "matched_class": "person" if ss.status == Status.OCCUPIED else None,
            "person_confidence": round(ss.confidence, 4),
            "belongings": list(ss.belongings),
            "state": ss.status.value,
            "away_seconds": away_sec,
        })

    return status_list, len(tables)


# ---------- 推論 ----------
RELEVANT_CLASSES = {"person", "laptop", "dining table", "chair", "couch",
                     "backpack", "book", "handbag", "suitcase", "cell phone", "bottle"}

def run_inference(image_bgr, model_name, conf, iou, apply_rois=True):
    model = get_model(model_name)
    t0 = time.perf_counter()
    results = model.predict(source=image_bgr, conf=conf, iou=iou, verbose=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    r = results[0]
    names = r.names

    detections, stats = [], {}
    if r.boxes is not None and len(r.boxes) > 0:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            cls_name = names[cls_id]
            confidence = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            detections.append({
                "class": cls_name,
                "confidence": round(confidence, 4),
                "box": [round(v, 1) for v in xyxy],
            })
            stats[cls_name] = stats.get(cls_name, 0) + 1

    annotated_bgr = image_bgr.copy()
    for d in detections:
        if d["class"] not in RELEVANT_CLASSES:
            continue
        x1, y1, x2, y2 = [int(v) for v in d["box"]]
        color = (0, 255, 0) if d["class"] == "person" else (255, 180, 0)
        cv2.rectangle(annotated_bgr, (x1, y1), (x2, y2), color, 2)
        label = f'{d["class"]} {d["confidence"]:.2f}'
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(annotated_bgr, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(annotated_bgr, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    # 座位狀態
    seat_status = []
    rois_used = None
    table_count = 0
    seat_mode = "none"
    if apply_rois:
        rois = load_rois()
        if rois.get("seats"):
            # ROI mode — use annotated polygons
            seat_mode = "roi"
            h, w = image_bgr.shape[:2]
            rois_scaled = scaled_rois(rois, w, h)
            seat_status = compute_seat_status(detections, rois_scaled, image_bgr.shape)
            annotated_bgr = draw_seat_overlay(annotated_bgr, rois_scaled, seat_status)
            rois_used = rois_scaled
            table_count = stats.get("dining table", 0) or 1
        else:
            # Auto mode — use detected chairs as seats
            seat_mode = "auto"
            seat_status, table_count = auto_detect_seats(detections, image_bgr.shape)

    return annotated_bgr, detections, stats, elapsed_ms, seat_status, rois_used, table_count, seat_mode

# ---------- Flask ----------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTS


def bgr_to_data_url(image_bgr, fmt="jpeg", quality=90) -> str:
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(image_rgb)
    buf = io.BytesIO()
    pil.save(buf, format=fmt.upper(), quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/{fmt};base64,{b64}"


# ---------- Routes: pages ----------
@app.route("/")
def index():
    return redirect("/dashboard")

@app.route("/detect")
def detect_page():
    return render_template("index.html", models=list(AVAILABLE_MODELS.keys()))

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# ---------- Routes: config ----------
@app.route("/api/config/flag-threshold", methods=["GET"])
def api_config_flag_threshold_get():
    return jsonify({"T_flag": occupancy_engine.flag_threshold})


@app.route("/api/config/flag-threshold", methods=["PUT"])
def api_config_flag_threshold_put():
    body = request.get_json(silent=True) or {}
    try:
        val = float(body.get("T_flag", 3600))
    except (TypeError, ValueError):
        return jsonify({"error": "T_flag must be a number"}), 400
    occupancy_engine.set_flag_threshold(val)
    actual = occupancy_engine.flag_threshold
    cfg = load_config()
    cfg["T_flag"] = actual
    save_config(cfg)
    return jsonify({"ok": True, "T_flag": actual})

@app.route("/annotator")
def annotator():
    return render_template("annotator.html")

@app.route("/history")
def history():
    return render_template("history.html")


# ---------- Routes: history API ----------
@app.route("/api/history/summary")
def api_history_summary():
    start_dt, end_dt = parse_time_range()
    s, e = start_dt.isoformat(timespec="seconds"), end_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as samples, AVG(occ_rate) as avg_rate,
                      MAX(occ_rate) as max_rate, MIN(occ_rate) as min_rate,
                      MAX(occupied) as max_occupied,
                      MAX(persons) as max_persons,
                      AVG(persons) as avg_persons
               FROM detections WHERE ts BETWEEN ? AND ?""", (s, e)).fetchone()
        peak = conn.execute(
            """SELECT ts, occ_rate FROM detections
               WHERE ts BETWEEN ? AND ? AND occ_rate IS NOT NULL
               ORDER BY occ_rate DESC, ts LIMIT 1""", (s, e)).fetchone()
        latest = conn.execute(
            """SELECT ts, total_seats, occupied, vacant, occ_rate FROM detections
               ORDER BY id DESC LIMIT 1""").fetchone()
    return jsonify({
        "start": s, "end": e,
        "samples": row["samples"] or 0,
        "avg_rate": round(row["avg_rate"], 4) if row["avg_rate"] is not None else None,
        "max_rate": round(row["max_rate"], 4) if row["max_rate"] is not None else None,
        "min_rate": round(row["min_rate"], 4) if row["min_rate"] is not None else None,
        "max_occupied": row["max_occupied"] or 0,
        "max_persons": row["max_persons"] or 0,
        "avg_persons": round(row["avg_persons"], 2) if row["avg_persons"] else 0,
        "peak": {"ts": peak["ts"], "occ_rate": peak["occ_rate"]} if peak else None,
        "latest": dict(latest) if latest else None,
    })

@app.route("/api/history/timeseries")
def api_history_timeseries():
    start_dt, end_dt = parse_time_range()
    bucket_min = int(request.args.get("bucket_min", auto_bucket_minutes(start_dt, end_dt)))
    s, e = start_dt.isoformat(timespec="seconds"), end_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        # 將每筆 ts 對齊到 N-分鐘桶
        rows = conn.execute(
            f"""SELECT 
                  strftime('%Y-%m-%dT%H:', ts) ||
                    printf('%02d', (CAST(strftime('%M', ts) AS INTEGER) / ?) * ?) ||
                    ':00' as bucket,
                  AVG(occ_rate) as avg_rate,
                  AVG(occupied) as avg_occupied,
                  AVG(persons) as avg_persons,
                  COUNT(*) as samples
               FROM detections
               WHERE ts BETWEEN ? AND ?
               GROUP BY bucket ORDER BY bucket""",
            (bucket_min, bucket_min, s, e)).fetchall()
    return jsonify({
        "bucket_min": bucket_min,
        "start": s, "end": e,
        "data": [{
            "bucket": r["bucket"],
            "avg_rate": round(r["avg_rate"], 4) if r["avg_rate"] is not None else 0,
            "avg_occupied": round(r["avg_occupied"], 2) if r["avg_occupied"] is not None else 0,
            "avg_persons": round(r["avg_persons"], 2) if r["avg_persons"] is not None else 0,
            "samples": r["samples"],
        } for r in rows],
    })

@app.route("/api/history/by-seat")
def api_history_by_seat():
    start_dt, end_dt = parse_time_range()
    s, e = start_dt.isoformat(timespec="seconds"), end_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT seat_id, seat_label,
                      AVG(CAST(occupied AS REAL)) as occ_rate,
                      AVG(coverage) as avg_coverage,
                      COUNT(*) as samples
               FROM seat_events WHERE ts BETWEEN ? AND ?
               GROUP BY seat_id ORDER BY occ_rate DESC""", (s, e)).fetchall()
    return jsonify({
        "data": [{
            "seat_id": r["seat_id"],
            "seat_label": r["seat_label"] or r["seat_id"],
            "occ_rate": round(r["occ_rate"], 4),
            "avg_coverage": round(r["avg_coverage"], 4) if r["avg_coverage"] is not None else 0,
            "samples": r["samples"],
        } for r in rows]
    })

@app.route("/api/history/hourly")
def api_history_hourly():
    start_dt, end_dt = parse_time_range()
    s, e = start_dt.isoformat(timespec="seconds"), end_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT CAST(strftime('%H', ts) AS INTEGER) as hour,
                      AVG(occ_rate) as avg_rate,
                      AVG(persons) as avg_persons,
                      COUNT(*) as samples
               FROM detections WHERE ts BETWEEN ? AND ?
               GROUP BY hour ORDER BY hour""", (s, e)).fetchall()
    by_hour = {r["hour"]: r for r in rows}
    return jsonify({
        "data": [{
            "hour": h,
            "avg_rate": round(by_hour[h]["avg_rate"], 4) if h in by_hour and by_hour[h]["avg_rate"] is not None else 0,
            "avg_persons": round(by_hour[h]["avg_persons"], 2) if h in by_hour and by_hour[h]["avg_persons"] is not None else 0,
            "samples": by_hour[h]["samples"] if h in by_hour else 0,
        } for h in range(24)]
    })

@app.route("/api/history/recent")
def api_history_recent():
    limit = max(1, min(int(request.args.get("limit", 20)), 200))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM detections ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return jsonify({"data": [dict(r) for r in rows]})

@app.route("/api/history/csv")
def api_history_csv():
    start_dt, end_dt = parse_time_range()
    s, e = start_dt.isoformat(timespec="seconds"), end_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.ts, d.model, d.persons, d.total_seats, d.occupied,
                      d.vacant, d.occ_rate, d.result_id
               FROM detections d
               WHERE d.ts BETWEEN ? AND ? ORDER BY d.ts""", (s, e)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ts", "model", "persons", "total_seats", "occupied",
                "vacant", "occupancy_rate", "result_id"])
    for r in rows:
        w.writerow([r[k] for k in ("ts","model","persons","total_seats",
                                     "occupied","vacant","occ_rate","result_id")])
    fname = f"history_{s[:10]}_{e[:10]}.csv"
    return out.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename={fname}",
    }

@app.route("/api/history", methods=["DELETE"])
def api_history_delete():
    before = request.args.get("before")
    with get_db() as conn:
        if before:
            d = conn.execute("DELETE FROM detections WHERE ts < ?", (before,)).rowcount
            sec = conn.execute("DELETE FROM seat_events WHERE ts < ?", (before,)).rowcount
        else:
            d = conn.execute("DELETE FROM detections").rowcount
            sec = conn.execute("DELETE FROM seat_events").rowcount
    return jsonify({"ok": True, "detections_deleted": d, "seat_events_deleted": sec})


# ---------- Routes: detection ----------
@app.route("/api/detect", methods=["POST"])
def api_detect():
    model_name = request.values.get("model", "nano")
    try:
        conf = float(request.values.get("conf", 0.25))
        iou = float(request.values.get("iou", 0.45))
    except ValueError:
        return jsonify({"error": "conf / iou 必須是數字"}), 400

    image_bgr = None
    if "file" in request.files:
        f = request.files["file"]
        if f.filename == "" or not allowed_file(f.filename):
            return jsonify({"error": "不支援的檔案格式"}), 400
        arr = np.frombuffer(f.read(), dtype=np.uint8)
        image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    elif request.is_json:
        body = request.get_json(silent=True) or {}
        data_url = body.get("image", "")
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        try:
            raw = base64.b64decode(data_url)
            arr = np.frombuffer(raw, dtype=np.uint8)
            image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            return jsonify({"error": f"base64 解碼失敗: {e}"}), 400

    if image_bgr is None:
        return jsonify({"error": "沒有收到有效圖片"}), 400

    try:
        annotated_bgr, detections, stats, elapsed_ms, seat_status, rois_used, table_count, seat_mode = run_inference(
            image_bgr, model_name, conf, iou, apply_rois=True
        )
    except Exception as e:
        return jsonify({"error": f"推論失敗: {e}"}), 500

    # 統計
    persons = stats.get("person", 0)
    chairs = stats.get("chair", 0) + stats.get("couch", 0)
    focus_stats = {k: v for k, v in stats.items() if k in LIBRARY_FOCUS}

    # 座位指標
    total_seats = len(seat_status)
    occupied = sum(1 for s in seat_status if s["occupied"])
    vacant = total_seats - occupied
    occupancy_rate = round(occupied / total_seats, 4) if total_seats > 0 else None

    # 儲存標註圖
    result_id = uuid.uuid4().hex[:12]
    cv2.imwrite(str(RESULT_DIR / f"{result_id}.jpg"), annotated_bgr)

    # 寫入歷史 DB
    if total_seats > 0 and request.args.get("nolog") != "1":
        try:
            log_detection_to_db(
                model=model_name,
                num_detections=len(detections),
                persons=persons,
                total_seats=total_seats,
                occupied=occupied,
                vacant=vacant,
                occ_rate=occupancy_rate,
                result_id=result_id,
                seat_status=seat_status,
            )
        except Exception as e:
            print(f"[DB] log failed: {e}")

    return jsonify({
        "result_id": result_id,
        "model": model_name,
        "elapsed_ms": round(elapsed_ms, 1),
        "image_shape": list(image_bgr.shape),
        "num_detections": len(detections),
        "detections": detections,
        "stats": stats,
        "library": {
            "focus_stats": focus_stats,
            "persons": persons,
            "chairs_or_couches": chairs,
        },
        "seats": {
            "defined": total_seats > 0,
            "mode": seat_mode,
            "tables": table_count,
            "total": total_seats,
            "occupied": occupied,
            "vacant": vacant,
            "occupancy_rate": occupancy_rate,
            "threshold": rois_used["occupancy_threshold"] if rois_used else None,
            "status": seat_status,
        },
        "annotated_image": bgr_to_data_url(annotated_bgr),
    })


# ---------- Routes: ESP32-CAM capture + detect ----------
def _is_safe_camera_url(url):
    """Block SSRF: only allow http/https to non-loopback addresses."""
    from urllib.parse import urlparse
    import ipaddress
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        # Block common loopback/metadata names
        blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
                         "metadata.google.internal", "169.254.169.254"}
        if host.lower() in blocked_hosts:
            return False
        # Block loopback & link-local IPs
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_loopback or addr.is_link_local:
                return False
        except ValueError:
            pass  # hostname, not IP — allow
        return True
    except Exception:
        return False


@app.route("/api/capture-detect", methods=["POST"])
def api_capture_detect():
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    if not _is_safe_camera_url(url):
        return jsonify({"error": "URL blocked: only http/https to non-loopback hosts allowed"}), 403

    model_name = body.get("model", "nano")
    try:
        conf = float(body.get("conf", 0.25))
        iou = float(body.get("iou", 0.45))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid conf or iou value"}), 400

    try:
        resp = http_requests.get(url, timeout=8)
        resp.raise_for_status()
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            return jsonify({"error": "Failed to decode image from camera"}), 400
    except http_requests.RequestException as e:
        print(f"[CAMERA] fetch error: {e}")
        return jsonify({"error": "Camera fetch failed — check URL and connectivity"}), 502

    try:
        annotated_bgr, detections, stats, elapsed_ms, seat_status, rois_used, table_count, seat_mode = run_inference(
            image_bgr, model_name, conf, iou, apply_rois=True
        )
    except Exception as e:
        return jsonify({"error": f"Inference failed: {e}"}), 500

    persons = stats.get("person", 0)
    chairs = stats.get("chair", 0) + stats.get("couch", 0)
    focus_stats = {k: v for k, v in stats.items() if k in LIBRARY_FOCUS}
    total_seats = len(seat_status)
    occupied = sum(1 for s in seat_status if s["occupied"])
    vacant = total_seats - occupied
    occupancy_rate = round(occupied / total_seats, 4) if total_seats > 0 else None

    result_id = uuid.uuid4().hex[:12]
    cv2.imwrite(str(RESULT_DIR / f"{result_id}.jpg"), annotated_bgr)

    if total_seats > 0:
        try:
            log_detection_to_db(
                model=model_name, num_detections=len(detections),
                persons=persons, total_seats=total_seats,
                occupied=occupied, vacant=vacant,
                occ_rate=occupancy_rate, result_id=result_id,
                seat_status=seat_status,
            )
        except Exception as e:
            print(f"[DB] log failed: {e}")

    return jsonify({
        "result_id": result_id,
        "model": model_name,
        "elapsed_ms": round(elapsed_ms, 1),
        "image_shape": list(image_bgr.shape),
        "num_detections": len(detections),
        "detections": detections,
        "stats": stats,
        "library": {
            "focus_stats": focus_stats,
            "persons": persons,
            "chairs_or_couches": chairs,
        },
        "seats": {
            "defined": total_seats > 0,
            "mode": seat_mode,
            "tables": table_count,
            "total": total_seats,
            "occupied": occupied,
            "vacant": vacant,
            "occupancy_rate": occupancy_rate,
            "threshold": rois_used["occupancy_threshold"] if rois_used else None,
            "status": seat_status,
        },
        "annotated_image": bgr_to_data_url(annotated_bgr),
    })


# ---------- Routes: ROIs ----------
@app.route("/api/rois", methods=["GET"])
def api_rois_get():
    return jsonify(load_rois())


@app.route("/api/rois", methods=["POST"])
def api_rois_post():
    """
    Body:
    {
      "image_size": {"w": int, "h": int},
      "seats": [{"id": str, "label": str, "polygon": [[x,y],...]}],
      "occupancy_threshold": float (optional)
    }
    """
    body = request.get_json(silent=True) or {}
    seats_in = body.get("seats", [])
    image_size = body.get("image_size")
    if not image_size or not isinstance(image_size, dict):
        return jsonify({"error": "image_size 必填"}), 400

    cleaned = []
    seen_ids = set()
    for i, s in enumerate(seats_in):
        sid = str(s.get("id") or f"S{i+1}").strip() or f"S{i+1}"
        if sid in seen_ids:
            return jsonify({"error": f"重複的座位 id: {sid}"}), 400
        seen_ids.add(sid)
        poly = s.get("polygon") or []
        if not isinstance(poly, list) or len(poly) < 3:
            return jsonify({"error": f"座位 {sid} 多邊形至少 3 點"}), 400
        try:
            poly_clean = [[float(p[0]), float(p[1])] for p in poly]
        except Exception:
            return jsonify({"error": f"座位 {sid} polygon 格式錯誤"}), 400
        cleaned.append({
            "id": sid,
            "label": s.get("label") or sid,
            "polygon": poly_clean,
        })

    thr = body.get("occupancy_threshold", DEFAULT_OCCUPANCY_THRESHOLD)
    try:
        thr = float(thr)
        if not (0 <= thr <= 1):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "occupancy_threshold 必須是 0~1"}), 400

    data = {
        "image_size": {"w": int(image_size["w"]), "h": int(image_size["h"])},
        "seats": cleaned,
        "occupancy_threshold": thr,
    }
    save_rois(data)
    return jsonify({"ok": True, "saved": len(cleaned)})


@app.route("/api/rois", methods=["DELETE"])
def api_rois_delete():
    if ROI_FILE.exists():
        ROI_FILE.unlink()
    return jsonify({"ok": True})


# ---------- Routes: reference image ----------
@app.route("/api/reference", methods=["GET"])
def api_reference_get():
    if not REF_IMG_FILE.exists():
        return jsonify({"error": "no reference image"}), 404
    return send_file(REF_IMG_FILE, mimetype="image/jpeg")


@app.route("/api/reference", methods=["POST"])
def api_reference_post():
    if "file" not in request.files:
        return jsonify({"error": "需要 file 欄位"}), 400
    f = request.files["file"]
    if not allowed_file(f.filename):
        return jsonify({"error": "不支援的檔案格式"}), 400
    arr = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "影像解析失敗"}), 400
    cv2.imwrite(str(REF_IMG_FILE), img)
    h, w = img.shape[:2]
    return jsonify({"ok": True, "size": {"w": w, "h": h}})


@app.route("/results/<filename>")
def serve_result(filename):
    import re
    if not re.match(r'^[a-f0-9]{12}\.jpg$', filename):
        return jsonify({"error": "invalid filename"}), 400
    return send_from_directory(RESULT_DIR, filename)


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": f"檔案超過 {MAX_FILE_MB}MB 上限"}), 413


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5001))
    print("=" * 60)
    print(" YOLO Detection Web App  +  Seat ROI  +  History")
    print(f" Models   : {list(AVAILABLE_MODELS.keys())}")
    print(f" ROI file : {ROI_FILE}")
    print(f" DB file  : {DB_FILE}")
    print(f" Listening: http://{host}:{port}/")
    print(" Set HOST=0.0.0.0 to expose to network")
    print("=" * 60)
    app.run(host=host, port=port, debug=False)
