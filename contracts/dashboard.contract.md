# CONTRACT (module) — dashboard

## Plain-language summary

The dashboard is the user-facing window into the system. It shows two things: a live camera
feed with YOLO annotations, and the real-time occupancy state of each seat. It also exposes
one critical control: the flagging threshold (T_flag) — how long belongings can sit unattended
before the system raises an alert. Everything else is display-only.

## Public interface

### Page route

`GET /dashboard` — serves the single-page dashboard HTML.

### API endpoints consumed

| Endpoint               | Method | Purpose                                |
|------------------------|--------|----------------------------------------|
| `/api/capture-detect`  | POST   | Fetch frame from ESP32 + run inference |
| `/api/config/flag-threshold` | GET/PUT | Read/write T_flag (seconds)     |

### API endpoint provided

```
GET  /api/config/flag-threshold  -> { "T_flag": 3600 }
PUT  /api/config/flag-threshold  <- { "T_flag": 1800 }  -> { "ok": true, "T_flag": 1800 }
```

T_flag range: 60 to 86400 seconds (1 minute to 24 hours). Values outside range are clamped.

## UI layout

Two-column layout:
- **Left (main area):** seat status visualization — one visual slot per seat showing state
  (EMPTY/OCCUPIED/AWAY/FLAGGED), away timer, detected belongings, occupancy count banner,
  flag alert banner.
- **Right (sidebar):** live camera feed (annotated), camera URL input, snap/live/stop controls,
  stats grid (persons, occupancy rate, latency, scan count), **T_flag slider**, event log.

### T_flag control

- Slider or input field in the sidebar.
- Range: 1 minute to 24 hours.
- Default: 60 minutes.
- Display format: human-readable (e.g. "1h 30m").
- On change: PUT to `/api/config/flag-threshold`, then the occupancy engine uses the new value
  for subsequent frames. Does not retroactively change already-flagged seats.

### State display per seat

| State    | Visual                                    | Info shown                    |
|----------|-------------------------------------------|-------------------------------|
| EMPTY    | Dimmed silhouette, green badge "VACANT"   | —                             |
| OCCUPIED | Bright silhouette, red badge "OCCUPIED"   | —                             |
| AWAY     | Faded silhouette, amber badge "AWAY"      | Timer (counting up), belongings list |
| FLAGGED  | Flashing silhouette, red badge "FLAGGED"  | Timer, belongings, alert banner |
| UNKNOWN  | Grey silhouette, grey badge "NO SIGNAL"   | —                             |

## Dependencies

- Reads SeatState from `/api/capture-detect` response.
- Reads/writes T_flag via config endpoint.

**Forbidden:** dashboard never computes occupancy logic — it only displays what the backend tells it.

## AI may not change without human review

- The T_flag config endpoint format
- The seat state display mapping (which visual = which state)
