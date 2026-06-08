# SPEC — Library Seat Occupancy Monitor

- **Status:** draft
- **Schema version:** 2
- **ESP32-CAM endpoint:** `http://172.20.10.3/capture`

---

## 1. Thesis (falsifiable)

A fixed ESP32-CAM sends periodic frames to a Flask backend. YOLOv8 detects persons and
belongings; static ROI polygons map detections to seats. The system reports real-time seat
occupancy, detects unattended belongings after a person leaves, and flags potential illegal
occupation when belongings are left beyond a configurable time threshold.

**What could make this wrong:** single fixed-angle camera + YOLO detection + static ROI cannot
reliably distinguish "person stepped away briefly" from "person left for good" — especially
when belongings look similar to background clutter or when multiple people share a table.
If per-seat accuracy falls below ~85%, the flagging feature generates too many false alerts
and nobody trusts the dashboard.

---

## 2. Elephants

**E1 — Schema is spine, owned by no module.**
ROI writes it, occupancy mutates it, persistence records it, dashboard reads it.
If any module redefines it locally, they drift. Isolated in `shared/seat-schema.contract.md`.

**E2 — Firmware-to-backend link is unreliable (WiFi, power, lighting).**
Frames arrive irregularly; some are bad (blur, overexposure). Occupancy must use hysteresis —
single-frame flips are forbidden. A dropped frame must never flip OCCUPIED to EMPTY.

**E3 — "Detected in ROI" != "seat is occupied."**
Bags on chairs, passers-by clipping ROI edges, shared tables — these are accuracy killers.
Product value lives in the occupancy logic, not in YOLO.

**E4 — Time-based transitions require monotonic timestamps.**
AWAY->FLAGGED is a wall-clock transition. Frame timestamps must be monotonically increasing
(or at least non-decreasing). Clock skew or replay-mode synthetic timestamps must not break
the timer logic. The occupancy engine takes `now` as an explicit parameter, never calls
`datetime.now()` internally.

---

## 3. Architecture

```
                        +-----------------------------+
                        |  shared/ seat-schema (spine) |
                        |  Seat / Detection / SeatState |
                        +------^--------------^--------+
                               | reads         | reads/writes
 +----------+  frames  +--------+  +----------+
 | firmware | -------> | ingest |->| inference |
 | ESP32CAM |  (HTTP,  +--------+  +-----+----+
 +----------+  unreliable E2)           | detections
                                        v
 +----------+  ROI defs     +---------------------+
 |   roi    | ------------> |     occupancy       |  <- product value (E3)
 | annotate |               | hysteresis / 5-state |
 +----------+               +----+----------+-----+
                                 | SeatState  | SeatState
                                 v            v
                          +-----------+ +-----------+
                          |persistence| | dashboard |
                          |  SQLite   | |  Flask UI |
                          +-----------+ +-----------+
```

Arrows = data flow. Spine = shared state layer, separate from computation.

---

## 4. Module map

| Module      | Responsibility                        | Owns                          | Reads              | Contract                          |
|-------------|---------------------------------------|-------------------------------|---------------------|-----------------------------------|
| firmware    | Capture frames, WiFi reconnect, HTTP  | Capture freq, reconnect logic | —                   | (hardware)                        |
| ingest      | Receive frames, drop bad ones, queue  | Endpoint format, validation   | —                   | `contracts/ingest.contract.md`    |
| inference   | YOLOv8 -> Detection list              | Model version, conf threshold | Detection (schema)  | (follows schema)                  |
| roi         | Annotate / store seat polygons        | ROI editor, storage format    | Seat (schema)       | (follows schema)                  |
| occupancy   | Detections + ROI -> seat state        | Hysteresis, 5-state machine   | Schema, ROI         | `contracts/occupancy.contract.md` |
| persistence | Record occupancy events in SQLite     | DB schema, migrations         | SeatState (schema)  | (follows schema)                  |
| dashboard   | Real-time UI + config controls        | UI layout, API shape          | SeatState (schema)  | `contracts/dashboard.contract.md` |

---

## 5. Cross-cutting (the spine)

Seat/occupancy schema is shared by 4+ modules. Lives in `shared/seat-schema.contract.md`.
Change procedure: edit contract -> bump schema_version -> append-only SQLite migration -> human sign-off.
AI proposes, never merges.

---

## 6. Research gates

| #  | Question                                                     | Why it matters                    | How to resolve                      | Blocks           |
|----|--------------------------------------------------------------|-----------------------------------|-------------------------------------|-------------------|
| Q1 | Can one camera's FOV cover the target seats without occlusion? | Can't detect what you can't see  | Physical mount, capture ~50 frames  | ROI tool          |
| Q2 | YOLOv8 person detection accuracy at this angle/lighting?     | Occupancy trusts inference output | Hand-label captured frames, measure | Occupancy tuning  |
| Q3 | Can we distinguish "belongings on seat" from background?     | False AWAY/FLAGGED = noise        | Test with real belongings scenarios  | AWAY logic        |
| Q4 | Is T_flag=60min a useful default for this library?           | Too short = false flags, too long = useless | Observe real departure patterns | Dashboard default |

---

## 7. Phases

| Phase        | Goal                                          | Exit criteria                                    |
|--------------|-----------------------------------------------|--------------------------------------------------|
| P0 Research  | Resolve Q1-Q4                                 | Measured numbers, go/no-go decision              |
| P1 Offline   | Replay mode: full pipeline on recorded frames | Contract tests green, known sequence = expected  |
| P2 Single    | One camera, one table, live                   | Accuracy >= 85% per seat, stable over days       |
| P3 Scale     | Multiple tables / cameras                     | Multi-camera states don't drift                  |
