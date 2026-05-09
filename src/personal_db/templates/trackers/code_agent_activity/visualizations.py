"""Visualizations for the code_agent_activity tracker."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars, stacked_vertical_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(cfg.db_path)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def render_runtime_heatmap(cfg: Config) -> str:
    """7-day x 24-hour heatmap of agent_running seconds (local time)."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            """
            SELECT start_ts, duration_seconds
            FROM code_agent_intervals
            WHERE state = 'agent_running' AND start_ts >= ?
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">code_agent_intervals not synced yet</p>'
    finally:
        con.close()

    if not rows:
        return '<p class="meta">no agent_running intervals in the last 7 days</p>'

    # Accumulate seconds per (weekday, hour) in local time
    grid_data: dict[tuple[int, int], float] = defaultdict(float)
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["start_ts"].replace("Z", "+00:00"))
        except ValueError:
            continue
        local = ts.astimezone()
        grid_data[(local.weekday(), local.hour)] += row["duration_seconds"] or 0.0

    today = datetime.now().astimezone().date()
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    # Build grid: rows=days, cols=hours
    grid = [
        [grid_data.get((d.weekday(), h), 0) or None for h in range(24)]
        for d in days
    ]
    row_labels = [d.strftime("%a %m-%d") for d in days]
    col_labels = [f"{h:02d}" for h in range(24)]
    return (
        '<p class="meta">agent_running seconds per hour, last 7 days · local time · '
        "darker = more agent activity</p>"
        + heatmap(grid, row_labels, col_labels, base_color=(37, 99, 235))
    )


def render_state_breakdown(cfg: Config) -> str:
    """Daily stacked bar: agent_running vs awaiting_user minutes, last 7 days."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            """
            SELECT date(start_ts, 'localtime') AS d, state, SUM(duration_seconds) AS total
            FROM code_agent_intervals
            WHERE start_ts >= ?
            GROUP BY d, state
            ORDER BY d
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">code_agent_intervals not synced yet</p>'
    finally:
        con.close()

    if not rows:
        return '<p class="meta">no intervals in the last 7 days</p>'

    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"agent_running": 0.0, "awaiting_user": 0.0})
    for row in rows:
        by_day[row["d"]][row["state"]] = row["total"] or 0.0

    bars = [
        {
            "label": d,
            "segments": [
                ("agent running", "#2563eb", by_day[d]["agent_running"] / 60),
                ("awaiting user", "#94a3b8", by_day[d]["awaiting_user"] / 60),
            ],
        }
        for d in sorted(by_day)
    ]
    return (
        '<p class="meta">minutes per day, last 7 days · '
        '<span style="color:#2563eb">■</span> agent running &nbsp; '
        '<span style="color:#94a3b8">■</span> awaiting user</p>'
        + stacked_vertical_bars(bars, show_every_nth_label=1, value_unit="m")
    )


def render_prompt_cadence(cfg: Config) -> str:
    """Horizontal bars: distribution of inter-prompt gap durations, last 7 days."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            """
            SELECT agent, session_id, timestamp
            FROM code_agent_events
            WHERE event_type = 'prompt_submitted' AND timestamp >= ?
            ORDER BY agent, session_id, timestamp
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">code_agent_events not synced yet</p>'
    finally:
        con.close()

    if not rows:
        return '<p class="meta">no prompt_submitted events in the last 7 days</p>'

    buckets: dict[str, int] = {"< 10 s": 0, "10 s – 1 m": 0, "1 – 10 m": 0, "> 10 m": 0}
    last_per_session: dict[tuple[str, str], datetime] = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        except ValueError:
            continue
        key = (row["agent"], row["session_id"])
        if key in last_per_session:
            gap = (ts - last_per_session[key]).total_seconds()
            if gap < 10:
                buckets["< 10 s"] += 1
            elif gap < 60:
                buckets["10 s – 1 m"] += 1
            elif gap < 600:
                buckets["1 – 10 m"] += 1
            else:
                buckets["> 10 m"] += 1
        last_per_session[key] = ts

    total = sum(buckets.values())
    if total == 0:
        return '<p class="meta">only one prompt per session — no inter-prompt gaps to measure</p>'

    items = [(label, float(count)) for label, count in buckets.items()]
    return (
        f'<p class="meta">inter-prompt gap distribution · {total} gaps in last 7 days</p>'
        + horizontal_bars(items, value_fmt=lambda v: str(int(v)))
    )


