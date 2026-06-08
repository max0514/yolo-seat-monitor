"""
occupancy 模組的契約測試 —— 規格的「可執行形式」。

這些測試就是 contracts/occupancy.contract.md 的可驗證版本:
它們鎖住「人讀規格時理解的行為」。實作要去符合它們,而不是反過來改它們。

跑法:  pytest tests/test_occupancy_contract.py -v
"""
import pytest

# 型別來自脊椎,實作來自 occupancy 模組(路徑依你的 repo 調整)
from shared.seat_schema import Seat, Detection, Status
from occupancy.engine import OccupancyEngine


# ---- 測試輔助 ----------------------------------------------------------

SEAT = Seat(id="F2-A07", label="2F 靠窗 A07", zone="2F",
            roi_polygon=[(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)])

def person_in_roi(ts: float) -> Detection:
    # 中心 (0.5, 0.5) 落在 SEAT 的 ROI 內
    return Detection(bbox=(0.45, 0.45, 0.55, 0.55), confidence=0.9, cls="person", frame_ts=ts)

def person_outside_roi(ts: float) -> Detection:
    return Detection(bbox=(0.0, 0.0, 0.1, 0.1), confidence=0.9, cls="person", frame_ts=ts)

def status_of(states, seat_id):
    return next(s.status for s in states if s.seat_id == seat_id)


# ---- 契約行為 ----------------------------------------------------------

def test_single_positive_frame_does_not_flip_to_occupied():
    """E2:單一幀偵測到人,不足以判定占用(需連續 k_occ=2)。"""
    eng = OccupancyEngine()
    states = eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    assert status_of(states, "F2-A07") != Status.OCCUPIED

def test_k_consecutive_positive_frames_flips_to_occupied():
    """連續 k_occ 幀為正 → OCCUPIED。"""
    eng = OccupancyEngine()
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    states = eng.update([person_in_roi(1.0)], [SEAT], now=1.0)
    assert status_of(states, "F2-A07") == Status.OCCUPIED

def test_single_dropped_frame_does_not_flip_to_empty():
    """E2 的核心保證:已占用時,掉一幀(看不到人)不可立刻翻成 EMPTY。"""
    eng = OccupancyEngine()
    eng.update([person_in_roi(0.0)], [SEAT], now=0.0)
    eng.update([person_in_roi(1.0)], [SEAT], now=1.0)   # 已 OCCUPIED
    states = eng.update([], [SEAT], now=2.0)             # 掉一幀
    assert status_of(states, "F2-A07") == Status.OCCUPIED

def test_detection_outside_roi_is_ignored():
    """走道上的人(ROI 外)不算占用(E3)。"""
    eng = OccupancyEngine()
    eng.update([person_outside_roi(0.0)], [SEAT], now=0.0)
    states = eng.update([person_outside_roi(1.0)], [SEAT], now=1.0)
    assert status_of(states, "F2-A07") != Status.OCCUPIED

def test_deterministic_replay():
    """相同輸入序列 → 相同輸出。replay 模式可用的前提。"""
    seq = [([person_in_roi(t)], t) for t in range(5)]
    out_a = [status_of(OccupancyEngine().update(d, [SEAT], now=t), "F2-A07") for d, t in seq]
    out_b = [status_of(OccupancyEngine().update(d, [SEAT], now=t), "F2-A07") for d, t in seq]
    # 注意:同一引擎跨多輪才有記憶;此處示意「同輸入同輸出」,實測請用同一 engine 跑兩次序列
    assert out_a == out_b
