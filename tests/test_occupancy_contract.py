"""
occupancy contract tests — executable form of contracts/occupancy.contract.md

These tests lock the behavior a human reads in the contract. The implementation
must conform to them. Assertions must never be weakened to pass.

Run:  pytest tests/test_occupancy_contract.py -v
"""
import pytest

from shared.seat_schema import Seat, Detection, Status
from occupancy.engine import OccupancyEngine


# ---- helpers ---------------------------------------------------------------

SEAT = Seat(
    id="A", label="Seat A", zone="main",
    roi_polygon=[(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)],
)

def person_in_roi(ts: float) -> Detection:
    """Person bbox centered at (0.5, 0.5) — inside SEAT's ROI."""
    return Detection(bbox=(0.45, 0.45, 0.55, 0.55), confidence=0.9,
                     cls="person", frame_ts=ts)

def person_outside_roi(ts: float) -> Detection:
    """Person bbox at top-left corner — outside SEAT's ROI."""
    return Detection(bbox=(0.0, 0.0, 0.1, 0.1), confidence=0.9,
                     cls="person", frame_ts=ts)

def laptop_in_roi(ts: float) -> Detection:
    """Laptop bbox centered at (0.5, 0.5) — inside SEAT's ROI."""
    return Detection(bbox=(0.46, 0.46, 0.54, 0.54), confidence=0.85,
                     cls="laptop", frame_ts=ts)

def status_of(states, seat_id="A"):
    return next(s.status for s in states if s.seat_id == seat_id)

def state_of(states, seat_id="A"):
    return next(s for s in states if s.seat_id == seat_id)


# ---- hysteresis (E2) -------------------------------------------------------

def test_single_positive_frame_does_not_flip_to_occupied():
    """E2: one frame with person is not enough (need k_occ=2 consecutive)."""
    eng = OccupancyEngine()
    states = eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    assert status_of(states) != Status.OCCUPIED


def test_k_consecutive_positive_frames_flips_to_occupied():
    """k_occ=2 consecutive person-in-ROI frames -> OCCUPIED."""
    eng = OccupancyEngine()
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    states = eng.update([person_in_roi(1.0)], [SEAT], now=1.0)
    assert status_of(states) == Status.OCCUPIED


def test_single_dropped_frame_does_not_flip_to_empty():
    """E2 core guarantee: once OCCUPIED, one missing frame must not flip to EMPTY."""
    eng = OccupancyEngine()
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)  # now OCCUPIED
    states = eng.update([], [SEAT], now=2.0)            # dropped frame
    assert status_of(states) == Status.OCCUPIED


# ---- ROI boundary (E3) ----------------------------------------------------

def test_detection_outside_roi_is_ignored():
    """E3: person outside the ROI polygon does not count as occupying the seat."""
    eng = OccupancyEngine()
    eng.update([person_outside_roi(0.0)], [SEAT], now=0.0)
    states = eng.update([person_outside_roi(1.0)], [SEAT], now=1.0)
    assert status_of(states) != Status.OCCUPIED


# ---- AWAY transition -------------------------------------------------------

def test_person_leaves_with_belongings_transitions_to_away():
    """OCCUPIED -> AWAY when person gone but belongings detected (after k_away frames)."""
    eng = OccupancyEngine(k_away=2)
    # Establish OCCUPIED
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)  # OCCUPIED
    # Person leaves, laptop remains — k_away=2 frames
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)
    states = eng.update([laptop_in_roi(3.0)], [SEAT], now=3.0)
    assert status_of(states) == Status.AWAY


def test_away_records_belongings():
    """When AWAY, the SeatState must list detected belonging classes."""
    eng = OccupancyEngine(k_away=2)
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)
    states = eng.update([laptop_in_roi(3.0)], [SEAT], now=3.0)
    s = state_of(states)
    assert "laptop" in s.belongings


def test_away_records_person_left_ts():
    """When AWAY, person_left_ts must be set to when the person was last seen leaving."""
    eng = OccupancyEngine(k_away=2)
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)  # last seen at 1.0
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)
    states = eng.update([laptop_in_roi(3.0)], [SEAT], now=3.0)
    s = state_of(states)
    assert s.person_left_ts is not None


# ---- FLAGGED transition ----------------------------------------------------

def test_away_escalates_to_flagged_after_t_flag():
    """AWAY -> FLAGGED when wall-clock time exceeds T_flag."""
    eng = OccupancyEngine(k_away=1, T_flag=60.0)
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)  # OCCUPIED
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)   # AWAY (k_away=1)
    # Jump forward past T_flag
    states = eng.update([laptop_in_roi(70.0)], [SEAT], now=70.0)
    assert status_of(states) == Status.FLAGGED


def test_flagged_clears_when_person_returns():
    """FLAGGED -> OCCUPIED when person comes back (after k_occ frames)."""
    eng = OccupancyEngine(k_away=1, k_occ=2, T_flag=60.0)
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)
    eng.update([laptop_in_roi(70.0)], [SEAT], now=70.0)  # FLAGGED
    # Person returns
    eng.update([person_in_roi(71.0), laptop_in_roi(71.0)], [SEAT], now=71.0)
    states = eng.update([person_in_roi(72.0), laptop_in_roi(72.0)], [SEAT], now=72.0)
    assert status_of(states) == Status.OCCUPIED


def test_flagged_to_empty_when_belongings_removed():
    """FLAGGED -> EMPTY when belongings are cleared (after k_emp frames, no person)."""
    eng = OccupancyEngine(k_away=1, k_emp=2, T_flag=60.0, T_empty=0.0)
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)
    eng.update([laptop_in_roi(70.0)], [SEAT], now=70.0)  # FLAGGED
    # Belongings removed — k_emp=2 empty frames
    eng.update([], [SEAT], now=71.0)
    states = eng.update([], [SEAT], now=72.0)
    assert status_of(states) == Status.EMPTY


# ---- set_flag_threshold ----------------------------------------------------

def test_set_flag_threshold_changes_t_flag():
    """set_flag_threshold updates T_flag for subsequent evaluations."""
    eng = OccupancyEngine(k_away=1, T_flag=3600.0)
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)
    eng.update([laptop_in_roi(2.0)], [SEAT], now=2.0)  # AWAY

    # At t=100, still AWAY (T_flag=3600 not reached)
    states = eng.update([laptop_in_roi(100.0)], [SEAT], now=100.0)
    assert status_of(states) == Status.AWAY

    # Lower threshold to 60s
    eng.set_flag_threshold(60.0)

    # Now at t=100 (>60s since away started), should escalate
    states = eng.update([laptop_in_roi(101.0)], [SEAT], now=101.0)
    assert status_of(states) == Status.FLAGGED


# ---- determinism (replay) --------------------------------------------------

def test_deterministic_replay():
    """Same input sequence -> same output. Replay mode prerequisite."""
    seq = [([person_in_roi(t)], t) for t in range(5)]

    def run():
        eng = OccupancyEngine()
        return [status_of(eng.update(d, [SEAT], now=t)) for d, t in seq]

    assert run() == run()
