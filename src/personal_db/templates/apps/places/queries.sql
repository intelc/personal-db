-- name: settings
SELECT key, value
FROM app_places_settings
ORDER BY key

-- name: aliases
SELECT place_name, alias, hidden, updated_at
FROM app_places_aliases
ORDER BY hidden DESC, lower(place_name)

-- name: visible_aliases
SELECT place_name, alias, hidden
FROM app_places_aliases

-- name: daily_locations_recent
SELECT date, place_name, visits
FROM daily_locations
WHERE date >= :cutoff
ORDER BY date DESC, visits DESC
LIMIT :limit

-- name: raw_location_bounds
SELECT min(ts) AS first_ts,
       max(ts) AS last_ts,
       count(*) AS points,
       count(DISTINCT date(ts, 'localtime')) AS days
FROM raw_locations

-- name: place_count
SELECT count(DISTINCT coalesce(g.place_name, '(unlabeled)')) AS places
FROM raw_locations r
LEFT JOIN geocoded_locations g ON g.source_id = r.id

-- name: hourly_rhythm
SELECT cast(strftime('%w', r.ts, 'localtime') AS INTEGER) AS weekday,
       cast(strftime('%H', r.ts, 'localtime') AS INTEGER) AS hour,
       count(*) AS points
FROM raw_locations r
WHERE r.ts >= :cutoff
GROUP BY weekday, hour
ORDER BY weekday, hour

-- name: top_places
SELECT coalesce(g.place_name, '(unlabeled)') AS place_name,
       count(*) AS points,
       count(DISTINCT date(r.ts, 'localtime')) AS days,
       min(r.ts) AS first_seen,
       max(r.ts) AS last_seen
FROM raw_locations r
LEFT JOIN geocoded_locations g ON g.source_id = r.id
WHERE r.ts >= :cutoff
GROUP BY place_name
ORDER BY points DESC
LIMIT :limit

-- name: recent_points
SELECT r.id,
       r.ts,
       r.lat,
       r.lon,
       coalesce(g.place_name, '(unlabeled)') AS place_name
FROM raw_locations r
LEFT JOIN geocoded_locations g ON g.source_id = r.id
WHERE r.ts >= :cutoff
ORDER BY r.ts ASC
LIMIT :limit

-- name: timeline_groups
SELECT date(r.ts, 'localtime') AS day,
       coalesce(g.place_name, '(unlabeled)') AS place_name,
       min(r.ts) AS arrived_at,
       max(r.ts) AS left_at,
       count(*) AS points
FROM raw_locations r
LEFT JOIN geocoded_locations g ON g.source_id = r.id
WHERE r.ts >= :cutoff
GROUP BY day, place_name
ORDER BY day DESC, arrived_at DESC
LIMIT :limit
