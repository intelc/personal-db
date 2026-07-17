"""Xiaohongshu / RedNote own-post status ingest.

This tracker intentionally uses the user's logged-in Chrome session instead of
raw Xiaohongshu API endpoints. The companion xhs-saved-posts workflow found
that the browser UI plus page initial state is the durable path: authenticated
API calls can return risk/account errors even when Chrome cookies are valid.
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
from datetime import UTC, datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
from pathlib import Path
from typing import Any

import requests

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError as exc:
    raise ImportError(
        "the cryptography package is required by the xhs tracker (it decrypts "
        "Chrome's cookie encryption key). Install it with: "
        "pip install 'personal_db[xhs]'"
    ) from exc

from personal_db.tracker import Tracker

NOTE_ID_RE = re.compile(
    r"(?:explore|user/profile/[^/]+)/([0-9a-f]{24})(?:[/?#]|$)", re.I
)
INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{[\s\S]*?\})\s*</script>"
)

DEFAULT_MAX_SCROLLS = 12
DEFAULT_POST_LIMIT = 30
DEFAULT_SCROLL_DELAY_MS = 900
COLLECT_TIMEOUT_S = 180
CREATOR_COLLECT_TIMEOUT_S = 180
CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
CREATOR_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"
BEIJING = timezone(timedelta(hours=8))
ARCHIVE_LABELS = {"仅自己可见", "已归档", "归档"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

TIERS: list[tuple[int, int]] = [
    (2, 1 * 3600),
    (7, 3 * 3600),
    (180, 24 * 3600),
]
DUE_TOLERANCE_S = 300


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 500) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _extract_note_id(url: str | None) -> str | None:
    if not url:
        return None
    match = NOTE_ID_RE.search(url)
    return match.group(1).lower() if match else None


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
        raise RuntimeError("osascript is required on macOS for the xhs tracker") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(f"Chrome automation failed: {detail}") from e
    return result.stdout.strip()


def _execute_chrome_js(js: str, *, timeout: int = 60) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        tmp = Path(f.name)
    try:
        quoted = str(tmp).replace('"', '\\"')
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


def _open_chrome_temp_tab(url: str) -> dict[str, int | bool]:
    escaped = _applescript_string(url)
    script = f"""tell application "Google Chrome"
  activate
  set createdWindow to false
  set previousTabId to 0
  if (count of windows) = 0 then
    make new window
    set createdWindow to true
  else
    set previousTabId to id of active tab of front window
  end if
  set targetWindow to front window
  set targetWindowId to id of targetWindow
  tell targetWindow
    set newTab to make new tab at end of tabs with properties {{URL:"{escaped}"}}
    set tempTabId to id of newTab
    set active tab index to count of tabs
  end tell
  return (createdWindow as string) & "|||" & (targetWindowId as string) & "|||" & (previousTabId as string) & "|||" & (tempTabId as string)
end tell"""
    raw = _run_osascript(script, timeout=30)
    created_raw, window_raw, previous_raw, temp_raw = raw.split("|||")
    return {
        "created_window": created_raw.lower() == "true",
        "window_id": int(window_raw),
        "previous_tab_id": int(previous_raw),
        "temp_tab_id": int(temp_raw),
    }


def _close_chrome_temp_tab(handle: dict[str, int | bool]) -> None:
    created_window = "true" if handle["created_window"] else "false"
    window_id = int(handle["window_id"])
    previous_tab_id = int(handle["previous_tab_id"])
    temp_tab_id = int(handle["temp_tab_id"])
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
    delete (first tab of targetWindow whose id is {temp_tab_id})
  end try
  if {previous_tab_id} > 0 then
    tell targetWindow
      set i to 1
      repeat with t in tabs
        if ((id of t) as string) is "{previous_tab_id}" then
          set active tab index to i
          exit repeat
        end if
        set i to i + 1
      end repeat
    end tell
  end if
end tell"""
    _run_osascript(script, timeout=30)


def _parse_json(raw: str, context: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"xhs: Chrome returned invalid JSON for {context}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"xhs: Chrome returned non-object JSON for {context}")
    return data


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


def _text_value(value: Any) -> str:
    return "" if value is None else str(value)


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


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
        "raw": {
            "noteId": _first(note.get("noteId"), note.get("note_id"), note.get("id"), note_id),
            "title": _first(note.get("title"), note.get("displayTitle"), note.get("display_title"), ""),
            "type": note.get("type") or "",
            "time": note.get("time"),
            "user": note.get("user"),
            "interactInfo": interact,
        },
    }


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


