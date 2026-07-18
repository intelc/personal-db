"""User-triggered Plaid Link action.

Run via:
  curl -X POST http://127.0.0.1:8765/api/v1/trackers/plaid/actions/link_item
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from personal_db.db import connect
from personal_db.oauth import _get_ssl_context

API_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}

MAX_TOKEN_BACKUPS = 100
MAX_LINK_EVENTS = 200
ACCOUNT_GROUPS = {"cash", "credit_card", "investments", "other"}
PARENT_OWNERS = {"parents"}


class _PortInUse(RuntimeError):
    pass


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _read_env_file(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _env(cfg, name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _read_env_file(cfg.root).get(name) or default


def _csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [part.strip() for part in value.split(",") if part.strip()]


def _api_host(cfg) -> str:
    env = (_env(cfg, "PLAID_ENV", "development") or "development").strip().lower()
    if env not in API_HOSTS:
        raise RuntimeError("PLAID_ENV must be one of: sandbox, development, production")
    return API_HOSTS[env]


def _credentials(cfg) -> tuple[str, str]:
    client_id = _env(cfg, "PLAID_CLIENT_ID")
    secret = _env(cfg, "PLAID_SECRET")
    if not client_id or not secret:
        raise RuntimeError("Set PLAID_CLIENT_ID and PLAID_SECRET before linking Items")
    return client_id, secret


def _post(cfg, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    client_id, secret = _credentials(cfg)
    r = requests.post(
        f"{_api_host(cfg)}{path}",
        json={"client_id": client_id, "secret": secret, **payload},
        timeout=30,
    )
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            r.raise_for_status()
        raise RuntimeError(body.get("error_message") or str(body))
    return r.json()


def _state_path(cfg) -> Path:
    path = cfg.state_dir / "plaid" / "items.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    return path


def _backup_dir(cfg) -> Path:
    path = cfg.state_dir / "plaid" / "backups"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _load_state(cfg) -> dict[str, Any]:
    path = _state_path(cfg)
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text())


def _link_events_path(cfg) -> Path:
    path = cfg.state_dir / "plaid" / "link_events.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    return path


def _record_link_event(cfg, event: str, metadata: dict[str, Any] | None = None, error: str | None = None) -> None:
    path = _link_events_path(cfg)
    try:
        data = json.loads(path.read_text()) if path.exists() else {"events": []}
    except json.JSONDecodeError:
        data = {"events": []}
    institution = ((metadata or {}).get("institution") or {})
    entry = {
        "ts": _now_iso(),
        "event": event,
        "institution_id": institution.get("institution_id"),
        "institution_name": institution.get("name"),
        "link_session_id": (metadata or {}).get("link_session_id"),
        "account_count": len((metadata or {}).get("accounts") or []),
        "error": error,
    }
    events = data.setdefault("events", [])
    events.append(entry)
    del events[:-MAX_LINK_EVENTS]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _backup_state(cfg, reason: str) -> Path | None:
    source = _state_path(cfg)
    if not source.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = _backup_dir(cfg) / f"items-{stamp}-{reason}.json"
    backup.write_bytes(source.read_bytes())
    os.chmod(backup, 0o600)
    _prune_backups(cfg)
    return backup


def _prune_backups(cfg) -> None:
    backups = sorted(_backup_dir(cfg).glob("items-*.json"), key=lambda p: p.stat().st_mtime)
    for path in backups[:-MAX_TOKEN_BACKUPS]:
        path.unlink(missing_ok=True)


def _write_state(cfg, state: dict[str, Any], *, backup_reason: str) -> Path:
    path = _state_path(cfg)
    _backup_state(cfg, f"before-{backup_reason}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    backup = _backup_state(cfg, f"after-{backup_reason}")
    return backup or path


def _save_item(cfg, item: dict[str, Any]) -> None:
    state = _load_state(cfg)
    items = state.setdefault("items", [])
    items[:] = [existing for existing in items if existing.get("item_id") != item.get("item_id")]
    items.append(item)
    state["updated_at"] = _now_iso()
    _write_state(cfg, state, backup_reason="save-item")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _account_group(account: dict[str, Any]) -> str:
    typ = (account.get("type") or "").lower()
    subtype = (account.get("subtype") or "").lower()
    if typ == "investment":
        return "investments"
    if typ == "credit":
        return "credit_card"
    if typ == "depository" and subtype in {
        "checking",
        "savings",
        "money market",
        "cash management",
        "prepaid",
    }:
        return "cash"
    return "other"


def _account_display_label(account: dict[str, Any]) -> str:
    institution = account.get("institution_name") or "Unknown institution"
    name = account.get("official_name") or account.get("name") or "Unknown account"
    mask = account.get("mask")
    suffix = f" ...{mask}" if mask and str(mask) not in str(name) else ""
    return f"{institution} - {name}{suffix}"


def _account_labels_path(cfg) -> Path:
    path = cfg.trackers_dir / "plaid" / "account_labels.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_account_labels_file(cfg) -> dict[str, Any]:
    path = _account_labels_path(cfg)
    if not path.exists():
        return {"accounts": {}}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {"accounts": {}}


def _write_account_labels_file(cfg, data: dict[str, Any]) -> None:
    path = _account_labels_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _owner_flags(owner: str) -> tuple[bool, bool]:
    is_parent = owner.strip().lower() in PARENT_OWNERS
    return (not is_parent, is_parent)


def _current_accounts(cfg) -> list[dict[str, Any]]:
    con = connect(cfg.db_path, read_only=True)
    try:
        cur = con.execute(
            """
            SELECT account_id, institution_name, name, official_name, mask, type, subtype,
                   current_balance, available_balance, iso_currency_code, balance_as_of
            FROM plaid_accounts
            WHERE account_id IS NOT NULL
            ORDER BY institution_name, name
            """
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _seed_account_labels(cfg, accounts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    data = _read_account_labels_file(cfg)
    labels = data.get("accounts")
    if not isinstance(labels, dict):
        labels = {}
    changed = False
    for account in accounts:
        account_id = account.get("account_id")
        if not account_id:
            continue
        existing = labels.get(account_id)
        if not isinstance(existing, dict):
            existing = {}
        owner = str(existing.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        seeded = {
            "export_enabled": _coerce_bool(existing.get("export_enabled"), True),
            "owner": owner,
            "account_group": str(existing.get("account_group") or _account_group(account)).strip(),
            "label": existing.get("label") or _account_display_label(account),
            "include_in_net_worth": include_in_net_worth,
            "parent_draw_source": parent_draw_source,
            "notes": existing.get("notes") or "",
        }
        if seeded["account_group"] not in ACCOUNT_GROUPS:
            seeded["account_group"] = _account_group(account)
        if seeded != existing:
            labels[account_id] = seeded
            changed = True
    if changed or not _account_labels_path(cfg).exists():
        data["notes"] = data.get("notes") or [
            "Set export_enabled to false to keep an account out of downstream finance views.",
            "Edit owner to 'parents' for accounts you manage for parents.",
            "Supported account_group values: cash, credit_card, investments, other.",
            "Credit-card payments and internal transfers are excluded from cashflow.",
        ]
        data["accounts"] = labels
        _write_account_labels_file(cfg, data)
    return labels


def _persist_account_settings(cfg, labels: dict[str, dict[str, Any]]) -> None:
    con = connect(cfg.db_path)
    con.executemany(
        """
        INSERT INTO plaid_account_labels(
          account_id, owner, account_group, label, include_in_net_worth,
          parent_draw_source, notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(account_id) DO UPDATE SET
          owner=excluded.owner,
          account_group=excluded.account_group,
          label=excluded.label,
          include_in_net_worth=excluded.include_in_net_worth,
          parent_draw_source=excluded.parent_draw_source,
          notes=excluded.notes,
          updated_at=excluded.updated_at
        """,
        [
            (
                account_id,
                row.get("owner") or "self",
                row.get("account_group") or "other",
                row.get("label") or "",
                1 if _coerce_bool(row.get("include_in_net_worth"), True) else 0,
                1 if _coerce_bool(row.get("parent_draw_source"), False) else 0,
                row.get("notes") or "",
            )
            for account_id, row in labels.items()
        ],
    )
    con.executemany(
        """
        INSERT INTO plaid_account_exports(account_id, export_enabled, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(account_id) DO UPDATE SET
          export_enabled=excluded.export_enabled,
          updated_at=excluded.updated_at
        """,
        [
            (account_id, 1 if _coerce_bool(row.get("export_enabled"), True) else 0)
            for account_id, row in labels.items()
        ],
    )
    con.commit()
    con.close()


def _link_token(cfg) -> str:
    days_raw = _env(cfg, "PLAID_TRANSACTIONS_DAYS_REQUESTED", "730") or "730"
    try:
        days = max(1, min(730, int(days_raw)))
    except ValueError:
        days = 730

    payload: dict[str, Any] = {
        "client_name": _env(cfg, "PLAID_CLIENT_NAME", "personal_db") or "personal_db",
        "language": "en",
        "country_codes": _csv(_env(cfg, "PLAID_COUNTRY_CODES"), ["US"]),
        "products": _csv(_env(cfg, "PLAID_PRODUCTS"), ["transactions"]),
        "optional_products": _csv(_env(cfg, "PLAID_OPTIONAL_PRODUCTS"), ["investments"]),
        "user": {"client_user_id": _env(cfg, "PLAID_CLIENT_USER_ID", "personal_db_user")},
    }
    requested_products = set(payload["products"]) | set(payload["optional_products"])
    if "transactions" in requested_products:
        payload["transactions"] = {"days_requested": days}
    redirect_uri = _env(cfg, "PLAID_REDIRECT_URI")
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri
    return _post(cfg, "/link/token/create", payload)["link_token"]


def _configured_redirect(cfg):
    uri = _env(cfg, "PLAID_REDIRECT_URI")
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("PLAID_REDIRECT_URI must be a local https://127.0.0.1 or https://localhost URL")
    if parsed.port is None:
        raise RuntimeError("PLAID_REDIRECT_URI must include an explicit local port")
    return parsed


def _exchange_public_token(cfg, public_token: str, metadata: dict[str, Any]) -> dict[str, Any]:
    _record_link_event(cfg, "exchange_started", metadata)
    exchanged = _post(cfg, "/item/public_token/exchange", {"public_token": public_token})
    access_token = exchanged["access_token"]
    item_id = exchanged["item_id"]
    _record_link_event(cfg, "exchange_succeeded", metadata)
    institution = metadata.get("institution") or {}
    item = {
        "item_id": item_id,
        "access_token": access_token,
        "institution_id": institution.get("institution_id"),
        "institution_name": institution.get("name"),
        "accounts": metadata.get("accounts") or [],
        "link_session_id": metadata.get("link_session_id"),
        "created_at": _now_iso(),
    }
    try:
        plaid_item = _post(cfg, "/item/get", {"access_token": access_token}).get("item") or {}
        item["institution_id"] = item.get("institution_id") or plaid_item.get("institution_id")
        item["products"] = plaid_item.get("products")
        item["available_products"] = plaid_item.get("available_products")
        item["billed_products"] = plaid_item.get("billed_products")
    except Exception:
        pass
    _save_item(cfg, item)
    _record_link_event(cfg, "item_saved", metadata)
    return {
        "ok": True,
        "item_id": item_id,
        "institution_name": item.get("institution_name"),
        "message": "Plaid Item saved. You can close this tab and run personal-db sync plaid.",
    }


def _html(link_token: str) -> bytes:
    page = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>personal_db Plaid Link</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 48px; max-width: 720px; line-height: 1.45; }}
    button {{ font: inherit; padding: 10px 14px; }}
    pre {{ background: #f5f5f5; padding: 12px; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>Connect a Plaid Item</h1>
  <p>Use this page once per institution. When Link succeeds, personal_db stores the Item token locally.</p>
  <button id="link">Connect institution</button>
  <pre id="out"></pre>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <script>
    const out = document.getElementById("out");
    const config = {{
      token: {json.dumps(link_token)},
      onSuccess: async (public_token, metadata) => {{
        out.textContent = "Saving Item...";
        const r = await fetch("/exchange", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{public_token, metadata}})
        }});
        const body = await r.json();
        out.textContent = JSON.stringify(body, null, 2);
      }},
      onExit: (err, metadata) => {{
        out.textContent = JSON.stringify({{err, metadata}}, null, 2);
      }}
    }};
    if (window.location.search.includes("oauth_state_id=")) {{
      config.receivedRedirectUri = window.location.href;
    }}
    const handler = Plaid.create(config);
    document.getElementById("link").onclick = () => handler.open();
    if (config.receivedRedirectUri) {{
      handler.open();
    }}
  </script>
</body>
</html>"""
    return page.encode()


def _port_accepts_connections(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _find_port() -> int:
    redirect = _configured_redirect(_CURRENT_CFG)
    if redirect is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((redirect.hostname, redirect.port))
            except OSError as exc:
                if _port_accepts_connections(redirect.hostname, redirect.port):
                    raise _PortInUse from exc
                raise RuntimeError(
                    f"PLAID_REDIRECT_URI port {redirect.port} is unavailable, "
                    "but no Plaid Link helper is listening on it yet. Wait a few "
                    "seconds and try again, or restart the daemon."
                ) from exc
        return redirect.port

    preferred = int(_env(_CURRENT_CFG, "PLAID_LINK_PORT", "9878") or "9878")
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Could not find a free local port for Plaid Link")


_CURRENT_CFG = None


def link_item(cfg) -> dict[str, Any]:
    """Start a short-lived local Plaid Link helper and open the browser."""
    global _CURRENT_CFG
    _CURRENT_CFG = cfg
    redirect = _configured_redirect(cfg)
    scheme = redirect.scheme if redirect is not None else "http"
    host = redirect.hostname if redirect is not None else "127.0.0.1"
    try:
        port = _find_port()
    except _PortInUse:
        url = f"{scheme}://{host}:{redirect.port}/"
        webbrowser.open(url)
        return {
            "ok": True,
            "url": url,
            "message": "Plaid Link helper is already running; opened the existing helper.",
        }
    host = redirect.hostname if redirect is not None else "127.0.0.1"
    redirect_path = redirect.path if redirect is not None else "/oauth-return"
    stop_event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path not in {"/", redirect_path}:
                self.send_error(404)
                return
            try:
                body = _html(_link_token(cfg))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_error(500, str(exc))

        def do_POST(self):
            if self.path != "/exchange":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode())
                result = _exchange_public_token(
                    cfg,
                    payload["public_token"],
                    payload.get("metadata") or {},
                )
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                stop_event.set()
            except Exception as exc:
                metadata = {}
                try:
                    metadata = payload.get("metadata") or {}
                except Exception:
                    metadata = {}
                _record_link_event(cfg, "exchange_failed", metadata, str(exc))
                body = json.dumps({"ok": False, "error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

    server = _ReusableThreadingHTTPServer((host, port), Handler)
    server.timeout = 5
    if redirect is not None and redirect.scheme == "https":
        server.socket = _get_ssl_context(cfg.state_dir).wrap_socket(server.socket, server_side=True)

    def _serve():
        deadline = time.time() + 15 * 60
        while time.time() < deadline and not stop_event.is_set():
            server.handle_request()
        server.server_close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    url = f"{scheme}://{host}:{port}/"
    webbrowser.open(url)
    return {
        "ok": True,
        "url": url,
        "message": "Opened Plaid Link helper. Repeat this action once per institution.",
    }


def backup_tokens(cfg) -> dict[str, Any]:
    """Create an immediate private backup of the linked Plaid Item token file."""
    state = _load_state(cfg)
    state["updated_at"] = _now_iso()
    backup = _write_state(cfg, state, backup_reason="manual")
    item_count = len(state.get("items") or [])
    return {
        "ok": True,
        "item_count": item_count,
        "backup_path": str(backup),
        "message": f"Backed up {item_count} Plaid Item token record(s).",
    }


def token_status(cfg) -> dict[str, Any]:
    """Report linked Item and backup counts without returning access tokens."""
    state = _load_state(cfg)
    items = [
        {
            "item_id": item.get("item_id"),
            "institution_id": item.get("institution_id"),
            "institution_name": item.get("institution_name"),
            "created_at": item.get("created_at"),
            "has_access_token": bool(item.get("access_token")),
        }
        for item in state.get("items", [])
    ]
    backups = sorted(_backup_dir(cfg).glob("items-*.json"))
    link_events = []
    events_path = _link_events_path(cfg)
    if events_path.exists():
        try:
            link_events = (json.loads(events_path.read_text()).get("events") or [])[-10:]
        except json.JSONDecodeError:
            link_events = []
    return {
        "ok": True,
        "state_path": str(_state_path(cfg)),
        "backup_dir": str(_backup_dir(cfg)),
        "item_count": len(items),
        "backup_count": len(backups),
        "items": items,
        "recent_link_events": link_events,
    }


def account_labels_status(cfg) -> dict[str, Any]:
    """Return editable Plaid account labels for the setup page."""
    accounts = _current_accounts(cfg)
    labels = _seed_account_labels(cfg, accounts)
    rows = []
    for account in accounts:
        account_id = account["account_id"]
        label = labels.get(account_id) or {}
        rows.append(
            {
                "account_id": account_id,
                "institution_name": account.get("institution_name"),
                "account_name": account.get("official_name") or account.get("name"),
                "mask": account.get("mask"),
                "type": account.get("type"),
                "subtype": account.get("subtype"),
                "current_balance": account.get("current_balance"),
                "available_balance": account.get("available_balance"),
                "iso_currency_code": account.get("iso_currency_code"),
                "balance_as_of": account.get("balance_as_of"),
                "export_enabled": _coerce_bool(label.get("export_enabled"), True),
                "owner": label.get("owner") or "self",
                "account_group": label.get("account_group") or _account_group(account),
                "label": label.get("label") or _account_display_label(account),
                "include_in_net_worth": _owner_flags(label.get("owner") or "self")[0],
                "parent_draw_source": _owner_flags(label.get("owner") or "self")[1],
                "notes": label.get("notes") or "",
            }
        )
    return {
        "ok": True,
        "labels_path": str(_account_labels_path(cfg)),
        "accounts": rows,
    }


def save_account_labels(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist edited account labels from the setup page.

    The browser only updates known Plaid account ids already present in the DB.
    The next Plaid sync re-materializes cashflow, net worth, and parent-draw
    tables from this yaml state.
    """
    accounts = _current_accounts(cfg)
    known = {account["account_id"]: account for account in accounts}
    labels = _seed_account_labels(cfg, accounts)
    submitted = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(submitted, list):
        raise RuntimeError("Expected payload.accounts to be a list")

    updated = 0
    for row in submitted:
        if not isinstance(row, dict):
            continue
        account_id = row.get("account_id")
        if account_id not in known:
            continue
        existing = labels.get(account_id) or {}
        export_enabled = _coerce_bool(
            row.get("export_enabled"),
            _coerce_bool(existing.get("export_enabled"), True),
        )
        group = str(row.get("account_group") or existing.get("account_group") or _account_group(known[account_id])).strip()
        if group not in ACCOUNT_GROUPS:
            raise RuntimeError(f"Invalid account_group for {account_id}: {group}")
        owner = str(row.get("owner") or existing.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        labels[account_id] = {
            "export_enabled": export_enabled,
            "owner": owner,
            "account_group": group,
            "label": str(row.get("label") or existing.get("label") or _account_display_label(known[account_id])).strip(),
            "include_in_net_worth": include_in_net_worth,
            "parent_draw_source": parent_draw_source,
            "notes": str(row.get("notes") or existing.get("notes") or "").strip(),
        }
        updated += 1

    data = _read_account_labels_file(cfg)
    data["notes"] = data.get("notes") or [
        "Set export_enabled to false to keep an account out of downstream finance views.",
        "Edit owner to 'parents' for accounts you manage for parents.",
        "Supported account_group values: cash, credit_card, investments, other.",
        "Credit-card payments and internal transfers are excluded from cashflow.",
    ]
    data["accounts"] = labels
    _write_account_labels_file(cfg, data)
    _persist_account_settings(cfg, labels)
    return {
        "ok": True,
        "updated_count": updated,
        "labels_path": str(_account_labels_path(cfg)),
        "message": f"Saved labels for {updated} Plaid account(s). Run sync plaid to refresh the finance views.",
    }
