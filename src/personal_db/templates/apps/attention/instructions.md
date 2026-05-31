# Attention App

Purpose: make notification data useful by focusing on impact, not raw counts.

Keep these contracts:
- Raw notification ownership stays in the `notifications` tracker.
- Impact inference stays materialized in `notification_impacts`.
- Views should treat notification text as optional because the tracker redacts it by default.

Validation:
- `.venv/bin/python -m pytest tests/unit/test_apps.py tests/unit/test_notifications_tracker.py -q`
