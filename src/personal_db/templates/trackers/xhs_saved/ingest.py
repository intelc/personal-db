"""Xiaohongshu / RedNote saved-post ingest.

The tracker follows the proven xhs-saved-posts workflow: collect links from the
logged-in Chrome UI, then fetch detail pages with Chrome cookies and parse
window.__INITIAL_STATE__. The raw favorites API is deliberately avoided.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac, sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from personal_db.tracker import Tracker

NOTE_ID_RE = re.compile(
    r"(?:explore|user/profile/[^/]+)/([0-9a-f]{24})(?:[/?#]|$)", re.I
)
INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{[\s\S]*?\})\s*</script>"
)

CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
DEFAULT_MAX_SCROLLS = 240
DEFAULT_DETAIL_LIMIT = 50
DEFAULT_SCROLL_DELAY_MS = 900
DEFAULT_REFRESH_DAYS = 30
DEFAULT_OVERLAP_STOP = 25
COLLECT_TIMEOUT_S = 600
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024


def _env_int(name: str, default: int, *, min_value: int = 0, max_value: int = 500) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _env_bool(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _extract_note_id(url: str | None) -> str | None:
    if not url:
        return None
    match = NOTE_ID_RE.search(url)
    return match.group(1).lower() if match else None


def _query_param(url: str, name: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    for part in parsed.query.split("&"):
        key, _, value = part.partition("=")
        if key == name:
            return value
    return ""


def _saved_tab_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["tab"] = "fav"
    query["subTab"] = "note"
    return urlunparse(parsed._replace(query=urlencode(query)))


def _run_osascript(script: str, *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RuntimeError("osascript is required on macOS for the xhs_saved tracker") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(f"Chrome automation failed: {detail}") from e
    return result.stdout.strip()


def _execute_chrome_js(
    js: str,
    *,
    timeout: int = 60,
    tab_handle: dict[str, int | bool] | None = None,
) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        tmp = Path(f.name)
    try:
        quoted = str(tmp).replace('"', '\\"')
        if tab_handle:
            window_id = int(tab_handle["window_id"])
            tab_id = int(tab_handle["tab_id"])
            script = f"""set jsCode to do shell script "cat " & quoted form of "{quoted}"
tell application "Google Chrome"
  if (count of windows) = 0 then error "no Chrome windows"
  set targetWindow to first window whose id is {window_id}
  set targetTab to first tab of targetWindow whose id is {tab_id}
  execute targetTab javascript jsCode
end tell"""
        else:
            script = f"""set jsCode to do shell script "cat " & quoted form of "{quoted}"
tell application "Google Chrome"
  if (count of windows) = 0 then make new window
  execute active tab of front window javascript jsCode
end tell"""
        return _run_osascript(script, timeout=timeout)
    finally:
        with suppress(OSError):
            tmp.unlink()


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _open_chrome_url(url: str) -> dict[str, int | bool]:
    escaped = _applescript_string(url)
    script = f"""tell application "Google Chrome"
  activate
  set createdWindow to false
  if (count of windows) = 0 then
    make new window
    set createdWindow to true
  end if
  set targetWindow to front window
  set targetWindowId to id of targetWindow
  tell targetWindow
    set newTab to make new tab at end of tabs with properties {{URL:"{escaped}"}}
    set newTabId to id of newTab
    set active tab index to count of tabs
  end tell
  return (createdWindow as string) & "|||" & (targetWindowId as string) & "|||" & (newTabId as string)
end tell"""
    raw = _run_osascript(script, timeout=30)
    created_raw, window_raw, tab_raw = raw.split("|||")
    return {
        "created_window": created_raw.lower() == "true",
        "window_id": int(window_raw),
        "tab_id": int(tab_raw),
    }


def _active_chrome_tab_handle() -> dict[str, int | bool]:
    script = """tell application "Google Chrome"
  if (count of windows) = 0 then error "no Chrome windows"
  return "false|||" & ((id of front window) as string) & "|||" & ((id of active tab of front window) as string)
end tell"""
    raw = _run_osascript(script, timeout=30)
    created_raw, window_raw, tab_raw = raw.split("|||")
    return {
        "created_window": created_raw.lower() == "true",
        "window_id": int(window_raw),
        "tab_id": int(tab_raw),
    }


def _close_chrome_tab_handle(handle: dict[str, int | bool]) -> None:
    created_window = "true" if handle.get("created_window") else "false"
    window_id = int(handle["window_id"])
    tab_id = int(handle["tab_id"])
    script = f"""tell application "Google Chrome"
  if (count of windows) = 0 then return
  try
    set targetWindow to first window whose id is {window_id}
  on error
    return
  end try
  if "{created_window}" is "true" then
    close targetWindow
    return
  end if
  try
    delete (first tab of targetWindow whose id is {tab_id})
  end try
