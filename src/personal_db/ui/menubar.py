"""rumps menu bar app for personal_db.

Lives in the menu bar; surfaces a tiny status indicator, today's totals,
quick life_context logging, and a "force sync" action. Heavy stuff
(charts, history) lives in the FastAPI dashboard — clicking "Open
dashboard" launches the default browser at http://127.0.0.1:<port>/.

The FastAPI server runs in a daemon thread so the rumps event loop owns
the main thread (Cocoa requirement).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import rumps
import uvicorn

from personal_db.config import Config
from personal_db.mcp_server.tools import log_life_context
from personal_db.sync import sync_due
from personal_db.ui.server import build_app

# Color → text mapping. macOS renders these emojis as native menu bar glyphs.
_HEALTHY = "🟢"
_STALE = "🟡"
_ERROR = "🔴"

_QUICK_STATES = ["well", "sick", "recovering", "traveling", "focused", "system_event"]


def _local_today() -> date:
    return datetime.now().astimezone().date()


def _start_server(cfg: Config, port: int) -> None:
    app = build_app(cfg)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # uvicorn installs SIGINT/SIGTERM handlers on import; in a non-main thread
    # those raise. Disable so the rumps quit handler does the cleanup.
    server.config.install_signal_handlers = False
    server.run()


def _today_summary(cfg: Config, day: date) -> str:
    """One-line summary of today's top categories: '2.4h work · 1.2h leisure'."""
    try:
        con = sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return "no data"
    try:
        rows = con.execute(
            "SELECT category, hours FROM daily_time_accounting "
            "WHERE date = ? AND category NOT LIKE '\\_%' ESCAPE '\\' "
            "ORDER BY hours DESC LIMIT 3",
            (day.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return "no data"
    finally:
        con.close()
    if not rows:
        return "no data yet"
    return " · ".join(f"{round(h, 1)}h {c}" for c, h in rows)


def _health_signal(cfg: Config) -> str:
    """Pick an icon based on last-run ages and recorded sync errors."""
    last_run_path = cfg.state_dir / "last_run.json"
    err_path = cfg.state_dir / "sync_errors.jsonl"
    # Recent errors → red. We only consider errors logged in the last 24h to
    # avoid one ancient failure pinning the badge red forever.
    if err_path.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            with err_path.open() as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        ts = datetime.fromisoformat(rec.get("ts", ""))
                        if ts >= cutoff:
                            return _ERROR
                    except (ValueError, json.JSONDecodeError):
                        continue
        except OSError:
            pass
    # Stale tracker → yellow. Anything that hasn't synced in >12h is stale.
    if last_run_path.exists():
        try:
            data = json.loads(last_run_path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
        for ts in data.values():
            try:
                if datetime.fromisoformat(ts) < cutoff:
                    return _STALE
            except ValueError:
                continue
    return _HEALTHY


class PersonalDBApp(rumps.App):
    def __init__(self, cfg: Config, port: int) -> None:
        super().__init__("personal_db", quit_button=None)
        self.cfg = cfg
        self.port = port
        self._build_menu()
        self._refresh()

    def _build_menu(self) -> None:
        # Status line (read-only label); rumps uses None callback for inert items.
        self.summary_item = rumps.MenuItem("loading…", callback=None)
        self.health_item = rumps.MenuItem("status: ?", callback=None)
        # Quick log submenu — one item per state, plus a "(custom note…)" option
        log_menu = rumps.MenuItem("Quick log…")
        for state in _QUICK_STATES:
            log_menu[state] = rumps.MenuItem(state, callback=self._make_log_handler(state))
        log_menu["(custom in dashboard)"] = rumps.MenuItem(
            "(custom in dashboard)", callback=self._open_dashboard
        )
        self.menu = [
            self.summary_item,
            self.health_item,
            None,
            log_menu,
            rumps.MenuItem("Force sync (all due)", callback=self._sync_all),
            rumps.MenuItem("Open dashboard", callback=self._open_dashboard),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

    def _make_log_handler(self, state: str):
        def handler(_):
            today = _local_today().isoformat()
            try:
                log_life_context(self.cfg, start_date=today, state=state)
                rumps.notification(
                    "personal_db",
                    "logged",
                    f"{state} · {today}",
                    sound=False,
                )
            except Exception as e:  # noqa: BLE001 — surface any error to the user
                rumps.notification("personal_db", "log failed", str(e), sound=False)
            self._refresh()
        return handler

    def _sync_all(self, _) -> None:
        # Run in a thread so the menu bar stays responsive during sync.
        def run():
            try:
                results = sync_due(self.cfg)
                ok = sum(1 for v in results.values() if v == "ok")
                err = sum(1 for v in results.values() if v.startswith("error"))
                msg = f"{ok} synced · {err} errored"
                rumps.notification("personal_db", "sync done", msg, sound=False)
            except Exception as e:  # noqa: BLE001
                rumps.notification("personal_db", "sync failed", str(e), sound=False)
            self._refresh()
        threading.Thread(target=run, daemon=True).start()

    def _open_dashboard(self, _) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    @rumps.timer(60)
    def _periodic(self, _) -> None:
        self._refresh()

    def _refresh(self) -> None:
        signal = _health_signal(self.cfg)
        self.title = signal
        summary = _today_summary(self.cfg, _local_today())
        self.summary_item.title = f"today: {summary}"
        self.health_item.title = (
            "all green" if signal == _HEALTHY
            else ("stale tracker(s)" if signal == _STALE else "errors in last 24h")
        )


def run_menubar(cfg: Config, port: int = 8765) -> None:
    threading.Thread(target=_start_server, args=(cfg, port), daemon=True).start()
    PersonalDBApp(cfg, port).run()
