# CONTRACT (spine) — seat-schema

## Plain-language summary

This is the shared data definition for the entire system. What a seat looks like, what a
detection looks like, what a seat's occupancy state looks like — defined once here, used
everywhere. ROI writes seats, inference produces detections, occupancy computes state,
persistence records it, dashboard displays it. **No module may redefine these types.**

## Why it's separate

This is where pure-module boundaries break (spec E1). Four modules share this schema.
Isolating it here with strict change rules keeps "module isolation" honest.

## Type definitions (schema_version = 2)

```python
from dataclasses import dataclass
from enum import Enum

class Status(str, Enum):
    EMPTY = "empty"          # no person, no belongings
    OCCUPIED = "occupied"    # person confirmed at seat
    AWAY = "away"            # person left, belongings remain — timer running
    FLAGGED = "flagged"      # away too long, potential illegal occupation
    UNKNOWN = "unknown"      # bad frames / no data — do NOT treat as EMPTY

@dataclass(frozen=True)
class Seat:
    id: str                  # stable unique ID, e.g. "A", "F2-A07"
    label: str               # human-readable name
    zone: str                # area grouping for dashboard
    roi_polygon: list[tuple[float, float]]  # normalized 0~1 image coordinates

@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 normalized 0~1
    confidence: float
    cls: str                 # COCO class name: "person", "laptop", "bottle", etc.
    frame_ts: float          # frame timestamp (seconds, monotonic)

@dataclass(frozen=True)
class SeatState:
    seat_id: str
    status: Status
    since_ts: float          # when current status started
    last_update_ts: float    # last frame evaluated
    confidence: float        # judgment confidence this round
    belongings: tuple[str, ...]   # detected belonging classes, e.g. ("laptop", "bottle")
    person_left_ts: float | None  # timestamp when person last departed (for away timer)
```

## Invariants

- `seat_id` must correspond to an existing `Seat.id`.
- `status = UNKNOWN` is valid. It must not be written to history as EMPTY.
- `since_ts <= last_update_ts`.
- `person_left_ts` is set when transitioning to AWAY, cleared when transitioning to OCCUPIED or EMPTY.
- `belongings` is empty when status is EMPTY or OCCUPIED (person present = not "unattended").

## Belonging classes (COCO80 subset)

`laptop`, `backpack`, `book`, `handbag`, `suitcase`, `cell phone`, `bottle`

## Change procedure (AI may only propose, never merge)

1. Edit the type definitions in this file
2. Bump `schema_version`
3. Write an append-only SQLite migration
4. Human sign-off required