end tell"""
    with suppress(Exception):
        _run_osascript(script, timeout=30)


def _parse_json(raw: str, context: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"xhs_saved: Chrome returned invalid JSON for {context}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"xhs_saved: Chrome returned non-object JSON for {context}")
    return data


def _collect_saved_posts(
    *,
    profile_url: str | None,
    use_active_tab: bool,
    max_scrolls: int,
    scroll_delay_ms: int,
    known_note_ids: set[str],
    overlap_stop: int,
    deep_backfill: bool,
) -> dict[str, Any]:
    close_tab_after = False
    if profile_url:
        tab_handle = _open_chrome_url(_saved_tab_url(profile_url))
        close_tab_after = True
        _execute_chrome_js("location.reload()", timeout=20, tab_handle=tab_handle)
        time.sleep(6.0)
    elif use_active_tab:
        tab_handle = _active_chrome_tab_handle()
        active_url = _execute_chrome_js("location.href", timeout=20, tab_handle=tab_handle)
        if "xiaohongshu.com" not in active_url:
            raise RuntimeError(f"Active Chrome tab is not Xiaohongshu: {active_url}")
        saved_url = _saved_tab_url(active_url)
        if saved_url != active_url:
            _execute_chrome_js(
                f"location.href = {json.dumps(saved_url)}",
                timeout=20,
                tab_handle=tab_handle,
            )
            time.sleep(1.0)
        _execute_chrome_js("location.reload()", timeout=20, tab_handle=tab_handle)
        time.sleep(6.0)
    else:
        raise RuntimeError(
            "Set XHS_SAVED_PROFILE_URL or XHS_SAVED_USE_ACTIVE_TAB=1 before syncing xhs_saved"
        )

    try:
        return _collect_saved_posts_in_tab(
            tab_handle=tab_handle,
            max_scrolls=max_scrolls,
            scroll_delay_ms=scroll_delay_ms,
            known_note_ids=known_note_ids,
            overlap_stop=overlap_stop,
            deep_backfill=deep_backfill,
        )
    finally:
        if close_tab_after:
            _close_chrome_tab_handle(tab_handle)


def _collect_saved_posts_in_tab(
    *,
    tab_handle: dict[str, int | bool],
    max_scrolls: int,
    scroll_delay_ms: int,
    known_note_ids: set[str],
    overlap_stop: int,
    deep_backfill: bool,
) -> dict[str, Any]:
    start_js = f"""
