# Calendar Reality App

Purpose: compare planned calendar blocks with observed app/browser/project behavior.

Keep these contracts:
- Calendar event ingestion and derived `calendar_reality_blocks` belong to the `calendar` tracker.
- The app is read-only in the MVP.
- Views should show evidence, not overclaim intent. Labels are heuristics.

Validation:
- `.venv/bin/python -m pytest tests/unit/test_calendar_tracker.py tests/unit/test_apps.py -q`
