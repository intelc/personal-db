# Subscriptions App

Purpose: show subscription spend only after finance has decided a transaction's
canonical category is `Subscriptions`, then compare those billing periods with
observed app/browser usage evidence.

Keep these contracts:
- Do not decide whether a transaction is a subscription here; read
  `finance_categorized_transactions` through the `subscriptions` tracker.
- The finance layer owns `effective_category='Subscriptions'`.
- This app is read-only in the MVP. Future identity/rule editing should write
  governed subscription-layer tables, not finance categories.
- Views should show evidence and coverage; do not overclaim unused subscriptions
  when usage-source coverage is poor.

Validation:
- `.venv/bin/python -m pytest tests/unit/test_subscriptions_tracker.py tests/unit/test_apps.py -q`