def _collect_creator_manager_notes(
    *,
    max_scrolls: int,
    scroll_delay_ms: int,
) -> list[dict[str, Any]]:
    tab_handle = _open_chrome_temp_tab(CREATOR_MANAGER_URL)
    try:
        time.sleep(6.0)
        start_js = f"""
(() => {{
  const maxScrolls = {json.dumps(max_scrolls)};
  const delayMs = {json.dumps(scroll_delay_ms)};
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const noteIdFrom = el => {{
    const raw = el.getAttribute("data-impression") || "";
    try {{
      return JSON.parse(raw)?.noteTarget?.value?.noteId || "";
    }} catch {{
      return "";
    }}
  }};
  const collect = () => Array.from(document.querySelectorAll(".note"))
    .map(el => {{
      const img = Array.from(el.querySelectorAll("img"))
        .map(img => img.currentSrc || img.src || "")
        .find(src => src && !src.startsWith("data:")) || "";
      return {{
        note_id: noteIdFrom(el),
        text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim(),
        thumbnail_url: img.split("?")[0],
      }};
    }})
    .filter(x => x.note_id);
  const clickTab = label => {{
    const candidates = Array.from(document.querySelectorAll("div, span, button"))
      .filter(el => ((el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim()).startsWith(label))
      .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
    if (candidates[0]) {{
      candidates[0].click();
      return true;
    }}
    return false;
  }};

  window.__personalDbXhsCreator = {{ state: "running", rows: collect() }};
  (async () => {{
    try {{
      clickTab("已发布");
      await sleep(1800);
      clickTab("全部笔记");
      for (let i = 0; i < 8 && collect().length === 0; i++) {{
        await sleep(1000);
      }}
      let stable = 0;
      let prevCount = 0;
      for (let i = 0; i < maxScrolls; i++) {{
        const scrollers = [document.scrollingElement, ...Array.from(document.querySelectorAll("div"))]
          .filter(el => el && el.scrollHeight > el.clientHeight + 50)
          .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
        for (const el of scrollers.slice(0, 5)) {{
          el.scrollTop = el.scrollHeight;
        }}
        window.scrollTo(0, document.documentElement.scrollHeight);
        await sleep(delayMs);
        const rows = collect();
        stable = rows.length === prevCount ? stable + 1 : 0;
        prevCount = rows.length;
        window.__personalDbXhsCreator = {{
          state: "running",
          pass: i + 1,
          count: rows.length,
          rows,
        }};
        if (stable >= 3) break;
      }}
      window.__personalDbXhsCreator = {{
        state: "done",
        count: collect().length,
        rows: collect(),
      }};
    }} catch (error) {{
      window.__personalDbXhsCreator = {{
        state: "error",
        message: String(error && error.message || error),
      }};
    }}
  }})();
  return "started";
}})()
"""
        _execute_chrome_js(start_js)
        deadline = time.monotonic() + CREATOR_COLLECT_TIMEOUT_S
        while time.monotonic() < deadline:
            time.sleep(1.5)
            raw = _execute_chrome_js(
                "JSON.stringify(window.__personalDbXhsCreator || null)",
                timeout=20,
            )
            state = _parse_json(raw, "creator note-manager collection") if raw else {}
            if state.get("state") == "done":
                rows = state.get("rows") if isinstance(state.get("rows"), list) else []
                return [row for row in rows if isinstance(row, dict)]
            if state.get("state") == "error":
                raise RuntimeError(
                    f"xhs: creator note-manager collector failed: {state.get('message')}"
                )
        raise RuntimeError("xhs: timed out collecting creator note-manager rows")
    finally:
        with suppress(Exception):
            _close_chrome_temp_tab(tab_handle)

def _fetch_note_summary(
    url: str,
    note_id: str,
    cookies: dict[str, str],
) -> dict[str, Any]:
    html = _fetch_note_html(url, cookies)
    state = _extract_initial_state(html)
    note = _find_note_in_state(state, note_id)
    return _note_summary(note, url, note_id)


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


