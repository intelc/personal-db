-- name: account_rows
SELECT source,
       account_group,
       owner,
       institution_name,
       account_name,
       subtype,
       current_balance,
       available_balance,
       iso_currency_code,
       as_of
FROM finance_accounts
WHERE (:scope = 'all')
   OR (:scope = 'self' AND LOWER(COALESCE(owner, 'self')) IN ('self', 'me', 'personal'))
   OR (:scope = 'parents' AND LOWER(COALESCE(owner, 'self')) NOT IN ('self', 'me', 'personal'))
ORDER BY institution_name, account_group, source, account_name

-- name: latest_net_worth
SELECT date,
       cash,
       investments,
       credit_card_debt,
       other,
       assets,
       debts,
       net_worth
FROM finance_daily_net_worth
WHERE owner = :owner
ORDER BY date DESC
LIMIT 1

-- name: net_worth_rows
SELECT date,
       cash,
       investments,
       -credit_card_debt AS cards,
       net_worth
FROM finance_daily_net_worth
WHERE owner = :owner
ORDER BY date DESC
LIMIT :limit

-- name: cashflow_rows
SELECT date,
       income,
       spending,
       net,
       parent_draw,
       credit_card_payments,
       internal_transfers,
       txn_count
FROM finance_daily_cashflow
WHERE owner = :owner
ORDER BY date DESC
LIMIT :limit

-- name: burn_rate_transactions
SELECT finance_transaction_id,
       date,
       COALESCE(merchant_name, name) AS merchant,
       amount,
       category,
       owner,
       is_internal_transfer,
       is_credit_card_payment
FROM finance_transactions
WHERE pending = 0
  AND date >= date('now', '-' || :days || ' days')
  AND (
    owner = 'self'
    OR LOWER(COALESCE(merchant_name, '') || ' ' || COALESCE(name, '')) LIKE '%greystar%'
  )
ORDER BY date DESC, finance_transaction_id

-- name: burn_rules
SELECT rule_id,
       rule_key,
       priority,
       label,
       bucket,
       merchant_pattern,
       category_pattern,
       category_match_type,
       flag_name,
       amount_direction,
       min_amount,
       reason,
       source,
       enabled
FROM app_finance_burn_rules
WHERE enabled = 1
ORDER BY priority, rule_id

-- name: burn_overrides
SELECT finance_transaction_id,
       bucket,
       note,
       updated_at
FROM app_finance_burn_overrides
ORDER BY updated_at DESC

-- name: burn_buckets
SELECT bucket,
       label,
       COALESCE(emoji, '') AS emoji,
       sort_order,
       source,
       COALESCE(color, '') AS color
FROM app_finance_burn_buckets
ORDER BY sort_order, LOWER(label)

-- name: holding_rows
SELECT source,
       owner,
       institution_name,
       account_name,
       COALESCE(ticker, security_name, security_id) AS holding,
       quantity,
       value,
       as_of
FROM finance_holdings
WHERE (:scope = 'all')
   OR (:scope = 'self' AND LOWER(COALESCE(owner, 'self')) IN ('self', 'me', 'personal'))
   OR (:scope = 'parents' AND LOWER(COALESCE(owner, 'self')) NOT IN ('self', 'me', 'personal'))
ORDER BY institution_name, account_name, COALESCE(value, 0) DESC
LIMIT :limit

-- name: parent_draw_rows
SELECT finance_transaction_id,
       date,
       source,
       owner,
       institution,
       account_name,
       COALESCE(merchant_name, name) AS merchant,
       amount,
       category
FROM finance_parent_draws
ORDER BY date DESC, institution, account_name
LIMIT :limit

-- name: parent_draw_daily_rows
SELECT date,
       parent_draw
FROM finance_daily_cashflow
WHERE owner = 'all'
  AND parent_draw > 0
ORDER BY date
LIMIT :limit

-- name: review_states
SELECT review_key,
       kind,
       status,
       note,
       updated_at
FROM app_finance_reviews
ORDER BY updated_at DESC

-- name: transaction_category_states
SELECT finance_transaction_id,
       category,
       note,
       updated_at
FROM app_finance_transaction_categories
ORDER BY updated_at DESC

-- name: category_presets
SELECT category
FROM (
  SELECT category FROM app_finance_category_presets
  UNION
  SELECT category FROM app_finance_transaction_categories
)
ORDER BY LOWER(category)

-- name: transaction_category_candidates
SELECT finance_transaction_id,
       date,
       owner,
       source,
       account_group,
       COALESCE(merchant_name, name) AS merchant,
       amount,
       category
FROM finance_transactions
WHERE pending = 0
  AND is_internal_transfer = 0
  AND is_credit_card_payment = 0
ORDER BY date DESC, finance_transaction_id
LIMIT :limit

-- name: recurring_candidates
SELECT COALESCE(merchant_name, name) AS merchant,
       owner,
       COUNT(*) AS txn_count,
       ROUND(AVG(amount), 2) AS avg_amount,
       MIN(date) AS first_seen,
       MAX(date) AS last_seen,
       category
FROM finance_transactions
WHERE pending = 0
  AND is_internal_transfer = 0
  AND is_credit_card_payment = 0
  AND date >= date('now', '-180 days')
GROUP BY LOWER(COALESCE(merchant_name, name)), owner, category
HAVING COUNT(*) >= 3
ORDER BY txn_count DESC, ABS(avg_amount) DESC
LIMIT :limit
