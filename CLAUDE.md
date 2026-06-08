# CLAUDE.md

## Read these first (in order)
1. `CONSTITUTION.md` — Inviolable rules, tech stack, 5-state model, AI restrictions
2. `seat-monitoring.spec.md` — System spec, elephants (E1-E4), architecture, module map
3. `shared/seat-schema.contract.md` — Shared schema (spine): Status, Seat, Detection, SeatState
4. `contracts/occupancy.contract.md` — 5-state machine, hysteresis, T_flag (UI-configurable)
5. `contracts/dashboard.contract.md` — Dashboard UI contract, T_flag slider, state display
6. `tests/test_occupancy_contract.py` — Contract tests (must stay green)

## Quick rules for AI
- **Tech stack is pinned.** Python 3.11 + Flask, YOLOv8, SQLite, Chart.js, pytest. No new deps without human approval.
- **Schema is the spine.** `shared/seat-schema.contract.md` defines Status/Seat/Detection/SeatState (schema_version=2). No module may redefine them.
- **5-state model.** EMPTY, OCCUPIED, AWAY, FLAGGED, UNKNOWN. See occupancy contract for transitions.
- **Module isolation.** Changes in module X must not alter module Y's public contract.
- **Contract tests must pass.** Never weaken assertions to make tests pass.
- **Hysteresis required (E2).** Single-frame flips are forbidden. Use k_occ/k_emp/k_away/T_empty.
- **ROI detection != occupation (E3).** Product value is in occupancy logic, not YOLO.
- **T_flag is UI-configurable.** Range 60s-86400s, default 3600s. Dashboard exposes a slider.
- **Config in env/config files.** No hardcoded values in source.
- **Replay mode.** Inference/occupancy must work with pre-recorded frames, same code path as live.
- **UNKNOWN is valid.** Bad frames -> maintain state, don't flip to EMPTY.
- **Time via parameter.** OccupancyEngine takes `now` as explicit parameter, never calls datetime.now().

## Cannot do without human approval
- Change `shared/seat-schema.contract.md` or bump schema_version
- Change any module's public contract (function signatures, endpoint formats, return structures)
- Write or modify SQLite migrations
- Add external dependencies
- Change firmware capture frequency or WiFi reconnect logic
