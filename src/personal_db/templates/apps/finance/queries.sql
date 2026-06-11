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
       user_category AS category,
       note,
       updated_at
FROM finance_transaction_user_categories
ORDER BY updated_at DESC

-- name: category_presets
SELECT category
FROM (
  SELECT category FROM finance_categories
  UNION
  SELECT user_category AS category FROM finance_transaction_user_categories
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
       source_category AS category,
       user_category,
       effective_category,
       category_source
FROM finance_categorized_transactions
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

-- name: receipt_enrichment_rows
WITH latest_receipts AS (
  SELECT *
  FROM enrichment_latest
  WHERE enrichment_name = 'finance.transaction_receipt_v1'
    AND input_table = 'finance_transactions'
),
evidence_refs AS (
  SELECT run_id,
         GROUP_CONCAT(ref, ', ') AS evidence_refs
  FROM enrichment_evidence
  GROUP BY run_id
)
SELECT tx.finance_transaction_id,
       tx.date,
       COALESCE(tx.merchant_name, tx.name) AS merchant,
       tx.amount,
       tx.category,
       COALESCE(latest.status, 'missing') AS receipt_status,
       latest.confidence,
       latest.result_summary,
       latest.updated_at,
       json_extract(latest.result_json, '$.decision') AS decision,
       json_extract(latest.result_json, '$.receipt_candidate_count') AS candidate_count,
       json_extract(latest.result_json, '$.candidate_evidence_count') AS evidence_count,
       json_extract(latest.result_json, '$.amount_combination.total') AS combined_total,
       json_extract(latest.result_json, '$.agent_result.receipt_match') AS agent_match,
       json_extract(latest.result_json, '$.agent_result.reasoning') AS reasoning,
       COALESCE(evidence.evidence_refs, '') AS evidence_refs,
       latest.run_id
FROM finance_transactions tx
LEFT JOIN latest_receipts latest
  ON latest.input_id = tx.finance_transaction_id
LEFT JOIN evidence_refs evidence
  ON evidence.run_id = latest.run_id
WHERE tx.pending = 0
  AND tx.amount > 0
ORDER BY
  CASE COALESCE(latest.status, 'missing')
    WHEN 'no_match' THEN 0
    WHEN 'uncertain' THEN 1
    WHEN 'no_context' THEN 2
    WHEN 'skipped' THEN 3
    WHEN 'missing' THEN 4
    WHEN 'enriched' THEN 5
    ELSE 6
  END,
  tx.date DESC,
  tx.finance_transaction_id
LIMIT :limit