CREATOR_ROW_RE = re.compile(
    r"^(?:(?P<label>仅自己可见|已归档|归档|审核中|未通过)\s+)?"
    r"(?:(?P<duration>\d{2}:\d{2}(?::\d{2})?)\s+)?"
    r"(?P<title>.+?)\s+发布于\s+"
    r"(?P<posted>\d{4}年\d{2}月\d{2}日\s+\d{2}:\d{2})\s+"
    r"(?P<views>[\d,万亿wWkK.]+)\s+"
    r"(?P<comments>[\d,万亿wWkK.]+)\s+"
    r"(?P<likes>[\d,万亿wWkK.]+)\s+"
    r"(?P<collects>[\d,万亿wWkK.]+)\s+"
    r"(?P<shares>[\d,万亿wWkK.]+)"
)


def _parse_creator_posted_at(value: str) -> str | None:
    try:
        dt = datetime.strptime(value, "%Y年%m月%d日 %H:%M").replace(tzinfo=BEIJING)
    except ValueError:
        return None
    return dt.astimezone(UTC).isoformat()


def _parse_creator_row(row: dict[str, Any]) -> dict[str, Any] | None:
    note_id = str(row.get("note_id") or "").lower()
    text = str(row.get("text") or "")
    if not note_id or not text:
        return None
    match = CREATOR_ROW_RE.search(text)
    if not match:
        return None
    label = match.group("label") or ""
    return {
        "note_id": note_id,
        "title": match.group("title").strip(),
        "posted_at": _parse_creator_posted_at(match.group("posted")),
        "thumbnail_url": row.get("thumbnail_url") or "",
        "visibility_label": label,
        "is_archived": 1 if label in ARCHIVE_LABELS else 0,
        "view_count": _parse_count(match.group("views")),
        "comment_count": _parse_count(match.group("comments")),
        "liked_count": _parse_count(match.group("likes")),
        "collected_count": _parse_count(match.group("collects")),
        "share_count": _parse_count(match.group("shares")),
        "raw_text": text,
    }


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


def _cadence_seconds(posted_at: str | None, now: datetime) -> int | None:
    posted = _parse_iso(posted_at)
    if posted is None:
        return 24 * 3600
    age_days = (now - posted.astimezone(UTC)).total_seconds() / 86400
    for max_age, interval in TIERS:
        if age_days < max_age:
            return interval
    return None