def render_engagement(cfg: Config) -> str:
    """Per agent_running interval: keystrokes produced while the agent ran.

    Joins against mosspath_lite_events. Sums key_count across all event types
    that carry keystrokes (input_batch is rare; most typing surfaces as the
    derived semantic events composed_text / submitted_text / pasted_text /
    copied_text). Renders a graceful fallback if mosspath_lite isn't
    installed.
    """
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'

    try:
        has_mosspath = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mosspath_lite_events'"
        ).fetchone()
    except sqlite3.OperationalError:
        con.close()
        return '<p class="meta">database not initialized yet</p>'

    if not has_mosspath:
        con.close()
        return (
            "<p>Install the <strong>mosspath_lite</strong> tracker to see engagement data — "
            "this view joins agent runtime intervals against user keystroke batches.</p>"
        )

    try:
        # Inference: at the moment a user submits a prompt to an agent
        # (event_type='prompt_submitted'), they MUST be focused on the agent's
        # window — otherwise their Enter wouldn't have reached the agent. We
        # correlate each prompt_submitted with the nearest mosspath_lite
        # 'submitted_text' event (within ±3s) and take the most-common
        # bundle_id across all of a session's prompts as the session's app.
        #
        # Three remote-detection paths:
        #   1. Claude Code: hook writer captured $SSH_CONNECTION → i.is_remote=1.
        #   2. Codex CLI heuristic: a session with ≥3 prompts and 0 paired
        #      mosspath events is treated as remote — the user's keystrokes
        #      are happening on a client machine mosspath can't see.
        #   3. Otherwise (some prompts paired or session too short to be sure):
        #      apply normal app inference.
        rows = con.execute(
            """
            WITH paired AS (
                SELECT e.agent, e.session_id,
                       m.bundle_id, m.app_name,
                       COUNT(*) AS n
                FROM code_agent_events e
                JOIN mosspath_lite_events m
                  ON m.action_type = 'submitted_text'
                 AND ABS((julianday(m.timestamp) - julianday(e.timestamp)) * 86400) <= 3
                 AND m.bundle_id IS NOT NULL
                WHERE e.event_type = 'prompt_submitted'
                GROUP BY e.agent, e.session_id, m.bundle_id, m.app_name
            ),
            session_app AS (
                SELECT agent, session_id, bundle_id, app_name
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY agent, session_id ORDER BY n DESC
                           ) AS rn
                    FROM paired
                )
                WHERE rn = 1
            ),
            session_meta AS (
                -- Codex stamps these on the session_meta row of the rollout
                -- file. originator='codex-tui' means interactive CLI (a real
                -- person typing into a terminal) — exactly the invocation
                -- pattern an SSH user would use. 'Codex Desktop' / 'codex_exec'
                -- mean IDE / programmatic, where SSH is unlikely.
                SELECT agent, session_id,
                       json_extract(raw, '$.payload.originator') AS originator,
                       json_extract(raw, '$.payload.source') AS source
                FROM code_agent_events
                WHERE event_type = 'session_start'
            ),
            session_pair_stats AS (
                SELECT s.agent, s.session_id,
                       s.prompt_count,
                       COALESCE(p.paired_count, 0) AS paired_count,
                       sm.originator AS originator
                FROM (
                    SELECT agent, session_id, COUNT(*) AS prompt_count
                    FROM code_agent_events
                    WHERE event_type = 'prompt_submitted'
                    GROUP BY agent, session_id
                ) s
                LEFT JOIN (
                    SELECT e.agent, e.session_id, COUNT(*) AS paired_count
                    FROM code_agent_events e
                    JOIN session_app sa
                      ON sa.agent = e.agent AND sa.session_id = e.session_id
                    WHERE e.event_type = 'prompt_submitted'
                    GROUP BY e.agent, e.session_id
                ) p ON p.agent = s.agent AND p.session_id = s.session_id
                LEFT JOIN session_meta sm
                  ON sm.agent = s.agent AND sm.session_id = s.session_id
            )
            SELECT i.agent,
                   i.session_id,
                   strftime('%m-%d %H:%M', i.start_ts, 'localtime') AS run_when,
                   CAST(SUM(i.duration_seconds) AS INTEGER) AS total_run_sec,
                   sa.app_name AS agent_app,
                   i.is_remote AS hook_remote,
                   sps.prompt_count AS prompt_count,
                   sps.paired_count AS paired_count,
                   sps.originator AS originator,
                   COALESCE(SUM(CASE
                       WHEN m.bundle_id = sa.bundle_id THEN m.key_count
                       ELSE 0 END), 0) AS keys_in_agent,
                   COALESCE(SUM(CASE
                       WHEN sa.bundle_id IS NULL OR m.bundle_id != sa.bundle_id
                       THEN m.key_count ELSE 0 END), 0) AS keys_other
            FROM code_agent_intervals i
            LEFT JOIN session_app sa
              ON sa.agent = i.agent AND sa.session_id = i.session_id
            LEFT JOIN session_pair_stats sps
              ON sps.agent = i.agent AND sps.session_id = i.session_id
            LEFT JOIN mosspath_lite_events m
              ON datetime(m.timestamp) >= datetime(i.start_ts)
             AND datetime(m.timestamp) <  datetime(i.end_ts)
             AND m.key_count > 0
            WHERE i.state = 'agent_running'
              AND datetime(i.start_ts) >= datetime('now', '-7 days')
            GROUP BY i.agent, i.session_id, i.start_ts
            ORDER BY i.start_ts DESC
            LIMIT 50
            """,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return f'<p class="meta">query error: {escape(str(exc))}</p>'
    finally:
        con.close()

    if not rows:
        return '<p class="meta">no agent_running intervals in the last 7 days</p>'

    def _classify_remote(row: sqlite3.Row) -> str:
        """Return 'remote' (definite, from SSH hook), 'remote?' (heuristic),
        or '' (local / unknown).

        For Codex, the reliable signal is the rollout's `originator` field:
          - 'codex-tui' = interactive CLI invocation (terminal). Locally these
            should pair with mosspath events; if they don't pair, the user
            was almost certainly SSH'd.
          - 'Codex Desktop' = launched via the desktop app / IDE integration.
            If these don't pair, mosspath was broken — not SSH.
          - 'codex_exec' = programmatic (`codex exec` calls). No human at the
            keyboard, engagement metric is moot.
        """
        if row["hook_remote"]:
            return "remote"
        originator = row["originator"]
        prompts = row["prompt_count"] or 0
        paired = row["paired_count"] or 0
        if originator == "codex-tui" and prompts >= 1 and paired == 0:
            return "remote?"
        return ""

    # Roll up by agent: split engaged vs elsewhere vs remote (unmeasurable)
    by_agent: dict[str, dict[str, float]] = defaultdict(
        lambda: {"in_agent": 0.0, "other": 0.0, "remote_sec": 0.0}
    )
    for row in rows:
        if _classify_remote(row):
            by_agent[row["agent"]]["remote_sec"] += row["total_run_sec"] or 0
        else:
            by_agent[row["agent"]]["in_agent"] += row["keys_in_agent"] or 0
            by_agent[row["agent"]]["other"] += row["keys_other"] or 0

    in_agent_items = [
        (f"{agent} (in agent app)", by_agent[agent]["in_agent"])
        for agent in sorted(by_agent, key=lambda a: by_agent[a]["in_agent"], reverse=True)
    ]
    other_items = [
        (f"{agent} (elsewhere)", by_agent[agent]["other"])
        for agent in sorted(by_agent, key=lambda a: by_agent[a]["other"], reverse=True)
    ]

    # Detail table
    detail_rows = []
    for row in rows:
        dur = row["total_run_sec"] or 0
        remote_label = _classify_remote(row)
        if remote_label:
            agent_app_display = remote_label
            in_keys_display = "—"
            other_keys_display = "—"
            rate_display = "—"
        else:
            in_keys = row["keys_in_agent"] or 0
            other_keys = row["keys_other"] or 0
            total = in_keys + other_keys
            agent_app_display = row["agent_app"] or "(unknown)"
            in_keys_display = str(in_keys)
            other_keys_display = str(other_keys)
            rate_display = f"{total / dur:.2f}" if dur else "—"
        detail_rows.append(
            "<tr>"
            f'<td>{escape(row["run_when"] or "")}</td>'
            f'<td>{escape(row["agent"])}</td>'
            f'<td>{escape(row["session_id"][:8])}</td>'
            f'<td>{escape(agent_app_display)}</td>'
            f"<td>{dur}</td>"
            f"<td>{in_keys_display}</td>"
            f"<td>{other_keys_display}</td>"
            f"<td>{rate_display}</td>"
            "</tr>"
        )

    # Remote-runtime summary line
    remote_total = sum(s["remote_sec"] for s in by_agent.values())
    remote_summary = ""
    if remote_total > 0:
        breakdown = ", ".join(
            f"{a}: {int(by_agent[a]['remote_sec'] // 60)}m"
            for a in sorted(by_agent)
            if by_agent[a]["remote_sec"] > 0
        )
        remote_summary = (
            '<p class="meta">'
            f"agent runtime over SSH (engagement unmeasurable): {breakdown}. "
            'Marked "remote" (Claude SSH hook) or "remote?" (Codex heuristic: '
            "≥3 prompts, 0 paired with local keystrokes).</p>"
        )

    return (
        '<p class="meta">keystrokes typed while agent was running, split by where '
        "they were typed · last 50 runs (7 days). The agent app is inferred from "
        "the focused window at each prompt-submit moment.</p>"
        + remote_summary
        + "<h3>Keys typed in the agent's window</h3>"
        + horizontal_bars(in_agent_items, value_fmt=lambda v: str(int(v)))
        + "<h3>Keys typed elsewhere (multitasking)</h3>"
        + horizontal_bars(other_items, value_fmt=lambda v: str(int(v)))
        + "<h3>Per-run detail</h3>"
        '<table class="recent-rows">'
        "<thead><tr><th>when (local)</th><th>agent</th><th>session</th>"
        "<th>agent app</th><th>run sec</th><th>keys in agent</th>"
        "<th>keys elsewhere</th><th>keys/sec</th></tr></thead>"
        f'<tbody>{"".join(detail_rows)}</tbody></table>'
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "00_runtime_heatmap",
            "name": "Runtime Heatmap (7d)",
            "description": "7-day × 24-hour heatmap of agent_running seconds in local time.",
            "render": render_runtime_heatmap,
        },
        {
            "slug": "01_state_breakdown",
            "name": "State Breakdown (7d)",
            "description": "Daily stacked bars of agent_running vs awaiting_user minutes.",
            "render": render_state_breakdown,
        },
        {
            "slug": "02_prompt_cadence",
            "name": "Prompt Cadence (7d)",
            "description": "Distribution of inter-prompt gap durations in the last 7 days.",
            "render": render_prompt_cadence,
        },
        {
            "slug": "03_engagement",
            "name": "Engagement (7d)",
            "description": "Keystrokes typed while agent was running, joined from mosspath_lite.",
            "render": render_engagement,
        },
    ]
