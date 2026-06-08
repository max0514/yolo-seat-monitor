# CONTRACT (module) — occupancy

## Plain-language summary

Takes "what YOLO saw" plus "where each seat is" and decides each seat's state. The key insight:
don't trust a single frame. A person must be seen for multiple consecutive frames to count as
"occupied." They must be gone for even more frames to count as "left." And when belongings
remain after the person leaves, a configurable wall-clock timer runs — if the person doesn't
return within T_flag seconds, the seat is flagged as potential illegal occupation.
**This is where product accuracy lives (E3), not in YOLO.**

## Public interface (changing this requires human review)

```python
class OccupancyEngine:
    def __init__(self, *, k_occ=2, k_emp=4, k_away=2,
                 T_empty=30.0, T_flag=3600.0, T_unknown=60.0):
        """
        k_occ:    consecutive person-in-ROI frames to confirm OCCUPIED
        k_emp:    consecutive no-person-no-belongings frames to confirm EMPTY
        k_away:   consecutive no-person-but-belongings frames to confirm AWAY
        T_empty:  minimum seconds since last person before allowing EMPTY transition
        T_flag:   seconds in AWAY before escalating to FLAGGED (UI-configurable)
        T_unknown: seconds of consecutive bad frames before UNKNOWN
        """

    def update(
        self,
        detections: list[Detection],
        roi_set: list[Seat],
        now: float,
    ) -> list[SeatState]:
        """Pure computation + internal counters. Returns current state for every seat."""

    def set_flag_threshold(self, seconds: float) -> None:
        """Update T_flag at runtime (called when user changes the UI slider)."""
```

Types come from `shared/seat-schema.contract.md`. This module must not redefine them.

## Dependencies (allow-list)

- `shared/seat-schema`: Status, Seat, Detection, SeatState
- `roi_set` provided by caller (from ROI module)

**Forbidden:** no direct DB access, no YOLO calls, no network I/O, no `datetime.now()`.
Time comes in via the `now` parameter — this is what makes replay mode work (E4).

## Core algorithm

### 1. ROI matching

For each seat, determine which detections fall within its ROI polygon.
Default: detection bbox center-point inside polygon. (Upgradable to IoU later.)

Classify per seat:
- `has_person`: any "person" detection overlaps ROI above threshold
- `belongings_found`: list of belonging-class detections overlapping ROI

### 2. State machine (per seat)

```
EMPTY ──(k_occ consecutive has_person)──> OCCUPIED
OCCUPIED ──(k_away consecutive !has_person + belongings)──> AWAY
OCCUPIED ──(k_emp consecutive !has_person + !belongings, >= T_empty since last person)──> EMPTY
AWAY ──(k_occ consecutive has_person)──> OCCUPIED
AWAY ──(wall-clock >= T_flag since person_left_ts)──> FLAGGED
AWAY ──(k_emp consecutive !has_person + !belongings)──> EMPTY
FLAGGED ──(k_occ consecutive has_person)──> OCCUPIED
FLAGGED ──(k_emp consecutive !has_person + !belongings)──> EMPTY
Any ──(>= T_unknown seconds of bad/missing frames)──> UNKNOWN
UNKNOWN ──(first credible frame)──> resume from last known state
```

### 3. Parameters

| Param     | Default | Range        | Description                                        |
|-----------|---------|--------------|----------------------------------------------------|
| k_occ     | 2       | 1-10         | Frames to confirm person present                   |
| k_emp     | 4       | 2-20         | Frames to confirm seat fully vacated               |
| k_away    | 2       | 1-10         | Frames to confirm person left but belongings remain|
| T_empty   | 30s     | 10-300s      | Min seconds before OCCUPIED->EMPTY allowed         |
| T_flag    | 3600s   | 60-86400s    | Seconds in AWAY before FLAGGED (**UI-configurable**)|
| T_unknown | 60s     | 10-600s      | Seconds of bad frames before UNKNOWN               |

**T_flag is the only parameter exposed to the end-user via the dashboard UI.**
All others are developer/config-level tuning.

### 4. Bad frames / no data

When a frame has no credible input (camera error, decode failure):
- Maintain current state, do not flip.
- Decrement confidence.
- After T_unknown consecutive seconds of no credible data: transition to UNKNOWN.
- UNKNOWN -> back to evaluation on next credible frame (do NOT default to EMPTY).

## Guarantees (invariants)

- **Deterministic:** same input sequence + same parameters = same output. Replay works.
- **Pure computation:** no I/O, no side effects beyond internal counters.
- **Single-frame resilience:** one positive or negative frame cannot flip a stable state.
- **Asymmetric by design:** harder to go EMPTY than to go OCCUPIED — because "false empty"
  (student's stuff still there) is worse than "slow to report empty."

## AI may not change without human review

- The `update()` / `set_flag_threshold()` signatures
- The hysteresis semantics (k_occ/k_emp/k_away asymmetry, T_empty minimum delay)
- The 5-state model and transition rules
- Any types from seat-schema

## Contract tests

`tests/test_occupancy_contract.py` — executable form of this contract.
