-- name: impact_counts
SELECT impact, count(*) AS n
FROM notification_impacts
WHERE delivered_at >= :cutoff
GROUP BY impact
ORDER BY n DESC

-- name: source_rows
SELECT coalesce(app_name, bundle_id, '(unknown)') AS source,
       bundle_id,
       count(*) AS notifications,
       sum(CASE WHEN impact = 'ignored' THEN 1 ELSE 0 END) AS ignored,
       sum(CASE WHEN impact = 'glanced' THEN 1 ELSE 0 END) AS glanced,
       sum(CASE WHEN impact = 'batched' THEN 1 ELSE 0 END) AS batched,
       sum(CASE WHEN impact = 'acted_on' THEN 1 ELSE 0 END) AS acted_on,
       sum(CASE WHEN impact = 'derailed' THEN 1 ELSE 0 END) AS derailed,
       round(100.0 * sum(CASE WHEN impact IN ('acted_on', 'derailed') THEN 1 ELSE 0 END) / count(*), 1) AS action_rate,
       round(avg(seconds_to_action), 1) AS avg_seconds_to_action
FROM notification_impacts
WHERE delivered_at >= :cutoff
GROUP BY source, bundle_id
ORDER BY derailed DESC, acted_on DESC, notifications DESC
LIMIT :limit

-- name: recent_events
SELECT n.delivered_at,
       coalesce(n.app_name, n.bundle_id, '(unknown)') AS source,
       i.impact,
       i.confidence,
       i.batch_count,
       i.seconds_to_action,
       i.prior_app_name,
       i.next_app_name,
       i.evidence
FROM notification_impacts i
JOIN notifications_events n ON n.source_record_id = i.notification_id
ORDER BY n.delivered_at DESC
LIMIT :limit

-- name: daily_rows
SELECT date(delivered_at, 'localtime') AS day,
       count(*) AS notifications,
       sum(CASE WHEN impact IN ('acted_on', 'derailed') THEN 1 ELSE 0 END) AS acted,
       sum(CASE WHEN impact = 'derailed' THEN 1 ELSE 0 END) AS derailed,
       sum(CASE WHEN impact = 'ignored' THEN 1 ELSE 0 END) AS ignored
FROM notification_impacts
WHERE delivered_at >= :cutoff
GROUP BY day
ORDER BY day

-- name: hourly_rows
SELECT cast(strftime('%H', delivered_at, 'localtime') AS INTEGER) AS hour,
       count(*) AS notifications,
       sum(CASE WHEN impact IN ('acted_on', 'derailed') THEN 1 ELSE 0 END) AS acted,
       sum(CASE WHEN impact = 'derailed' THEN 1 ELSE 0 END) AS derailed
FROM notification_impacts
WHERE delivered_at >= :cutoff
GROUP BY hour
ORDER BY hour
