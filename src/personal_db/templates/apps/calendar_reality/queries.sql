-- name: overview_counts
SELECT count(*) AS blocks,
       sum(planned_minutes) AS planned_minutes,
       sum(actual_minutes) AS actual_minutes,
       sum(CASE WHEN reality_label = 'focused' THEN 1 ELSE 0 END) AS focused_blocks,
       sum(CASE WHEN reality_label = 'fragmented' THEN 1 ELSE 0 END) AS fragmented_blocks,
       avg(fragmentation_score) AS avg_fragmentation
FROM calendar_reality_blocks
WHERE start_at >= :cutoff
  AND reality_label != 'calendar_only'

-- name: label_counts
SELECT reality_label, count(*) AS n
FROM calendar_reality_blocks
WHERE start_at >= :cutoff
  AND reality_label != 'calendar_only'
GROUP BY reality_label
ORDER BY n DESC

-- name: daily_rows
SELECT date,
       count(*) AS blocks,
       sum(planned_minutes) AS planned_minutes,
       sum(actual_minutes) AS actual_minutes,
       sum(CASE WHEN reality_label = 'focused' THEN 1 ELSE 0 END) AS focused,
       sum(CASE WHEN reality_label = 'fragmented' THEN 1 ELSE 0 END) AS fragmented
FROM calendar_reality_blocks
WHERE start_at >= :cutoff
  AND reality_label != 'calendar_only'
GROUP BY date
ORDER BY date

-- name: recent_blocks
SELECT date,
       title,
       calendar_title,
       start_at,
       end_at,
       planned_minutes,
       actual_minutes,
       screen_time_minutes,
       mosspath_events,
       chrome_visits,
       app_count,
       domain_count,
       reality_label,
       fragmentation_score,
       top_apps_json,
       top_domains_json,
       projects_json
FROM calendar_reality_blocks
WHERE start_at >= :cutoff
  AND reality_label != 'calendar_only'
ORDER BY start_at DESC
LIMIT :limit

-- name: calendar_rows
SELECT coalesce(nullif(calendar_title, ''), 'Mac Calendar') AS calendar_title,
       count(*) AS blocks,
       sum(planned_minutes) AS planned_minutes,
       sum(actual_minutes) AS actual_minutes,
       sum(CASE WHEN reality_label = 'focused' THEN 1 ELSE 0 END) AS focused,
       sum(CASE WHEN reality_label = 'fragmented' THEN 1 ELSE 0 END) AS fragmented,
       round(avg(fragmentation_score), 3) AS avg_fragmentation
FROM calendar_reality_blocks
WHERE start_at >= :cutoff
  AND reality_label != 'calendar_only'
GROUP BY coalesce(nullif(calendar_title, ''), 'Mac Calendar')
ORDER BY planned_minutes DESC
LIMIT :limit

-- name: event_bounds
SELECT (SELECT count(*) FROM calendar_events) AS imported_events,
       count(*) AS analyzed_blocks,
       min(start_at) AS first_start,
       max(start_at) AS last_start
FROM calendar_reality_blocks
WHERE start_at >= :cutoff
  AND reality_label != 'calendar_only'
