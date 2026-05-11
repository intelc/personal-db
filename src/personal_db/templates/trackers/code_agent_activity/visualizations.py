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
                -- ISO-8601 timestamps sort lexicographically, so a plain
                -- string range using datetime(±N seconds) lets SQLite use
                -- idx_mosspath_lite_events_timestamp. Wrapping in julianday()
                -- or datetime() would force a full scan per outer row.
                SELECT e.agent, e.session_id,
                       m.bundle_id, m.app_name,
                       COUNT(*) AS n
                FROM code_agent_events e
                JOIN mosspath_lite_events m
                  ON m.action_type = 'submitted_text'
                 AND m.timestamp >= datetime(e.timestamp, '-3 seconds')
                 AND m.timestamp <= datetime(e.timestamp, '+3 seconds')
                 AND m.bundle_id IS NOT NULL
                WHERE e.event_type = 'prompt_submitted'
                  AND e.timestamp >= datetime('now', '-7 days')
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
              ON m.timestamp >= i.start_ts
             AND m.timestamp <  i.end_ts
             AND m.key_count > 0
             AND m.timestamp >= datetime('now', '-7 days')
            WHERE i.state = 'agent_running'
              AND i.start_ts >= datetime('now', '-7 days')
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


# Brand glyphs sourced from simple-icons (MIT) — single-path SVGs at 24x24.
# https://simpleicons.org/
_AGENT_BRAND_COLORS = {
    "claude_code": "#D97757",
    "codex_cli": "#412991",
}
_AGENT_GLYPH_PATHS = {
    "claude_code": "m4.7144 15.9555 4.7174-2.6471.079-.2307-.079-.1275h-.2307l-.7893-.0486-2.6956-.0729-2.3375-.0971-2.2646-.1214-.5707-.1215-.5343-.7042.0546-.3522.4797-.3218.686.0608 1.5179.1032 2.2767.1578 1.6514.0972 2.4468.255h.3886l.0546-.1579-.1336-.0971-.1032-.0972L6.973 9.8356l-2.55-1.6879-1.3356-.9714-.7225-.4918-.3643-.4614-.1578-1.0078.6557-.7225.8803.0607.2246.0607.8925.686 1.9064 1.4754 2.4893 1.8336.3643.3035.1457-.1032.0182-.0728-.164-.2733-1.3539-2.4467-1.445-2.4893-.6435-1.032-.17-.6194c-.0607-.255-.1032-.4674-.1032-.7285L6.287.1335 6.6997 0l.9957.1336.419.3642.6192 1.4147 1.0018 2.2282 1.5543 3.0296.4553.8985.2429.8318.091.255h.1579v-.1457l.1275-1.706.2368-2.0947.2307-2.6957.0789-.7589.3764-.9107.7468-.4918.5828.2793.4797.686-.0668.4433-.2853 1.8517-.5586 2.9021-.3643 1.9429h.2125l.2429-.2429.9835-1.3053 1.6514-2.0643.7286-.8196.85-.9046.5464-.4311h1.0321l.759 1.1293-.34 1.1657-1.0625 1.3478-.8804 1.1414-1.2628 1.7-.7893 1.36.0729.1093.1882-.0183 2.8535-.607 1.5421-.2794 1.8396-.3157.8318.3886.091.3946-.3278.8075-1.967.4857-2.3072.4614-3.4364.8136-.0425.0304.0486.0607 1.5482.1457.6618.0364h1.621l3.0175.2247.7892.522.4736.6376-.079.4857-1.2142.6193-1.6393-.3886-3.825-.9107-1.3113-.3279h-.1822v.1093l1.0929 1.0686 2.0035 1.8092 2.5075 2.3314.1275.5768-.3218.4554-.34-.0486-2.2039-1.6575-.85-.7468-1.9246-1.621h-.1275v.17l.4432.6496 2.3436 3.5214.1214 1.0807-.17.3521-.6071.2125-.6679-.1214-1.3721-1.9246L14.38 17.959l-1.1414-1.9428-.1397.079-.674 7.2552-.3156.3703-.7286.2793-.6071-.4614-.3218-.7468.3218-1.4753.3886-1.9246.3157-1.53.2853-1.9004.17-.6314-.0121-.0425-.1397.0182-1.4328 1.9672-2.1796 2.9446-1.7243 1.8456-.4128.164-.7164-.3704.0667-.6618.4008-.5889 2.386-3.0357 1.4389-1.882.929-1.0868-.0062-.1579h-.0546l-6.3385 4.1164-1.1293.1457-.4857-.4554.0608-.7467.2307-.2429 1.9064-1.3114Z",
    "codex_cli": "M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z",
}


