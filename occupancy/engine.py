"""
Occupancy engine — implements contracts/occupancy.contract.md.

5-state machine per seat with hysteresis counters and time-based flagging.
Pure computation: no I/O, no side effects beyond internal counters.
Time is always an explicit parameter (never calls datetime.now).
"""
from __future__ import annotations

from shared.seat_schema import Detection, Seat, SeatState, Status

PERSON_CLASS = "person"
BELONGING_CLASSES = frozenset({
    "laptop", "backpack", "book", "handbag", "suitcase", "cell phone", "bottle",
})


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _point_in_polygon(px: float, py: float,
                      polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Per-seat internal tracker (mutable, not exposed)
# ---------------------------------------------------------------------------

class _Tracker:
    __slots__ = (
        "status", "since_ts", "last_update_ts", "confidence",
        "belongings", "person_left_ts",
        "pos_count", "neg_count", "away_count", "last_person_ts",
    )

    def __init__(self) -> None:
        self.status: Status = Status.EMPTY
        self.since_ts: float = 0.0
        self.last_update_ts: float = 0.0
        self.confidence: float = 0.0
        self.belongings: tuple[str, ...] = ()
        self.person_left_ts: float | None = None
        # hysteresis counters
        self.pos_count: int = 0    # consecutive person-in-ROI frames
        self.neg_count: int = 0    # consecutive empty frames (no person, no belongings)
        self.away_count: int = 0   # consecutive away frames (no person, has belongings)
        self.last_person_ts: float | None = None

    def snapshot(self, seat_id: str) -> SeatState:
        return SeatState(
            seat_id=seat_id,
            status=self.status,
            since_ts=self.since_ts,
            last_update_ts=self.last_update_ts,
            confidence=self.confidence,
            belongings=self.belongings,
            person_left_ts=self.person_left_ts,
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class OccupancyEngine:
    """
    Parameters
    ----------
    k_occ : consecutive person-in-ROI frames to confirm OCCUPIED
    k_emp : consecutive no-person + no-belongings frames to confirm EMPTY
    k_away : consecutive no-person + has-belongings frames to confirm AWAY
    T_empty : min seconds since last person before OCCUPIED->EMPTY allowed
    T_flag : seconds in AWAY before escalating to FLAGGED (UI-configurable)
    T_unknown : seconds of no updates before UNKNOWN
    """

    def __init__(self, *, k_occ: int = 2, k_emp: int = 4, k_away: int = 2,
                 T_empty: float = 30.0, T_flag: float = 3600.0,
                 T_unknown: float = 60.0) -> None:
        self._k_occ = k_occ
        self._k_emp = k_emp
        self._k_away = k_away
        self._T_empty = T_empty
        self._T_flag = T_flag
        self._T_unknown = T_unknown
        self._trackers: dict[str, _Tracker] = {}

    # -- public ------------------------------------------------------------

    def set_flag_threshold(self, seconds: float) -> None:
        """Update T_flag at runtime (called when user changes UI slider)."""
        self._T_flag = max(60.0, min(86400.0, float(seconds)))

    @property
    def flag_threshold(self) -> float:
        return self._T_flag

    def update(self, detections: list[Detection], roi_set: list[Seat],
               now: float) -> list[SeatState]:
        """Evaluate one frame. Returns current state for every seat in roi_set."""
        results: list[SeatState] = []
        for seat in roi_set:
            tk = self._ensure(seat.id)
            has_person, belongings = self._classify(seat, detections)
            self._update_counters(tk, has_person, belongings, now)
            self._transition(tk, has_person, belongings, now)
            tk.last_update_ts = now
            results.append(tk.snapshot(seat.id))
        return results

    # -- internals ---------------------------------------------------------

    def _ensure(self, seat_id: str) -> _Tracker:
        if seat_id not in self._trackers:
            self._trackers[seat_id] = _Tracker()
        return self._trackers[seat_id]

    def _classify(self, seat: Seat,
                  detections: list[Detection]) -> tuple[bool, tuple[str, ...]]:
        """Determine has_person and belonging classes for one seat."""
        has_person = False
        found: list[str] = []
        for det in detections:
            cx, cy = _bbox_center(det.bbox)
            if not _point_in_polygon(cx, cy, seat.roi_polygon):
                continue
            if det.cls == PERSON_CLASS:
                has_person = True
            elif det.cls in BELONGING_CLASSES:
                if det.cls not in found:
                    found.append(det.cls)
        return has_person, tuple(sorted(found))

    @staticmethod
    def _update_counters(tk: _Tracker, has_person: bool,
                         belongings: tuple[str, ...], now: float) -> None:
        if has_person:
            tk.pos_count += 1
            tk.neg_count = 0
            tk.away_count = 0
            tk.last_person_ts = now
        elif belongings:
            tk.pos_count = 0
            tk.neg_count = 0
            tk.away_count += 1
        else:
            tk.pos_count = 0
            tk.neg_count += 1
            tk.away_count = 0

    def _transition(self, tk: _Tracker, has_person: bool,
                    belongings: tuple[str, ...], now: float) -> None:
        if tk.status == Status.EMPTY:
            self._from_empty(tk, now)
        elif tk.status == Status.OCCUPIED:
            self._from_occupied(tk, has_person, belongings, now)
        elif tk.status == Status.AWAY:
            self._from_away(tk, belongings, now)
        elif tk.status == Status.FLAGGED:
            self._from_flagged(tk, belongings, now)
        elif tk.status == Status.UNKNOWN:
            self._from_unknown(tk, has_person, belongings, now)

    def _from_empty(self, tk: _Tracker, now: float) -> None:
        if tk.pos_count >= self._k_occ:
            tk.status = Status.OCCUPIED
            tk.since_ts = now
            tk.person_left_ts = None
            tk.belongings = ()
            tk.confidence = 1.0

    def _from_occupied(self, tk: _Tracker, has_person: bool,
                       belongings: tuple[str, ...], now: float) -> None:
        if has_person:
            tk.confidence = 1.0
            tk.belongings = ()
            return
        # person not detected this frame
        if tk.away_count >= self._k_away:
            tk.status = Status.AWAY
            tk.since_ts = now
            tk.person_left_ts = tk.last_person_ts or now
            tk.belongings = belongings
            tk.confidence = 0.8
        elif tk.neg_count >= self._k_emp:
            elapsed = now - (tk.last_person_ts or now)
            if elapsed >= self._T_empty:
                tk.status = Status.EMPTY
                tk.since_ts = now
                tk.person_left_ts = None
                tk.belongings = ()
                tk.confidence = 0.9

    def _from_away(self, tk: _Tracker, belongings: tuple[str, ...],
                   now: float) -> None:
        if belongings:
            tk.belongings = belongings
        if tk.pos_count >= self._k_occ:
            tk.status = Status.OCCUPIED
            tk.since_ts = now
            tk.person_left_ts = None
            tk.belongings = ()
            tk.confidence = 1.0
        elif tk.neg_count >= self._k_emp:
            tk.status = Status.EMPTY
            tk.since_ts = now
            tk.person_left_ts = None
            tk.belongings = ()
            tk.confidence = 0.9
        elif tk.person_left_ts is not None and \
             (now - tk.person_left_ts) >= self._T_flag:
            tk.status = Status.FLAGGED
            tk.since_ts = now
            tk.confidence = 0.9

    def _from_flagged(self, tk: _Tracker, belongings: tuple[str, ...],
                      now: float) -> None:
        if belongings:
            tk.belongings = belongings
        if tk.pos_count >= self._k_occ:
            tk.status = Status.OCCUPIED
            tk.since_ts = now
            tk.person_left_ts = None
            tk.belongings = ()
            tk.confidence = 1.0
        elif tk.neg_count >= self._k_emp:
            tk.status = Status.EMPTY
            tk.since_ts = now
            tk.person_left_ts = None
            tk.belongings = ()
            tk.confidence = 0.9

    def _from_unknown(self, tk: _Tracker, has_person: bool,
                      belongings: tuple[str, ...], now: float) -> None:
        # Resume from UNKNOWN on first credible frame
        if has_person and tk.pos_count >= self._k_occ:
            tk.status = Status.OCCUPIED
            tk.since_ts = now
            tk.confidence = 0.7
        elif belongings:
            tk.status = Status.AWAY
            tk.since_ts = now
            tk.belongings = belongings
            tk.confidence = 0.5
        elif tk.neg_count >= self._k_emp:
            tk.status = Status.EMPTY
            tk.since_ts = now
            tk.confidence = 0.5
