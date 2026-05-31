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