def _agent_glyph(agent: str, color: str, x: float, y: float, size: int) -> str:
    """Inline SVG glyph identifying the agent in a timeline lane.

    Uses simple-icons paths and the brand's official hex color, scaled
    from the canonical 24x24 viewBox. Falls back to a colored square
    for unknown agents.
    """
    path_d = _AGENT_GLYPH_PATHS.get(agent)
    if path_d is None:
        return (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
            f'fill="{color}" />'
        )
    glyph_fill = _AGENT_BRAND_COLORS.get(agent, color)
    scale = size / 24.0
    return (
        f'<g transform="translate({x:.2f},{y:.2f}) scale({scale:.4f})">'
        f'<path d="{path_d}" fill="{glyph_fill}" />'
        f"</g>"
    )


def render_session_timeline(cfg: Config) -> str:
    """Per-session Gantt-style timeline of the last 24 hours.

    One horizontal lane per (agent, session_id) showing agent_running
    intervals (solid, agent-colored) and awaiting_user intervals (lighter).
    Small ticks above each lane mark moments mosspath captured user
    keystrokes (any app), giving a sense of when the user was at the
    keyboard during a session.
    """
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)

    try:
        rows = con.execute(
            """
            SELECT i.agent, i.session_id, i.start_ts, i.end_ts, i.state,
                   i.duration_seconds, i.is_remote,
                   s.first_user_prompt AS first_user_prompt,
                   s.cwd AS session_cwd
            FROM code_agent_intervals i
            LEFT JOIN code_agent_sessions s
              ON s.agent = i.agent AND s.session_id = i.session_id
            WHERE i.end_ts >= ?
              AND i.start_ts <= ?
            ORDER BY i.agent, i.session_id, i.start_ts
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        # Keystroke moments — any mosspath event with key_count > 0 in the
        # window. Used for the per-lane keystroke tick marks.
        has_mosspath = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mosspath_lite_events'"
        ).fetchone()
        if has_mosspath:
            key_rows = con.execute(
                """
                SELECT timestamp, key_count
                FROM mosspath_lite_events
                WHERE key_count > 0
                  AND timestamp >= ?
                  AND timestamp <= ?
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        else:
            key_rows = []
    except sqlite3.OperationalError as exc:
        return f'<p class="meta">query error: {escape(str(exc))}</p>'
    finally:
        con.close()

    if not rows:
        return '<p class="meta">no agent runs in the last 24 hours</p>'

    # Group intervals by session, preserving display order: earliest start first
    sessions: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    session_first: dict[tuple[str, str], float] = {}
    # Per-session metadata from code_agent_sessions (LEFT JOIN above). Captured
    # once per session and surfaced in a header list above the SVG so users can
    # tell what each lane was about (cwd + first prompt).
    session_meta_disp: dict[tuple[str, str], dict[str, str | None]] = {}
    for r in rows:
        key = (r["agent"], r["session_id"])
        sessions[key].append(r)
        ts = datetime.fromisoformat(r["start_ts"].replace("Z", "+00:00")).timestamp()
        if key not in session_first or ts < session_first[key]:
            session_first[key] = ts
        if key not in session_meta_disp:
            session_meta_disp[key] = {
                "first_user_prompt": r["first_user_prompt"],
                "cwd": r["session_cwd"],
            }
    ordered_keys = sorted(sessions.keys(), key=lambda k: session_first[k])

    # Layout
    W = 760
    PAD_L, PAD_R, PAD_T, PAD_B = 110, 14, 18, 28
    LANE_H = 16
    LANE_GAP = 4
    plot_w = W - PAD_L - PAD_R
    plot_h = (LANE_H + LANE_GAP) * len(ordered_keys) - LANE_GAP
    H = PAD_T + plot_h + PAD_B

    cutoff_ts = start.timestamp()
    now_ts = end.timestamp()
    span = now_ts - cutoff_ts

    def x_of(ts_iso: str) -> float:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()
        clamped = max(cutoff_ts, min(now_ts, ts))
        return PAD_L + (clamped - cutoff_ts) / span * plot_w

    # Agent palette: solid for agent_running, alpha-variant for awaiting_user.
    AGENT_COLORS = {
        "claude_code": "#D97757",  # claude orange (brand)
        "codex_cli": "#2563eb",    # blue
    }

    def fmt_dur(s: float) -> str:
        s = int(round(s))
        if s >= 3600:
            return f"{s // 3600}h {(s % 3600) // 60}m"
        if s >= 60:
            return f"{s // 60}m {s % 60}s"
        return f"{s}s"

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" '
        f'style="width: 100%; height: auto; max-height: {H + 20}px; '
        f'border: 1px solid #000; background: #fafafa;">'
    )
    # Gridlines: hour (full, darker, labeled every 2h) + half-hour (faint, no label).
    for half in range(0, 49):  # 0, 0.5h, 1h, ... 24h
        ts_h = cutoff_ts + half * 1800
        if ts_h < cutoff_ts or ts_h > now_ts:
            continue
        x = PAD_L + (ts_h - cutoff_ts) / span * plot_w
        is_hour = (half % 2) == 0
        stroke = "#ddd" if is_hour else "#eee"
        parts.append(
            f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{PAD_T + plot_h}" '
            f'stroke="{stroke}" stroke-width="1" />'
        )
        # Label every 2 hours to avoid crowding (24 hour-labels @ 636px = too tight).
        if is_hour and ((half // 2) % 2) == 0:
            label_dt = datetime.fromtimestamp(ts_h)
            parts.append(
                f'<text x="{x:.1f}" y="{H - PAD_B + 14}" font-size="10" '
                f'text-anchor="middle" fill="#666" font-family="ui-monospace, monospace">'
                f"{label_dt.strftime('%H:%M')}</text>"
            )

    # Each session lane
    for idx, key in enumerate(ordered_keys):
        agent, sid = key
        y = PAD_T + idx * (LANE_H + LANE_GAP)
        color = AGENT_COLORS.get(agent, "#666")
        # Lane label: colored agent glyph + first 8 chars of session id;
        # 'remote' tag if any interval in the session was remote.
        any_remote = any(r["is_remote"] for r in sessions[key])
        label = sid[:8]
        if any_remote:
            label += " ⌁"
        # Per-lane hover: prompt + cwd (newline-separated). Falls back to
        # agent · sid when neither is available. The interval bars below have
        # their own <title> elements which override this on hover.
        meta = session_meta_disp.get(key, {})
        prompt = (meta.get("first_user_prompt") or "").strip()
        if len(prompt) > 200:
            prompt = prompt[:197].rstrip() + "..."
        cwd = (meta.get("cwd") or "").strip()
        # Always two lines so the hover format is consistent across sessions.
        hover_text = (
            f"{agent} · {sid[:8]}\n"
            f"{prompt or '(no prompt captured)'}\n"
            f"{cwd or '(cwd unknown)'}"
        )
        parts.append(f'<g data-tip="{escape(hover_text)}">')
        # Agent-identity glyph at the far left, before the session id text.
        # 12x12 with ~6px gap; text reserves ~60px (8 chars + " ⌁" suffix at
        # ~6px/char monospace 10).
        gl_size = 12
        gl_y = y + (LANE_H - gl_size) / 2
        gl_x = PAD_L - 6 - 60 - 6 - gl_size
        parts.append(_agent_glyph(agent, color, gl_x, gl_y, gl_size))
        parts.append(
            f'<text x="{PAD_L - 6}" y="{y + LANE_H / 2 + 4:.1f}" font-size="10" '
            f'text-anchor="end" fill="#333" font-family="ui-monospace, monospace">'
            f"{escape(label)}</text>"
        )
        # Lane background
        parts.append(
            f'<rect x="{PAD_L}" y="{y}" width="{plot_w}" height="{LANE_H}" '
            f'fill="#fff" stroke="#eee" />'
        )
        # Intervals
        for r in sessions[key]:
            x1 = x_of(r["start_ts"])
            x2 = x_of(r["end_ts"])
            w = max(1.0, x2 - x1)
            if r["state"] == "agent_running":
                fill = color
                opacity = "0.95"
            elif r["state"] == "awaiting_user":
                fill = color
                opacity = "0.18"
            else:
                fill = "#999"
                opacity = "0.3"
            t0 = datetime.fromisoformat(r["start_ts"].replace("Z", "+00:00")).astimezone()
            t1 = datetime.fromisoformat(r["end_ts"].replace("Z", "+00:00")).astimezone()
            tip = (
                f"{r['state']} · {fmt_dur(r['duration_seconds'])} · "
                f"{t0.strftime('%H:%M:%S')}–{t1.strftime('%H:%M:%S')}"
            )
            parts.append(
                f'<rect x="{x1:.1f}" y="{y}" width="{w:.2f}" height="{LANE_H}" '
                f'fill="{fill}" fill-opacity="{opacity}" '
                f'data-tip="{escape(tip)}" />'
            )
        parts.append("</g>")

    # Keystroke tick marks across the bottom of the plot area (single row;
    # density gives a sense of when the user was at the keyboard).
    if key_rows:
        tick_y = PAD_T + plot_h + 2
        for kr in key_rows:
            x = x_of(kr["timestamp"])
            parts.append(
                f'<line x1="{x:.1f}" y1="{tick_y}" x2="{x:.1f}" y2="{tick_y + 4}" '
                f'stroke="#000" stroke-width="0.6" stroke-opacity="0.5" />'
            )

    parts.append("</svg>")

    legend = (
        '<p class="meta" style="margin-top: 6px;">'
        '<span style="display: inline-block; width: 10px; height: 10px; '
        'background: #2563eb; vertical-align: middle;"></span> codex agent_running &nbsp; '
        '<span style="display: inline-block; width: 10px; height: 10px; '
        'background: #D97757; vertical-align: middle;"></span> claude agent_running &nbsp; '
        '<span style="display: inline-block; width: 10px; height: 10px; '
        'background: #2563eb33; vertical-align: middle;"></span> awaiting_user (lighter) &nbsp; '
        '| black ticks below: keystroke moments &nbsp; ⌁ = remote session'
        "</p>"
    )

    # Custom HTML tooltip — replaces SVG <title> for instant hover (no ~1s
    # native delay). One <div> per render gets shown/positioned by the script
    # whenever the cursor enters an element with a [data-tip] attribute.
    tooltip_css = (
        "<style>"
        ".cga-tip-wrap{position:relative;}"
        ".cga-tip{position:absolute;display:none;pointer-events:none;"
        "background:#000;color:#fff;padding:5px 7px;font-size:11px;"
        "font-family:ui-monospace,monospace;border-radius:3px;"
        "max-width:520px;white-space:pre-wrap;line-height:1.4;"
        "z-index:1000;}"
        "</style>"
    )
    tooltip_script = (
        "<script>"
        "(function(){"
        "if(window.__cgaTipInit)return;window.__cgaTipInit=true;"
        "function findTipEl(t){return t&&t.closest?t.closest('[data-tip]'):null;}"
        "document.addEventListener('mouseover',function(e){"
        "var el=findTipEl(e.target);if(!el)return;"
        "var w=el.closest('.cga-tip-wrap');if(!w)return;"
        "var tip=w.querySelector('[data-cga-tip]');"
        "tip.textContent=el.getAttribute('data-tip')||'';"
        "tip.style.display='block';"
        "});"
        "document.addEventListener('mousemove',function(e){"
        "var el=findTipEl(e.target);if(!el)return;"
        "var w=el.closest('.cga-tip-wrap');if(!w)return;"
        "var tip=w.querySelector('[data-cga-tip]');"
        "var r=w.getBoundingClientRect();"
        "tip.style.left=(e.clientX-r.left+12)+'px';"
        "tip.style.top=(e.clientY-r.top+12)+'px';"
        "});"
        "document.addEventListener('mouseout',function(e){"
        "var el=findTipEl(e.target);if(!el)return;"
        "var to=e.relatedTarget;if(to&&el.contains(to))return;"
        "var w=el.closest('.cga-tip-wrap');if(!w)return;"
        "var tip=w.querySelector('[data-cga-tip]');"
        "tip.style.display='none';"
        "});"
        "})();"
        "</script>"
    )

    return (
        tooltip_css
        + '<p class="meta">last 24 hours · one lane per session · hover for details</p>'
        + '<div class="cga-tip-wrap">'
        + "".join(parts)
        + '<div class="cga-tip" data-cga-tip></div>'
        + "</div>"
        + legend
        + tooltip_script
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "00_session_timeline",
            "name": "Session Timeline (24h)",
            "description": "One lane per session; agent runtime vs awaiting_user vs keystroke moments.",
            "render": render_session_timeline,
        },
        {
            "slug": "01_runtime_heatmap",
            "name": "Runtime Heatmap (7d)",
            "description": "7-day × 24-hour heatmap of agent_running seconds in local time.",
            "render": render_runtime_heatmap,
        },
        {
            "slug": "02_state_breakdown",
            "name": "State Breakdown (7d)",
            "description": "Daily stacked bars of agent_running vs awaiting_user minutes.",
            "render": render_state_breakdown,
        },
        {
            "slug": "03_prompt_cadence",
            "name": "Prompt Cadence (7d)",
            "description": "Distribution of inter-prompt gap durations in the last 7 days.",
            "render": render_prompt_cadence,
        },
        {
            "slug": "04_engagement",
            "name": "Engagement (7d)",
            "description": "Keystrokes typed while agent was running, joined from mosspath_lite.",
            "render": render_engagement,
        },
    ]
