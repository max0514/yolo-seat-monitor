"""
Shared schema — the spine of the system.
Implements types from shared/seat-schema.contract.md (schema_version = 2).

No module may redefine these types. Changes require bumping schema_version,
writing a migration, and human sign-off.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

SCHEMA_VERSION = 2


class Status(str, Enum):
    EMPTY = "empty"
    OCCUPIED = "occupied"
    AWAY = "away"
    FLAGGED = "flagged"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Seat:
    id: str
    label: str
    zone: str
    roi_polygon: list[tuple[float, float]]   # normalized 0~1


@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 normalized 0~1
    confidence: float
    cls: str
    frame_ts: float


@dataclass(frozen=True)
class SeatState:
    seat_id: str
    status: Status
    since_ts: float
    last_update_ts: float
    confidence: float
    belongings: tuple[str, ...]
    person_left_ts: float | None