(() => {{
  const maxScrolls = {json.dumps(max_scrolls)};
  const delayMs = {json.dumps(scroll_delay_ms)};
  const knownIds = new Set({json.dumps(sorted(known_note_ids))});
  const overlapStop = {json.dumps(overlap_stop)};
  const deepBackfill = {json.dumps(deep_backfill)};
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const visible = el => {{
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const textOf = el => (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
  const tabCandidates = label => Array.from(document.querySelectorAll('[role="tab"], .reds-tab-item, button, a, div, span'))
    .filter(el => {{
      if (!visible(el)) return false;
      const text = textOf(el);
      return text === label || (text.startsWith(label) && text.length <= label.length + 12);
    }})
    .sort((a, b) => {{
      const aTab = a.closest('[role="tab"], .reds-tab-item') ? 0 : 1;
      const bTab = b.closest('[role="tab"], .reds-tab-item') ? 0 : 1;
      if (aTab !== bTab) return aTab - bTab;
      return a.getBoundingClientRect().width - b.getBoundingClientRect().width;
    }});
  const clickTab = label => {{
    const candidates = tabCandidates(label);
    if (candidates[0]) {{
      const target = candidates[0].closest(".reds-tab-item, button, a") || candidates[0];
      target.scrollIntoView({{ block: "center", inline: "center" }});
      for (const type of ["mousedown", "mouseup"]) {{
        target.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
      }}
      target.click();
      return true;
    }}
    return false;
  }};
  const savedTabActive = () => Array.from(document.querySelectorAll(".reds-tab-item"))
    .some(el => textOf(el).startsWith("收藏") && String(el.className || "").includes("active"));
  const expectedSavedCount = () => {{
    const activeSubtab = Array.from(document.querySelectorAll(".reds-tab-item.active"))
      .map(textOf)
      .find(text => text.startsWith("笔记"));
    const match = (activeSubtab || "").match(/笔记[・·\\s]*(\\d+)/);
    return match ? Number(match[1]) : 0;
  }};
  const activePanel = () => {{
    const panels = Array.from(document.querySelectorAll(".tab-content-item"));
    const visiblePanels = panels
      .map(el => {{
        const r = el.getBoundingClientRect();
        const visibleWidth = Math.max(0, Math.min(r.right, window.innerWidth) - Math.max(r.left, 0));
        const visibleHeight = Math.max(0, Math.min(r.bottom, window.innerHeight * 4) - Math.max(r.top, 0));
        const linkCount = Array.from(el.querySelectorAll("a[href]"))
          .filter(a => noteRe.test(new URL(a.getAttribute("href"), location.href).href)).length;
        return {{ el, linkCount, visibleArea: visibleWidth * visibleHeight, rect: r }};
      }})
      .filter(x => x.visibleArea > 1000);
    visiblePanels.sort((a, b) => (b.linkCount - a.linkCount) || (b.visibleArea - a.visibleArea));
    return visiblePanels[0]?.el || document.body;
  }};
  const scrollActivePanel = () => {{
    const root = activePanel();
    const amount = Math.max(320, Math.floor((root.clientHeight || window.innerHeight) * 0.9));
    if (root.scrollHeight > root.clientHeight + 20) {{
      root.scrollTop = Math.min(root.scrollTop + amount, root.scrollHeight);
    }}
    const innerScrollers = Array.from(root.querySelectorAll("div"))
      .filter(el => el.scrollHeight > el.clientHeight + 20)
      .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
    for (const el of innerScrollers.slice(0, 4)) {{
      el.scrollTop = Math.min(el.scrollTop + Math.max(320, Math.floor((el.clientHeight || amount) * 0.9)), el.scrollHeight);
    }}
    window.scrollBy(0, Math.max(600, Math.floor(window.innerHeight * 0.9)));
    const doc = document.scrollingElement || document.documentElement;
    const docScrollable = doc.scrollHeight > doc.clientHeight + 20;
    const docAtBottom = doc.scrollTop + doc.clientHeight >= doc.scrollHeight - 24;
    const rootAtBottom = root.scrollHeight <= root.clientHeight + 20 ||
      root.scrollTop + root.clientHeight >= root.scrollHeight - 16;
    return docScrollable ? docAtBottom : rootAtBottom;
  }};
  const noteRe = /(?:explore|user\\/profile\\/[^/]+)\\/([0-9a-f]{{24}})(?:[/?#]|$)/i;
  const notes = new Map();
  const collect = () => {{
    const root = activePanel();
    const added = [];
    for (const a of Array.from(root.querySelectorAll("a[href]"))) {{
      const href = new URL(a.getAttribute("href"), location.href).href;
      const match = href.match(noteRe);
      if (!match) continue;
      const id = match[1].toLowerCase();
      const u = new URL(href);
      const title = textOf(a) || textOf(a.closest("section, article, div")) || "";
      const existing = notes.get(id) || {{}};
      notes.set(id, {{
        note_id: id,
        url: href,
        title: existing.title || title.slice(0, 180),
        xsec_token: u.searchParams.get("xsec_token") || existing.xsec_token || "",
        xsec_source: u.searchParams.get("xsec_source") || existing.xsec_source || "",
        first_seen_url: existing.first_seen_url || href
      }});
      if (!existing.note_id) added.push(id);
    }}
    return added;
  }};
  const visibleSavedLinkCount = () => {{
    const root = activePanel();
    const ids = new Set();
    for (const a of Array.from(root.querySelectorAll("a[href]"))) {{
      const href = new URL(a.getAttribute("href"), location.href).href;
      const match = href.match(noteRe);
      if (match) ids.add(match[1].toLowerCase());
    }}
    return ids.size;
  }};
  const activePanelLoading = () => {{
    const root = activePanel();
    return Array.from(root.querySelectorAll(".feeds-loading, .loading, span, div"))
      .some(el => {{
        const r = el.getBoundingClientRect();
        const visible = r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= window.innerHeight * 2;
        return visible && textOf(el).includes("加载中");
      }});
  }};

  window.__personalDbXhsSavedCollector = {{
    state: "running",
    startedAt: Date.now(),
    notes: [],
    clickedSaved: false
  }};
  (async () => {{
    try {{
      window.scrollTo(0, 0);
      await sleep(500);
      const url = new URL(location.href);
      if (url.searchParams.get("tab") !== "fav" || url.searchParams.get("subTab") !== "note") {{
        throw new Error("XHS profile is not on tab=fav&subTab=note");
      }}
      if (!savedTabActive()) {{
        window.__personalDbXhsSavedCollector.clickedSaved = clickTab("收藏");
        await sleep(2200);
      }} else {{
        window.__personalDbXhsSavedCollector.clickedSaved = true;
      }}
      if (!savedTabActive()) {{
        throw new Error("XHS saved tab is not active after navigating to tab=fav&subTab=note");
      }}
      notes.clear();
      window.scrollTo(0, 0);
      let visibleCount = 0;
      let stableVisible = 0;
      for (let i = 0; i < 12; i++) {{
        await sleep(700);
        const nextCount = visibleSavedLinkCount();
        stableVisible = nextCount > 0 && nextCount === visibleCount ? stableVisible + 1 : 0;
        visibleCount = nextCount;
        window.__personalDbXhsSavedCollector = {{
          state: "loading_saved_feed",
          clickedSaved: true,
          count: visibleCount,
          notes: []
        }};
        if (visibleCount > 0 && stableVisible >= 2) break;
      }}
      collect();
      const expectedCount = expectedSavedCount();
      let stable = 0;
      let prevCount = notes.size;
      let overlapRun = 0;
      let stoppedForOverlap = false;
      for (let i = 0; i < maxScrolls; i++) {{
        const atBottom = scrollActivePanel();
        await sleep(delayMs);
        const added = collect();
        for (const id of added) {{
          overlapRun = knownIds.has(id) ? overlapRun + 1 : 0;
        }}
        stable = notes.size === prevCount ? stable + 1 : 0;
        prevCount = notes.size;
        window.__personalDbXhsSavedCollector = {{
          state: "running",
          startedAt: window.__personalDbXhsSavedCollector.startedAt,
          clickedSaved: window.__personalDbXhsSavedCollector.clickedSaved,
          scrolls: i + 1,
          count: notes.size,
          expectedCount,
          loading: activePanelLoading(),
          overlapRun,
          overlapStop,
          knownIdCount: knownIds.size,
          incremental: !deepBackfill,
          notes: Array.from(notes.values())
        }};
        if (!deepBackfill && overlapStop > 0 && overlapRun >= overlapStop) {{
          stoppedForOverlap = true;
          break;
        }}
        if (deepBackfill && expectedCount > 0 && notes.size >= expectedCount) break;
        if (atBottom && stable >= 10 && !activePanelLoading()) break;
      }}
      collect();
      window.__personalDbXhsSavedCollector = {{
        state: "done",
        href: location.href,
        title: document.title,
        clickedSaved: savedTabActive(),
        scrolls: window.__personalDbXhsSavedCollector.scrolls || 0,
        count: notes.size,
        expectedCount,
        stoppedForOverlap,
        overlapRun,
        overlapStop,
        knownIdCount: knownIds.size,
        incremental: !deepBackfill,
        notes: Array.from(notes.values()),
        finishedAt: Date.now()
      }};
    }} catch (error) {{
      window.__personalDbXhsSavedCollector = {{
        state: "error",
        error: String(error),
        message: error && error.message
      }};
    }}
  }})();
  return "started";
}})()
"""
    _execute_chrome_js(start_js, timeout=30, tab_handle=tab_handle)
    deadline = time.monotonic() + COLLECT_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(1.5)
        raw = _execute_chrome_js(
            "JSON.stringify(window.__personalDbXhsSavedCollector || null)",
            timeout=20,
            tab_handle=tab_handle,
        )
        try:
            state = _parse_json(raw, "saved-post collection") if raw else {}
        except RuntimeError:
            state = {}
        if state.get("state") == "done":
            return state
        if state.get("state") == "error":
            raise RuntimeError(
                f"xhs_saved: Chrome collector failed: {state.get('message') or state.get('error')}"
            )
    raise RuntimeError("xhs_saved: timed out collecting saved posts")


def _dedupe_collected_notes(notes: list[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in notes:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        note_id = str(item.get("note_id") or _extract_note_id(url) or "").lower()
        if not note_id:
            continue
        prev = by_id.get(note_id, {})
        by_id[note_id] = {
            "note_id": note_id,
            "url": url or prev.get("url") or "",
            "title": prev.get("title") or str(item.get("title") or "")[:180],
            "xsec_token": item.get("xsec_token") or _query_param(url, "xsec_token") or prev.get("xsec_token") or "",
            "xsec_source": item.get("xsec_source") or _query_param(url, "xsec_source") or prev.get("xsec_source") or "",
            "first_seen_url": prev.get("first_seen_url") or item.get("first_seen_url") or url,
        }
    rows = list(by_id.values())
    for rank, row in enumerate(rows, start=1):
        row["latest_seen_rank"] = rank
    return rows


def _domain_matches(host_key: str, req_host: str) -> bool:
    return (
        req_host == host_key[1:] or req_host.endswith(host_key)
        if host_key.startswith(".")
        else req_host == host_key
    )


def _chrome_cookie_db(profile: str) -> Path:
    return CHROME_DIR / profile / "Cookies"


def _chrome_cookie_key() -> bytes:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise RuntimeError("Could not read Chrome Safe Storage password from Keychain") from e
    password = result.stdout.strip().encode()
    return pbkdf2_hmac("sha1", password, b"saltysalt", 1003, dklen=16)


def _decrypt_chrome_cookie(encrypted_value: bytes, host_key: str, key: bytes) -> str:
    if not encrypted_value:
        return ""
    if not encrypted_value.startswith(b"v10"):
        raise RuntimeError("Unsupported Chrome cookie encryption format")
    decryptor = Cipher(
        algorithms.AES(key),
        modes.CBC(b" " * 16),
    ).decryptor()
    plain = decryptor.update(encrypted_value[3:]) + decryptor.finalize()
    if plain:
        pad = plain[-1]
        if 1 <= pad <= 16:
            plain = plain[:-pad]
    digest = sha256(host_key.encode()).digest()
    if plain.startswith(digest):
        plain = plain[len(digest):]
    return plain.decode("utf-8")


def _load_xhs_cookies(profile: str) -> dict[str, str]:
    db_path = _chrome_cookie_db(profile)
    if not db_path.exists():
        raise RuntimeError(f"No Chrome cookie database found at {db_path}")
    key = _chrome_cookie_key()
    cookies: dict[str, str] = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        rows = con.execute(
            """
            SELECT host_key, name, value, encrypted_value
            FROM cookies
            WHERE host_key LIKE '%xiaohongshu.com%'
            ORDER BY host_key, name
            """
        ).fetchall()
    finally:
        con.close()
    for host_key, name, value, encrypted_value in rows:
        if not (
            _domain_matches(host_key, "www.xiaohongshu.com")
            or _domain_matches(host_key, "edith.xiaohongshu.com")
        ):
            continue
        cookie_value = value or _decrypt_chrome_cookie(encrypted_value, host_key, key)
        if cookie_value:
            cookies[name] = cookie_value
    if "a1" not in cookies:
        raise RuntimeError(
            f"No Xiaohongshu a1 cookie found in Chrome profile {profile}. "
            "Open xiaohongshu.com in Chrome and sign in."
        )
    return cookies


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def _fetch_note_html(url: str, cookies: dict[str, str]) -> str:
    response = requests.get(
        url,
        headers={
            "cookie": _cookie_header(cookies),
            "user-agent": USER_AGENT,
            "referer": "https://www.xiaohongshu.com/",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def _extract_initial_state(html: str) -> dict[str, Any]:
    match = INITIAL_STATE_RE.search(html)
    if not match:
        raise RuntimeError("No window.__INITIAL_STATE__ found in XHS note HTML")
    raw = re.sub(r"\bundefined\b", "null", match.group(1))
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("XHS initial state was not valid JSON") from e
    if not isinstance(state, dict):
        raise RuntimeError("XHS initial state was not an object")
    return state


def _find_note_in_state(state: dict[str, Any], expected_id: str | None) -> dict[str, Any]:
    maps = [
        (state.get("note") or {}).get("noteDetailMap"),
        (state.get("noteData") or {}).get("noteDetailMap"),
        (state.get("feed") or {}).get("noteDetailMap"),
        (state.get("explore") or {}).get("noteDetailMap"),
    ]
    for note_map in maps:
        if not isinstance(note_map, dict):
            continue
        for key, value in note_map.items():
            note = value.get("note") if isinstance(value, dict) else None
            if note is None:
                note = value
            if not isinstance(note, dict):
                continue
            note_id = str(
                note.get("noteId")
                or note.get("note_id")
                or note.get("id")
                or key
                or ""
            ).lower()
            if not expected_id or note_id == expected_id or expected_id in str(key).lower():
                return note
    raise RuntimeError("No matching note object found in XHS initial state")


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _text_value(value: Any) -> str:
    return "" if value is None else str(value)


def _image_urls(note: dict[str, Any]) -> list[str]:
    image_list = (
        note.get("imageList")
        or note.get("image_list")
        or note.get("imagesList")
        or []
    )
    urls: list[str] = []
    if not isinstance(image_list, list):
        return urls
    for image in image_list:
        if not isinstance(image, dict):
            continue
        candidates: list[Any] = [
            image.get("urlDefault"),
            image.get("urlPre"),
            image.get("url"),
        ]
        for key in ("infoList", "info_list"):
            info_list = image.get(key)
            if isinstance(info_list, list):
                candidates.extend(x.get("url") for x in info_list if isinstance(x, dict))
        for candidate in candidates:
            if candidate and candidate not in urls:
                urls.append(str(candidate))
    return urls


def _video_urls(note: dict[str, Any]) -> list[str]:
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    media = video.get("media") if isinstance(video.get("media"), dict) else {}
    stream = media.get("stream") if isinstance(media.get("stream"), dict) else {}
    urls: list[str] = []
    for bucket in ("h264", "h265", "av1"):
        items = stream.get(bucket) if isinstance(stream.get(bucket), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("masterUrl", "master_url"):
                value = item.get(key)
                if value and value not in urls:
                    urls.append(str(value))
    return urls


def _note_summary(note: dict[str, Any], source_url: str, note_id: str) -> dict[str, Any]:
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    interact = (
        note.get("interactInfo")
        if isinstance(note.get("interactInfo"), dict)
        else note.get("interact_info")
        if isinstance(note.get("interact_info"), dict)
        else {}
    )
    return {
        "note_id": note_id or _text_value(_first(note.get("noteId"), note.get("note_id"), note.get("id"))),
        "title": _text_value(
            _first(note.get("title"), note.get("displayTitle"), note.get("display_title"))
        ).strip(),
        "desc": _text_value(_first(note.get("desc"), note.get("description"))).strip(),
        "type": _text_value(note.get("type")),
        "time": _first(note.get("time"), note.get("lastUpdateTime"), note.get("last_update_time")),
        "user": {
            "id": _text_value(_first(user.get("userId"), user.get("user_id"), user.get("id"))),
            "nickname": _text_value(_first(user.get("nickname"), user.get("name"))),
        },
        "interact": {
            "liked": _first(interact.get("likedCount"), interact.get("liked_count")),
            "collected": _first(interact.get("collectedCount"), interact.get("collected_count")),
            "comments": _first(interact.get("commentCount"), interact.get("comment_count")),
            "shares": _first(interact.get("shareCount"), interact.get("share_count")),
        },
        "source_url": source_url,
        "images": _image_urls(note),
        "videos": _video_urls(note),
        "raw": {
            "noteId": _first(note.get("noteId"), note.get("note_id"), note.get("id"), note_id),
            "title": _first(note.get("title"), note.get("displayTitle"), note.get("display_title"), ""),
            "type": note.get("type") or "",
            "time": note.get("time"),
            "user": note.get("user"),
            "interactInfo": interact,
        },
    }


def _fetch_note_summary(url: str, note_id: str, cookies: dict[str, str]) -> dict[str, Any]:
    html = _fetch_note_html(url, cookies)
    state = _extract_initial_state(html)
    note = _find_note_in_state(state, note_id)
    return _note_summary(note, url, note_id)


def _thumbnail_cache_path(cfg: Any, note_id: str) -> Path:
    safe_note_id = re.sub(r"[^0-9a-zA-Z_-]", "", note_id)
    return cfg.state_dir / "xhs_saved_media" / "thumbs" / f"{safe_note_id}.bin"


def _thumbnail_cached(cfg: Any, note_id: str) -> bool:
    path = _thumbnail_cache_path(cfg, note_id)
    return path.exists() and path.stat().st_size > 0


def _looks_like_image(content_type: str, body: bytes) -> bool:
    if content_type.startswith("image/"):
        return True
    return (
        body.startswith(b"\xff\xd8")
        or body.startswith(b"\x89PNG\r\n\x1a\n")
        or (body.startswith(b"RIFF") and body[8:12] == b"WEBP")
        or body.startswith(b"GIF87a")
        or body.startswith(b"GIF89a")
    )


def _cache_thumbnail(
    cfg: Any,
    note_id: str,
    url: str | None,
    cookies: dict[str, str] | None = None,
) -> bool:
    if not note_id or not url or _thumbnail_cached(cfg, note_id):
        return False
    headers = {
        "user-agent": USER_AGENT,
        "referer": "https://www.xiaohongshu.com/",
        "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    if cookies:
        headers["cookie"] = _cookie_header(cookies)

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    body = response.content
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not body or len(body) > MAX_THUMBNAIL_BYTES or not _looks_like_image(content_type, body):
        return False

    path = _thumbnail_cache_path(cfg, note_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
    return True


def _parse_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None
    text = text.replace(",", "").replace(" ", "")
    multiplier = 1.0
    if text.endswith(("万", "w", "W")):
        multiplier = 10_000.0
        text = text[:-1]
    elif text.endswith(("亿",)):
        multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith(("k", "K")):
        multiplier = 1_000.0
        text = text[:-1]
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    return round(float(match.group(0)) * multiplier)


def _iso_from_xhs_time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and not value.strip().isdigit():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                UTC
            ).isoformat()
        except ValueError:
            return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:
        ts = ts / 1000.0
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _existing_posts(db_path: Path, note_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" * len(note_ids))
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""
            SELECT *
            FROM xhs_saved_posts
            WHERE note_id IN ({placeholders})
            """,
            note_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return {row["note_id"]: dict(row) for row in rows}


def _known_note_ids(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT note_id FROM xhs_saved_posts").fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        con.close()
    return {str(row[0]).lower() for row in rows if row[0]}


_SCHEMA_NEW_COLS: dict[str, list[tuple[str, str]]] = {
    "xhs_saved_posts": [
        ("latest_seen_rank", "INTEGER"),
    ],
}


def _migrate_schema(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        for table, cols in _SCHEMA_NEW_COLS.items():
            existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            for col, col_type in cols:
                if col not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        con.commit()
    finally:
        con.close()


def _needs_fetch(existing: dict[str, Any] | None, now: datetime, refresh_days: int) -> bool:
    if not existing:
        return True
    if existing.get("fetch_status") != "ok":
        return True
    last = _parse_iso(existing.get("last_fetched_at"))
    if last is None:
        return True
    if refresh_days <= 0:
        return False
    return now - last.astimezone(UTC) >= timedelta(days=refresh_days)


def _needs_thumbnail_refresh(cfg: Any, existing: dict[str, Any] | None) -> bool:
    if not existing:
        return False
    note_id = str(existing.get("note_id") or "")
    return bool(existing.get("thumbnail_url")) and not _thumbnail_cached(cfg, note_id)


def _post_row(
    collected: dict[str, Any],
    existing: dict[str, Any] | None,
    now_iso: str,
    *,
    summary: dict[str, Any] | None = None,
    fetch_error: str | None = None,
) -> dict[str, Any]:
    note_id = str(collected["note_id"]).lower()
    user = summary.get("user") if summary and isinstance(summary.get("user"), dict) else {}
    images = summary.get("images") if summary and isinstance(summary.get("images"), list) else []
    videos = summary.get("videos") if summary and isinstance(summary.get("videos"), list) else []
    posted_at = (
        _iso_from_xhs_time(summary.get("time")) if summary else None
    ) or (existing or {}).get("posted_at")
    title = (
        (summary or {}).get("title")
        or (existing or {}).get("title")
        or collected.get("title")
        or ""
    )
    description = (
        (summary or {}).get("desc")
        or (existing or {}).get("description")
        or ""
    )
    source_url = (summary or {}).get("source_url") or collected.get("url") or ""
    first_seen_url = (existing or {}).get("first_seen_url") or collected.get("first_seen_url") or source_url
    first_seen = (existing or {}).get("saved_first_seen_at") or now_iso
    fetch_status = "ok" if summary else "error" if fetch_error else (existing or {}).get("fetch_status") or "pending"
    return {
        "note_id": note_id,
        "source_url": source_url,
        "first_seen_url": first_seen_url,
        "xsec_token": collected.get("xsec_token") or _query_param(source_url, "xsec_token"),
        "xsec_source": collected.get("xsec_source") or _query_param(source_url, "xsec_source"),
        "title": str(title).strip(),
        "description": str(description).strip(),
        "author_user_id": user.get("id") or (existing or {}).get("author_user_id") or "",
        "author_nickname": user.get("nickname") or (existing or {}).get("author_nickname") or "",
        "note_type": (summary or {}).get("type") or (existing or {}).get("note_type") or "",
        "posted_at": posted_at,
        "thumbnail_url": (images[0] if images else "") or (existing or {}).get("thumbnail_url") or "",
        "image_urls_json": json.dumps(images or json.loads((existing or {}).get("image_urls_json") or "[]"), ensure_ascii=False),
        "video_urls_json": json.dumps(videos or json.loads((existing or {}).get("video_urls_json") or "[]"), ensure_ascii=False),
        "saved_first_seen_at": first_seen,
        "saved_last_seen_at": now_iso,
        "latest_seen_rank": collected.get("latest_seen_rank") or (existing or {}).get("latest_seen_rank"),
        "last_fetched_at": now_iso if summary else (existing or {}).get("last_fetched_at"),
        "fetch_status": fetch_status,
        "fetch_error": (fetch_error or "")[:500],
        "raw_json": json.dumps((summary or {}).get("raw") or json.loads((existing or {}).get("raw_json") or "{}"), ensure_ascii=False, sort_keys=True),
    }


def _snapshot_row(summary: dict[str, Any], note_id: str, now_iso: str) -> dict[str, Any]:
    interact = summary.get("interact") if isinstance(summary.get("interact"), dict) else {}
    raw = {
        "note_id": note_id,
        "source_url": summary.get("source_url"),
        "interact": interact,
    }
    return {
        "note_id": note_id,
        "snapshot_at": now_iso,
        "liked_count": _parse_count(interact.get("liked")),
        "collected_count": _parse_count(interact.get("collected")),
        "comment_count": _parse_count(interact.get("comments")),
        "share_count": _parse_count(interact.get("shares")),
        "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def _collection_row(state: dict[str, Any], notes: list[dict[str, Any]], now_iso: str) -> dict[str, Any]:
    raw = {
        "href": state.get("href"),
        "title": state.get("title"),
        "clicked_saved_tab": bool(state.get("clickedSaved")),
        "scrolls": state.get("scrolls"),
        "count": len(notes),
        "expected_count": state.get("expectedCount"),
        "incremental": state.get("incremental"),
        "stopped_for_overlap": state.get("stoppedForOverlap"),
        "overlap_run": state.get("overlapRun"),
        "overlap_stop": state.get("overlapStop"),
        "known_id_count": state.get("knownIdCount"),
    }
    return {
        "collected_at": now_iso,
        "source_url": str(state.get("href") or ""),
        "source_title": str(state.get("title") or ""),
        "clicked_saved_tab": 1 if state.get("clickedSaved") else 0,
        "note_count": len(notes),
        "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def _sync(t: Tracker, *, force_deep_backfill: bool = False) -> None:
    _migrate_schema(t.cfg.db_path)

    max_scrolls = _env_int("XHS_SAVED_MAX_SCROLLS", DEFAULT_MAX_SCROLLS, min_value=1, max_value=500)
    scroll_delay_ms = _env_int(
        "XHS_SAVED_SCROLL_DELAY_MS",
        DEFAULT_SCROLL_DELAY_MS,
        min_value=250,
        max_value=5000,
    )
    detail_limit = _env_int("XHS_SAVED_DETAIL_LIMIT", DEFAULT_DETAIL_LIMIT, max_value=500)
    refresh_days = _env_int("XHS_SAVED_REFRESH_DAYS", DEFAULT_REFRESH_DAYS, max_value=3650)
    overlap_stop = _env_int("XHS_SAVED_OVERLAP_STOP", DEFAULT_OVERLAP_STOP, max_value=500)
    deep_backfill = force_deep_backfill or _env_bool("XHS_SAVED_DEEP_BACKFILL")
    profile_url = (os.environ.get("XHS_SAVED_PROFILE_URL") or "").strip() or None
    use_active_tab = _env_bool("XHS_SAVED_USE_ACTIVE_TAB")
    chrome_profile = (os.environ.get("XHS_CHROME_PROFILE") or "Default").strip() or "Default"

    now = datetime.now(UTC)
    now_iso = now.isoformat()
    known_ids = _known_note_ids(t.cfg.db_path)

    state = _collect_saved_posts(
        profile_url=profile_url,
        use_active_tab=use_active_tab,
        max_scrolls=max_scrolls,
        scroll_delay_ms=scroll_delay_ms,
        known_note_ids=known_ids,
        overlap_stop=overlap_stop,
        deep_backfill=deep_backfill,
    )
    notes = _dedupe_collected_notes(state.get("notes") if isinstance(state.get("notes"), list) else [])
    note_ids = [str(item["note_id"]).lower() for item in notes]
    existing = _existing_posts(t.cfg.db_path, note_ids)

    t.upsert(
        "xhs_saved_collections",
        [_collection_row(state, notes, now_iso)],
        key=["collected_at"],
    )

    cookies: dict[str, str] | None = None
    candidates = [
        item for item in notes
        if _needs_fetch(existing.get(item["note_id"]), now, refresh_days)
        or _needs_thumbnail_refresh(t.cfg, existing.get(item["note_id"]))
    ]
    if detail_limit > 0:
        candidates = candidates[:detail_limit]
    candidate_ids = {item["note_id"] for item in candidates}

    post_rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    errors = 0
    for item in notes:
        note_id = item["note_id"]
        if note_id not in candidate_ids:
            post_rows.append(_post_row(item, existing.get(note_id), now_iso))
            continue
        try:
            if cookies is None:
                cookies = _load_xhs_cookies(chrome_profile)
            url = item.get("url") or item.get("first_seen_url") or f"https://www.xiaohongshu.com/explore/{note_id}"
            summary = _fetch_note_summary(url, note_id, cookies)
            post_rows.append(_post_row(item, existing.get(note_id), now_iso, summary=summary))
            snapshot_rows.append(_snapshot_row(summary, note_id, now_iso))
        except Exception as e:
            errors += 1
            t.log.warning("xhs_saved: note %s detail fetch failed: %s", note_id, e)
            post_rows.append(
                _post_row(
                    item,
                    existing.get(note_id),
                    now_iso,
                    fetch_error=str(e),
                )
            )

    if post_rows:
        t.upsert("xhs_saved_posts", post_rows, key=["note_id"])
    if snapshot_rows:
        t.upsert("xhs_saved_post_snapshots", snapshot_rows, key=["note_id", "snapshot_at"])

    cached_thumbnails = 0
    for row in post_rows:
        note_id = row.get("note_id")
        thumbnail_url = row.get("thumbnail_url")
        if not note_id or not thumbnail_url or _thumbnail_cached(t.cfg, note_id):
            continue
        try:
            if cookies is None:
                cookies = _load_xhs_cookies(chrome_profile)
            if _cache_thumbnail(t.cfg, note_id, thumbnail_url, cookies):
                cached_thumbnails += 1
        except Exception as e:
            t.log.warning("xhs_saved: thumbnail cache failed for %s: %s", note_id, e)

    t.cursor.set(now_iso)
    t.log.info(
        "xhs_saved: discovered %d saved posts, fetched %d details, cached %d thumbnails, %d errors",
        len(notes),
        len(snapshot_rows),
        cached_thumbnails,
        errors,
    )


def sync(t: Tracker) -> None:
    _sync(t, force_deep_backfill=False)


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Re-run saved-tab collection.

    Historical depth is controlled by XHS_SAVED_MAX_SCROLLS. The start/end
    arguments are accepted for tracker compatibility.
    """
    t.cursor.set("")
    _sync(t, force_deep_backfill=True)
