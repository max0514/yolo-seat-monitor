# CLAUDE.md

## Read these first (in order)
1. `CONSTITUTION.md` — Inviolable rules, cardinal rules, tech stack
2. `seat-monitoring.spec.md` — System spec, elephants (E1–E3), architecture, module map
3. `shared/seat-schema.contract.md` — Shared schema (spine): Seat, Detection, SeatState, Status
4. `contracts/occupancy.contract.md` — Occupancy module contract, hysteresis state machine
5. `tests/test_occupancy_contract.py` — Contract tests (must stay green)

## Quick rules for AI
- **Tech stack is pinned.** Python 3.11 + Flask, YOLOv8, SQLite, Chart.js, pytest. No new deps without human approval.
- **Schema is the spine.** `shared/seat-schema.contract.md` defines Seat/Detection/SeatState. No module may redefine them.
- **Module isolation.** Changes in module X must not alter module Y's public contract.
- **Contract tests must pass.** Never weaken assertions to make tests pass.
- **Hysteresis required (E2).** Single-frame flips are forbidden. Use k_occ/k_emp/T_empty.
- **ROI detection ≠ occupation (E3).** The value lives in occupancy logic, not YOLO.
- **Config in env/config files.** No hardcoded values in source.
- **Replay mode.** Inference/occupancy must work with pre-recorded frames, same code path as live.
- **UNKNOWN is valid.** Bad frames → maintain state, don't flip to EMPTY.

## Cannot do without human approval
- Change `shared/seat-schema.contract.md` or bump schema_version
- Change any module's public contract (function signatures, endpoint formats, return structures)
- Write or modify SQLite migrations
- Add external dependencies
- Change firmware capture frequency or WiFi reconnect logic
