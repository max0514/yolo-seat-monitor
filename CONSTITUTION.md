# CONSTITUTION

> Rules for all contributors (human and AI). Violations invalidate the change.

## Tech stack (pinned, no additions without human approval)

- Firmware: ESP32-CAM (AI Thinker) + Arduino framework
- Backend: Python 3.11 + Flask
- Inference: Ultralytics YOLOv8 (model version recorded in inference config)
- Storage: SQLite
- Frontend charts: Chart.js
- Testing: pytest

## Cardinal rules

1. **Schema is spine.** `shared/seat-schema.contract.md` defines Status, Seat, Detection,
   SeatState. No module may redefine them. To change: edit the contract, bump schema_version,
   write an append-only SQLite migration, get human sign-off.
2. **Module isolation.** A change inside module X must not alter module Y's public contract.
   If it must, stop — mark it as a contract change, wait for human review.
3. **Contract tests stay green.** After any module change, its contract tests must pass.
   Assertions may never be weakened to pass.

## Detectable classes (COCO80 subset)

| Purpose    | Classes                                                              |
|------------|----------------------------------------------------------------------|
| Occupancy  | person                                                               |
| Belongings | laptop, backpack, book, handbag, suitcase, cell phone, bottle        |
| Scene      | chair, couch, dining table                                           |

"water bottle" in user-facing text maps to COCO class `bottle`.

## The 5-state model

Every seat is always in exactly one state: EMPTY, OCCUPIED, AWAY, FLAGGED, or UNKNOWN.
This is the product's core — see `contracts/occupancy.contract.md` for the full state machine.

- **EMPTY** — no person, no belongings.
- **OCCUPIED** — person confirmed at seat.
- **AWAY** — person left, belongings remain. Timer starts.
- **FLAGGED** — away duration exceeded configurable threshold T_flag. Potential illegal occupation.
- **UNKNOWN** — bad/missing frames. Maintain last known state, do not flip to EMPTY.

## Conventions

- Config in env vars or config files, never hardcoded in source.
  **T_flag must be settable from the dashboard UI** (range: 1 min to 24 hours, default: 60 min).
- Error handling: try/except at every external boundary (HTTP, camera, DB) with structured logging.
  Never swallow exceptions. Client-facing errors are generic; details go to server logs.
- Naming follows schema contract exactly — `seat` not `desk`/`spot`, `status` not `state_label`.
- SSRF protection on all server-side HTTP fetches (camera proxy).

## Modes

Inference and occupancy pipelines must support **replay mode**: feed pre-recorded frame sequences
through the same code path as live. This is a testing prerequisite, not optional.

## AI restrictions (propose only, never merge)

- Change `shared/seat-schema.contract.md` or bump schema_version
- Change any module's public contract (function signatures, endpoint formats, return structures)
- Write or modify SQLite migrations
- Add external dependencies
- Change firmware capture frequency or WiFi reconnect logic
