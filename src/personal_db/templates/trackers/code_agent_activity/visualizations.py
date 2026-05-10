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
            WHERE datetime(i.end_ts) >= datetime(?)
              AND datetime(i.start_ts) <= datetime(?)
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
                  AND datetime(timestamp) >= datetime(?)
                  AND datetime(timestamp) <= datetime(?)
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
        "claude_code": "#a855f7",  # purple
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
    # Hour gridlines (every 4h) and labels
    for h in range(0, 25, 4):
        ts_h = cutoff_ts + h * 3600
        clamped = max(cutoff_ts, min(now_ts, ts_h))
        x = PAD_L + (clamped - cutoff_ts) / span * plot_w
        parts.append(
            f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{PAD_T + plot_h}" '
            f'stroke="#ddd" stroke-width="1" />'
        )
        label_dt = datetime.fromtimestamp(clamped)
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
        # Lane label: agent + first 8 chars of session id; 'remote' tag if any
        # interval in the session was remote.
        any_remote = any(r["is_remote"] for r in sessions[key])
        label = f"{agent.split('_')[0]} · {sid[:8]}"
        if any_remote:
            label += " ⌁"
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
                f'fill="{fill}" fill-opacity="{opacity}">'
                f"<title>{escape(tip)}</title></rect>"
            )

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
        'background: #a855f7; vertical-align: middle;"></span> claude agent_running &nbsp; '
        '<span style="display: inline-block; width: 10px; height: 10px; '
        'background: #2563eb33; vertical-align: middle;"></span> awaiting_user (lighter) &nbsp; '
        '| black ticks below: keystroke moments &nbsp; ⌁ = remote session'
        "</p>"
    )

    # Per-session header: prompt (truncated to 100 chars) + cwd, in the same
    # order as the lanes. Lets the user map a lane label (e.g. "claude · abc12345")
    # back to what the session was actually doing.
    header_items: list[str] = []
    for key in ordered_keys:
        agent, sid = key
        meta = session_meta_disp.get(key, {})
        prompt = (meta.get("first_user_prompt") or "").strip()
        if len(prompt) > 100:
            prompt = prompt[:97].rstrip() + "..."
        cwd = meta.get("cwd")
        if not prompt and not cwd:
            continue
        prompt_html = (
            f'<span class="prompt">{escape(prompt)}</span>' if prompt
            else '<span class="prompt meta">(no prompt captured)</span>'
        )
        cwd_html = f' <span class="meta">({escape(cwd)})</span>' if cwd else ""
        header_items.append(
            f'<li><code>{escape(agent.split("_")[0])} · {escape(sid[:8])}</code> '
            f"— {prompt_html}{cwd_html}</li>"
        )
    session_header = (
        '<ul class="meta" style="margin: 6px 0 8px 0; padding-left: 18px;">'
        + "".join(header_items)
        + "</ul>"
        if header_items
        else ""
    )

    return (
        '<p class="meta">last 24 hours · one lane per session · hover for details</p>'
        + session_header
        + "".join(parts)
        + legend
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