def _existing_notes(db_path: Path, note_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" * len(note_ids))
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""
            SELECT note_id, posted_at, first_seen_at, last_fetched_at, title,
                   description, permalink, thumbnail_url, visibility_label,
                   is_archived, creator_last_seen_at
            FROM xhs_notes
            WHERE note_id IN ({placeholders})
            """,
            note_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return {row["note_id"]: dict(row) for row in rows}


def _due_note_ids(db_path: Path, note_ids: list[str], now: datetime) -> set[str]:
    if not note_ids:
        return set()
    placeholders = ",".join("?" * len(note_ids))
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            f"""
            SELECT n.note_id, n.posted_at,
                   (SELECT MAX(snapshot_at)
                    FROM xhs_note_snapshots s
                    WHERE s.note_id = n.note_id
                      AND s.view_count IS NOT NULL) AS last_snapshot
            FROM xhs_notes n
            WHERE n.note_id IN ({placeholders})
            """,
            note_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return set(note_ids)
    finally:
        con.close()

    known = {row[0] for row in rows}
    due = set(note_ids) - known
    for note_id, posted_at, last_snapshot in rows:
        if last_snapshot is None:
            due.add(note_id)
            continue
        interval = _cadence_seconds(posted_at, now)
        if interval is None:
            continue
        last = _parse_iso(last_snapshot)
        if last is None:
            due.add(note_id)
            continue
        elapsed = (now - last.astimezone(UTC)).total_seconds()
        if elapsed >= interval - DUE_TOLERANCE_S:
            due.add(note_id)
    return due


def _flatten_note(
    summary: dict[str, Any],
    collected: dict[str, Any],
    existing: dict[str, Any] | None,
    now_iso: str,
) -> dict[str, Any]:
    note_id = str(summary.get("note_id") or collected["note_id"]).lower()
    user = summary.get("user") if isinstance(summary.get("user"), dict) else {}
    images = summary.get("images") if isinstance(summary.get("images"), list) else []
    posted_at = (
        _iso_from_xhs_time(summary.get("time"))
        or collected.get("posted_at")
        or (existing or {}).get("posted_at")
        or now_iso
    )
    first_seen_at = (existing or {}).get("first_seen_at") or now_iso
    title = (summary.get("title") or collected.get("title") or "").strip()
    desc = (summary.get("desc") or (existing or {}).get("description") or "").strip()
    permalink = summary.get("source_url") or collected.get("url") or ""
    thumbnail = (
        (images[0] if images else "")
        or collected.get("thumbnail_url")
        or (existing or {}).get("thumbnail_url")
        or ""
    )
    return {
        "note_id": note_id,
        "xhs_user_id": user.get("id") or "",
        "author_nickname": user.get("nickname") or "",
        "note_type": summary.get("type") or "",
        "title": title or (existing or {}).get("title") or "",
        "description": desc,
        "permalink": permalink,
        "thumbnail_url": thumbnail,
        "posted_at": posted_at,
        "visibility_label": collected.get("visibility_label")
        or (existing or {}).get("visibility_label")
        or "",
        "is_archived": int(
            collected.get("is_archived") if collected.get("is_archived") is not None
            else (existing or {}).get("is_archived") or 0
        ),
        "creator_last_seen_at": collected.get("creator_last_seen_at")
        or (existing or {}).get("creator_last_seen_at")
        or "",
        "first_seen_at": first_seen_at,
        "last_fetched_at": now_iso,
    }


def _snapshot_row(summary: dict[str, Any], note_id: str, now_iso: str) -> dict[str, Any]:
    interact = summary.get("interact") if isinstance(summary.get("interact"), dict) else {}
    raw = {
        "note_id": note_id,
        "source_url": summary.get("source_url"),
        "interact": interact,
        "raw": summary.get("raw"),
    }
    return {
        "note_id": note_id,
        "snapshot_at": now_iso,
        "view_count": _parse_count(interact.get("views")),
        "liked_count": _parse_count(interact.get("liked")),
        "collected_count": _parse_count(interact.get("collected")),
        "comment_count": _parse_count(interact.get("comments")),
        "share_count": _parse_count(interact.get("shares")),
        "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def _profile_count(profile_text: str, labels: list[str]) -> int | None:
    chunks = [c for c in re.split(r"\s+", profile_text) if c]
    for i, chunk in enumerate(chunks):
        if chunk not in labels:
            continue
        neighbors = []
        if i + 1 < len(chunks):
            neighbors.append(chunks[i + 1])
        if i > 0:
            neighbors.append(chunks[i - 1])
        for value in neighbors:
            parsed = _parse_count(value)
            if parsed is not None:
                return parsed
    joined = " ".join(chunks)
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*([\d.,万亿wWkK]+)", joined)
        if match:
            return _parse_count(match.group(1))
        match = re.search(rf"([\d.,万亿wWkK]+)\s*{re.escape(label)}", joined)
        if match:
            return _parse_count(match.group(1))
    return None


def _profile_snapshot(
    profile_url: str,
    collected: dict[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    profile = collected.get("profile") if isinstance(collected.get("profile"), dict) else {}
    text = str(profile.get("text") or "")
    return {
        "profile_url": profile_url,
        "snapshot_at": now_iso,
        "nickname": str(profile.get("title") or "").replace(" - 小红书", "").strip(),
        "following_count": _profile_count(text, ["关注"]),
        "followers_count": _profile_count(text, ["粉丝"]),
        "liked_collected_count": _profile_count(text, ["获赞与收藏", "获赞"]),
        "visible_note_count": len(collected.get("notes") or []),
        "raw_json": json.dumps(profile, ensure_ascii=False, sort_keys=True),
    }


def _creator_manager_snapshot(
    rows: list[dict[str, Any]],
    parsed_rows: list[dict[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    active_count = sum(1 for row in parsed_rows if not row.get("is_archived"))
    archived_count = len(parsed_rows) - active_count
    raw = {
        "source": CREATOR_MANAGER_URL,
        "row_count": len(rows),
        "parsed_count": len(parsed_rows),
        "active_count": active_count,
        "archived_count": archived_count,
    }
    return {
        "profile_url": CREATOR_MANAGER_URL,
        "snapshot_at": now_iso,
        "nickname": "",
        "following_count": None,
        "followers_count": None,
        "liked_collected_count": None,
        "visible_note_count": active_count,
        "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


_SCHEMA_NEW_COLS: dict[str, list[tuple[str, str]]] = {
    "xhs_notes": [
        ("visibility_label", "TEXT"),
        ("is_archived", "INTEGER NOT NULL DEFAULT 0"),
        ("creator_last_seen_at", "TEXT"),
    ],
    "xhs_note_snapshots": [
        ("view_count", "INTEGER"),
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


def sync(t: Tracker) -> None:
    _migrate_schema(t.cfg.db_path)

    creator_scrolls = _env_int("XHS_CREATOR_MAX_SCROLLS", 30, max_value=200)
    post_limit = _env_int("XHS_POST_LIMIT", DEFAULT_POST_LIMIT, max_value=200)
    scroll_delay_ms = _env_int(
        "XHS_SCROLL_DELAY_MS",
        DEFAULT_SCROLL_DELAY_MS,
        min_value=250,
        max_value=5000,
    )

    now = datetime.now(UTC)
    now_iso = now.isoformat()

    raw_creator_rows = _collect_creator_manager_notes(
        max_scrolls=creator_scrolls,
        scroll_delay_ms=scroll_delay_ms,
    )
    creator_rows = [
        parsed
        for row in raw_creator_rows
        if (parsed := _parse_creator_row(row))
    ]

    combined: dict[str, dict[str, Any]] = {}
    for item in creator_rows:
        combined[item["note_id"]] = {
            **item,
            "url": f"https://www.xiaohongshu.com/explore/{item['note_id']}",
            "first_seen_url": f"https://www.xiaohongshu.com/explore/{item['note_id']}",
            "thumbnail_url": item.get("thumbnail_url") or "",
            "creator_last_seen_at": now_iso,
        }

    notes = list(combined.values())[:post_limit]
    note_ids = [str(item["note_id"]).lower() for item in notes]
    existing = _existing_notes(t.cfg.db_path, note_ids)
    due = _due_note_ids(t.cfg.db_path, note_ids, now)

    t.upsert(
        "xhs_account_snapshots",
        [_creator_manager_snapshot(raw_creator_rows, creator_rows, now_iso)],
        key=["profile_url", "snapshot_at"],
    )

    note_rows: list[dict[str, Any]] = []
    for item in notes:
        note_id = str(item["note_id"]).lower()
        note_rows.append(
            _flatten_note(
                {
                    "note_id": note_id,
                    "title": item.get("title") or "",
                    "time": item.get("posted_at"),
                    "source_url": item.get("url") or item.get("first_seen_url") or "",
                    "images": [item["thumbnail_url"]] if item.get("thumbnail_url") else [],
                },
                item,
                existing.get(note_id),
                now_iso,
            )
        )

    snapshot_rows: list[dict[str, Any]] = []
    errors = 0

    for item in notes:
        note_id = str(item["note_id"]).lower()
        if note_id not in due:
            continue
        url = item.get("url") or item.get("first_seen_url") or ""
        if not url:
            errors += 1
            continue
        try:
            manager_interact = {
                "views": item.get("view_count"),
                "liked": item.get("liked_count"),
                "collected": item.get("collected_count"),
                "comments": item.get("comment_count"),
                "shares": item.get("share_count"),
            }
            summary: dict[str, Any] = {
                "note_id": note_id,
                "title": item.get("title") or "",
                "time": item.get("posted_at"),
                "source_url": url,
                "images": [item["thumbnail_url"]] if item.get("thumbnail_url") else [],
                "interact": manager_interact,
            }
        except Exception as e:
            t.log.warning("xhs: note %s snapshot failed: %s", note_id, e)
            errors += 1
            if note_id not in existing:
                note_rows.append(
                    _flatten_note(
                        {"note_id": note_id, "source_url": url},
                        item,
                        None,
                        now_iso,
                    )
                )
            continue
        note_rows.append(_flatten_note(summary, item, existing.get(note_id), now_iso))
        snapshot_rows.append(_snapshot_row(summary, note_id, now_iso))

    if note_rows:
        t.upsert("xhs_notes", note_rows, key=["note_id"])
    if snapshot_rows:
        t.upsert("xhs_note_snapshots", snapshot_rows, key=["note_id", "snapshot_at"])
    t.cursor.set(now_iso)
    t.log.info(
        "xhs: discovered %d notes, snapshotted %d due notes, %d errors",
        len(notes),
        len(snapshot_rows),
        errors,
    )


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Re-run discovery and mark the cursor fresh.

    The start/end arguments are accepted for tracker compatibility. XHS profile
    discovery is UI based, so historical depth is controlled by
    XHS_CREATOR_MAX_SCROLLS and XHS_POST_LIMIT.
    """
    t.cursor.set("")
    sync(t)
