# Finance App

Purpose: make self and parent-managed finances reviewable without pushing app workflow back into source trackers.

Keep these contracts:
- Read finance mart tables through named queries in `queries.sql`.
- Keep Plaid, Monarch, and finance tracker ownership in trackers/marts.
- Use `views.py` for page composition and `personal_db.ui.components` for reusable UI.
- Parent-managed accounts must stay visually distinct from self-owned finances.

Validation:
- `.venv/bin/python -m pytest tests/unit/test_apps.py -q`
