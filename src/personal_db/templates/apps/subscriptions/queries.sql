-- name: overview_counts
SELECT COUNT(*) AS subscriptions,
       SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_subscriptions,
       SUM(COALESCE(monthly_avg_amount, latest_amount, 0)) AS latest_monthly_like_cost,
       AVG(confidence) AS avg_confidence
FROM subscription_entities

-- name: utilization_counts
SELECT utilization_label,
       COUNT(*) AS periods,
       SUM(cost) AS cost,
       SUM(usage_minutes) AS usage_minutes
FROM subscription_utilization_periods
WHERE period_start >= date('now', '-' || :days || ' days')
GROUP BY utilization_label
ORDER BY cost DESC

-- name: latest_periods
WITH ranked AS (
  SELECT p.*,
         e.label,
         e.status,
         e.cadence,
         ROW_NUMBER() OVER (
           PARTITION BY p.subscription_id
           ORDER BY p.period_start DESC
         ) AS rn
  FROM subscription_utilization_periods p
  JOIN subscription_entities e ON e.subscription_id = p.subscription_id
)
SELECT subscription_id,
       label,
       status,
       cadence,
       period_start,
       period_end,
       cost,
       usage_minutes,
       active_days,
       event_count,
       cost_per_hour,
       cost_per_active_day,
       coverage_ratio,
       utilization_label,
       evidence_json
FROM ranked
WHERE rn = 1
ORDER BY status, usage_minutes ASC, cost DESC
LIMIT :limit

-- name: subscription_rows
SELECT e.subscription_id,
       e.label,
       e.status,
       e.cadence,
       e.typical_amount,
       e.amount_min,
       e.amount_max,
       e.expected_day,
       e.charge_count,
       e.first_charge_date,
       e.last_charge_date,
       e.next_expected_date,
       e.avg_amount,
       e.monthly_avg_amount,
       e.latest_amount,
       e.confidence,
       (
         SELECT c2.merchant
         FROM subscription_charges c2
         WHERE c2.subscription_id = e.subscription_id
         ORDER BY c2.date DESC
         LIMIT 1
       ) AS latest_merchant,
       (
         SELECT GROUP_CONCAT(date, ', ')
         FROM (
           SELECT c3.date
           FROM subscription_charges c3
           WHERE c3.subscription_id = e.subscription_id
           ORDER BY c3.date DESC
           LIMIT 8
         )
       ) AS recent_charge_dates,
       COALESCE(SUM(p.usage_minutes), 0) AS usage_minutes,
       COALESCE(SUM(p.active_days), 0) AS active_days,
       COALESCE(SUM(p.event_count), 0) AS event_count
FROM subscription_entities e
LEFT JOIN subscription_utilization_periods p
  ON p.subscription_id = e.subscription_id
 AND p.period_start >= date('now', '-' || :days || ' days')
GROUP BY e.subscription_id
ORDER BY e.status, e.latest_amount DESC, e.label
LIMIT :limit

-- name: recent_charges
SELECT c.date,
       e.label,
       c.merchant,
       c.amount,
       c.category_source,
       c.match_reason
FROM subscription_charges c
JOIN subscription_entities e ON e.subscription_id = c.subscription_id
ORDER BY c.date DESC, e.label
LIMIT :limit

-- name: recent_evidence
SELECT u.started_at,
       e.label,
       u.source,
       u.minutes,
       u.event_count,
       u.app_name,
       u.bundle_id,
       u.domain,
       u.title,
       u.confidence,
       u.reason
FROM subscription_usage_evidence u
JOIN subscription_entities e ON e.subscription_id = u.subscription_id
ORDER BY u.started_at DESC
LIMIT :limit

-- name: match_rules
SELECT subscription_id,
       merchant_pattern,
       label,
       domain_pattern,
       app_pattern,
       bundle_id,
       enabled,
       source,
       updated_at
FROM subscription_match_rules
ORDER BY enabled DESC, source, merchant_pattern, label
