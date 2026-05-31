# Places App

Purpose: make personal location history useful as a local personal app. The app is a surface over mobile-export-derived location tables; it should not own ingestion or geocoding.

Data contract:
- Read canonical tracker/mart tables when present: `location_points`, `raw_locations`, `geocoded_locations`, and `daily_locations`.
- Store only app presentation state in `app_places_settings` and `app_places_aliases`.
- Do not write to raw location or geocoded tables from app actions.
- Render maps with Leaflet and OpenStreetMap, matching the old location tracker visualizations.
- Because this is a local personal app, exact coordinates may render in the browser. Do not reintroduce blurred or relative map stand-ins by default.

Rendering contract:
- App views should return exactly one `personal_db.ui.components.page(...)`.
- Pass page tabs through that `page(..., nav=...)` call.
- Do not add another app title/tab wrapper around the returned HTML; `app_page.html` is only the outer container.

Presentation rules:
- Hidden aliases must be filtered from map, timeline, rhythm, and top-place views.
- Empty states should mention mobile export dependency without implying cloud sync.

Validation:
- `.venv/bin/python -m pytest tests/unit/test_apps.py -q`
- `.venv/bin/python -m pytest tests/integration/test_cli_app.py -q`
