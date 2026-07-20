from __future__ import annotations

import html
import json
import sqlite3
from typing import Any

from personal_db.apps import AppContext
from personal_db.db import connect
from personal_db.ui import components as c


def _q(ctx: AppContext, name: str, **params: Any) -> list[dict[str, Any]]:
    try:
        return ctx.query(name, **params)
    except sqlite3.Error:
        return []


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _money(value: Any) -> str:
    return f"${_float(value):,.2f}"


def _hours(value: Any) -> str:
    return f"{_float(value) / 60.0:.1f}h"


def _compact_time(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.replace("T", " ")
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.split(".", 1)[0]


def _charge_dates(value: Any) -> str:
    dates = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return ", ".join(dates)


def _nav(ctx: AppContext, active: str) -> list[tuple[str, str, bool]]:
    return [
        (page.title, f"/a/{ctx.manifest.name}/{page.slug}", page.slug == active)
        for page in ctx.manifest.pages
    ]


def _row_ref(subscription_id: Any) -> str:
    return f"subscription_entities:{subscription_id}"


def _field_ref(subscription_id: Any, field: str) -> str:
    return f"subscription_entities:{subscription_id}:{field}"


def _merchant_ref(value: Any) -> str:
    merchant = " ".join(str(value or "").split()).casefold()
    return f"merchant:{merchant}" if merchant else ""


def _notes_by_ref(ctx: AppContext, refs: list[str]) -> dict[str, list[dict[str, Any]]]:
    unique = sorted({ref for ref in refs if ref})
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    con = connect(ctx.cfg.db_path)
    try:
        rows = con.execute(
            f"""
            SELECT r.ref, n.note_id, n.body, n.created_at, n.updated_at
            FROM ui_note_refs r
            JOIN ui_notes n ON n.note_id = r.note_id
            WHERE r.ref IN ({placeholders})
            ORDER BY n.created_at DESC
            """,
            unique,
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()

    out: dict[str, list[dict[str, Any]]] = {ref: [] for ref in unique}
    seen: set[tuple[str, str]] = set()
    for ref, note_id, body, created_at, updated_at in rows:
        key = (str(ref), str(note_id))
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(str(ref), []).append(
            {
                "note_id": str(note_id),
                "body": str(body or ""),
                "created_at": str(created_at or ""),
                "updated_at": str(updated_at or ""),
            }
        )
    return out


def _note_ref(ref: str, ref_kind: str, label: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"ref": ref, "ref_kind": ref_kind, "label": label}
    if metadata:
        item["metadata"] = metadata
    return item


def _subscription_refs(
    row: dict[str, Any],
    primary_ref: str,
    primary_kind: str,
    target_label: str,
) -> list[dict[str, Any]]:
    subscription_id = str(row.get("subscription_id") or "")
    label = str(row.get("label") or "")
    merchant = str(row.get("latest_merchant") or label)
    refs = [_note_ref(primary_ref, primary_kind, target_label)]
    if subscription_id and primary_ref != _row_ref(subscription_id):
        refs.append(_note_ref(_row_ref(subscription_id), "table_row", label or subscription_id))
    merchant_ref = _merchant_ref(merchant)
    if merchant_ref:
        refs.append(_note_ref(merchant_ref, "concept", f"Merchant: {merchant}"))
    refs.append(_note_ref("app:subscriptions", "app", "Subscriptions app"))
    return refs


def _note_target(
    ctx: AppContext,
    *,
    primary_ref: str,
    label: str,
    inner_html: str,
    refs: list[dict[str, Any]],
    notes_by_ref: dict[str, list[dict[str, Any]]],
) -> str:
    notes = notes_by_ref.get(primary_ref, [])
    attrs = {
        "data-note-target": "1",
        "data-note-primary-ref": primary_ref,
        "data-note-label": label,
        "data-note-create-url": ctx.action_url("create_note"),
        "data-note-count": str(len(notes)),
        "data-note-refs": json.dumps(refs, ensure_ascii=False),
        "data-note-notes": json.dumps(notes, ensure_ascii=False),
    }
    attr_html = "".join(
        f' {html.escape(key, quote=True)}="{html.escape(value, quote=True)}"'
        for key, value in attrs.items()
    )
    return f"<span{attr_html}>{inner_html}</span>"


def _notes_assets() -> str:
    return """
    <style>
      .pdb-note-toggle {
        position: fixed;
        right: calc(var(--grid) * 20);
        bottom: calc(var(--grid) * 2);
        z-index: 80;
        border: 2px solid #000;
        background: #fff;
        color: #000;
        font: inherit;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: lowercase;
        padding: 6px 10px;
        cursor: cell;
      }
      .pdb-agent-open .pdb-note-toggle {
        right: calc(min(720px, 46vw) + var(--grid) * 11);
      }
      .pdb-note-toggle:hover,
      .pdb-note-toggle.active,
      .pdb-note-toggle[aria-pressed="true"] {
        background: #000;
        color: #fff;
      }
      .pdb-note-popover button {
        border: 1px solid #000;
        font: inherit;
        font-size: 11px;
        padding: 3px 8px;
        cursor: pointer;
      }
      .pdb-note-popover button:hover {
        background: #000;
        color: #fff;
      }
      .pdb-note-list time,
      .pdb-note-form span,
      .pdb-note-message {
        color: #666;
        font-size: 11px;
      }
      .pdb-note-mode [data-note-target] {
        cursor: cell;
        outline: 1px dashed #999;
        outline-offset: 2px;
      }
      .pdb-note-mode [data-note-target][data-note-count]:not([data-note-count="0"]) {
        background: #fff3bf;
        outline: 2px solid #000;
      }
      .pdb-note-mode [data-note-target]:hover,
      .pdb-note-mode [data-note-target].pdb-note-active {
        background: #000;
        color: #fff;
        outline: 2px solid #000;
      }
      .pdb-note-mode [data-note-target][data-note-count]:not([data-note-count="0"])::after {
        content: attr(data-note-count);
        display: inline-block;
        margin-left: 4px;
        padding: 0 4px;
        border: 1px solid currentColor;
        font-size: 10px;
        line-height: 1.2;
      }
      .pdb-note-popover {
        position: fixed;
        z-index: 120;
        width: min(360px, calc(100vw - 16px));
        border: 2px solid #000;
        background: #fff;
        color: #000;
      }
      .pdb-note-popover[hidden] { display: none; }
      .pdb-note-popover-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px;
        border-bottom: 1px solid #000;
      }
      .pdb-note-popover-head strong {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .pdb-note-popover-head button {
        width: 24px;
        height: 24px;
        padding: 0;
      }
      .pdb-note-popover-body { padding: 8px; }
      .pdb-note-list {
        list-style: none;
        margin: 0 0 8px;
        padding: 0;
        max-height: 180px;
        overflow: auto;
      }
      .pdb-note-list li {
        border-bottom: 1px solid #ddd;
        padding: 0 0 8px;
        margin: 0 0 8px;
      }
      .pdb-note-list p,
      .pdb-note-empty,
      .pdb-note-message { margin: 0; }
      .pdb-note-form {
        display: grid;
        gap: 8px;
      }
      .pdb-note-form textarea {
        width: 100%;
        resize: vertical;
        border: 1px solid #000;
        padding: 6px;
        font: inherit;
      }
      .pdb-note-form div {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      @media (max-width: 900px) {
        .pdb-note-toggle,
        .pdb-agent-open .pdb-note-toggle {
          right: calc(var(--grid) * 20);
          z-index: 96;
        }
      }
    </style>
    <script>
    (() => {
      if (window.__pdbNotesReady) return;
      window.__pdbNotesReady = true;
      let noteMode = false;
      let popover = null;
      let currentTarget = null;

      function escapeHtml(value) {
        return String(value || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      }

      function parseJson(el, attr, fallback) {
        try {
          return JSON.parse(el.getAttribute(attr) || "");
        } catch (_error) {
          return fallback;
        }
      }

      function closePopover() {
        currentTarget = null;
        if (popover) popover.hidden = true;
        document.querySelectorAll(".pdb-note-active").forEach((el) => el.classList.remove("pdb-note-active"));
      }

      function ensureNoteToggle() {
        let button = document.getElementById("pdb-note-toggle");
        if (button) return button;
        button = document.createElement("button");
        button.id = "pdb-note-toggle";
        button.className = "pdb-note-toggle";
        button.type = "button";
        button.dataset.noteModeToggle = "1";
        button.setAttribute("aria-pressed", "false");
        button.title = "Add or view notes on this page";
        button.textContent = "note";
        document.body.appendChild(button);
        return button;
      }

      function setMode(next) {
        noteMode = next;
        document.body.classList.toggle("pdb-note-mode", noteMode);
        document.querySelectorAll("[data-note-mode-toggle]").forEach((button) => {
          button.setAttribute("aria-pressed", noteMode ? "true" : "false");
          button.textContent = noteMode ? "Exit notes" : "Add notes";
        });
        const floating = ensureNoteToggle();
        floating.classList.toggle("active", noteMode);
        floating.setAttribute("aria-pressed", noteMode ? "true" : "false");
        floating.textContent = noteMode ? "notes on" : "note";
        document.querySelectorAll("[data-note-mode-status]").forEach((status) => {
          status.textContent = noteMode
            ? "Click a highlighted element to view notes, or any target to add one."
            : "Notes hidden until note mode is on.";
        });
        document.querySelectorAll("[data-note-target]").forEach((target) => {
          if (noteMode) {
            target.setAttribute("tabindex", "0");
            target.setAttribute("role", "button");
          } else {
            target.removeAttribute("tabindex");
            target.removeAttribute("role");
          }
        });
        if (!noteMode) closePopover();
      }

      window.pdbNotes = {
        isActive: () => noteMode,
        setMode,
        toggle: () => setMode(!noteMode),
      };
      ensureNoteToggle();

      function ensurePopover() {
        if (popover) return popover;
        popover = document.createElement("div");
        popover.className = "pdb-note-popover";
        popover.hidden = true;
        document.body.appendChild(popover);
        return popover;
      }

      function positionPopover(target, el) {
        const rect = target.getBoundingClientRect();
        const margin = 8;
        const width = Math.min(360, window.innerWidth - margin * 2);
        el.style.width = width + "px";
        el.style.left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin) + "px";
        let top = rect.bottom + margin;
        if (top + 260 > window.innerHeight) top = Math.max(margin, rect.top - 260 - margin);
        el.style.top = top + "px";
      }

      function notesHtml(notes) {
        if (!notes.length) return '<p class="pdb-note-empty">No notes yet.</p>';
        return '<ul class="pdb-note-list">' + notes.map((note) => (
          '<li><p>' + escapeHtml(note.body) + '</p>' +
          (note.created_at ? '<time>' + escapeHtml(note.created_at) + '</time>' : '') +
          '</li>'
        )).join("") + "</ul>";
      }

      function renderPopover(target, message) {
        const el = ensurePopover();
        const label = target.dataset.noteLabel || "Selected item";
        const notes = parseJson(target, "data-note-notes", []);
        el.innerHTML =
          '<div class="pdb-note-popover-head"><strong>' + escapeHtml(label) +
          '</strong><button type="button" data-note-close aria-label="Close notes">x</button></div>' +
          '<div class="pdb-note-popover-body">' + notesHtml(notes) +
          '<form class="pdb-note-form"><textarea name="body" rows="4" placeholder="Add a note" required></textarea>' +
          '<div><span>' + notes.length + ' saved note' + (notes.length === 1 ? '' : 's') +
          '</span><button type="submit">Save note</button></div></form>' +
          '<p class="pdb-note-message">' + escapeHtml(message || "") + '</p></div>';
        el.hidden = false;
        positionPopover(target, el);
        const textarea = el.querySelector("textarea");
        if (textarea) textarea.focus();
      }

      function selectorValue(value) {
        if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
        return String(value).replace(/["\\\\]/g, "\\\\$&");
      }

      function updateTargets(primaryRef, notes) {
        document.querySelectorAll('[data-note-primary-ref="' + selectorValue(primaryRef) + '"]').forEach((target) => {
          target.setAttribute("data-note-count", String(notes.length));
          target.setAttribute("data-note-notes", JSON.stringify(notes));
        });
      }

      async function saveNote(form) {
        if (!currentTarget || !window.pdbApp || !window.pdbApp.requestJson) return;
        const body = String(new FormData(form).get("body") || "").trim();
        if (!body) return;
        const button = form.querySelector('button[type="submit"]');
        if (button) {
          button.disabled = true;
          button.textContent = "Saving";
        }
        const primaryRef = currentTarget.dataset.notePrimaryRef || "";
        try {
          const result = await window.pdbApp.requestJson(currentTarget.dataset.noteCreateUrl || "", {
            data: {
              body,
              label: currentTarget.dataset.noteLabel || "",
              primary_ref: primaryRef,
              refs_json: currentTarget.getAttribute("data-note-refs") || "[]",
            },
          });
          updateTargets(result.primary_ref || primaryRef, result.notes || []);
          renderPopover(currentTarget, "Saved.");
        } catch (error) {
          renderPopover(currentTarget, error && error.message ? error.message : "Save failed.");
        }
      }

      document.addEventListener("click", (event) => {
        const close = event.target.closest && event.target.closest("[data-note-close]");
        if (close) {
          event.preventDefault();
          closePopover();
          return;
        }
        const toggle = event.target.closest && event.target.closest("[data-note-mode-toggle]");
        if (toggle) {
          event.preventDefault();
          setMode(!noteMode);
          return;
        }
        const floatingToggle = event.target.closest && event.target.closest("#pdb-note-toggle");
        if (floatingToggle) {
          event.preventDefault();
          setMode(!noteMode);
          return;
        }
        if (event.target.closest && event.target.closest(".pdb-note-popover")) return;
        const target = event.target.closest && event.target.closest("[data-note-target]");
        if (target && noteMode) {
          event.preventDefault();
          event.stopPropagation();
          closePopover();
          currentTarget = target;
          target.classList.add("pdb-note-active");
          renderPopover(target);
          return;
        }
        if (popover && !popover.hidden) closePopover();
      });

      document.addEventListener("submit", (event) => {
        const form = event.target.closest && event.target.closest(".pdb-note-form");
        if (!form) return;
        event.preventDefault();
        saveNote(form);
      });

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          closePopover();
          return;
        }
        if (!noteMode || (event.key !== "Enter" && event.key !== " ")) return;
        const target = event.target.closest && event.target.closest("[data-note-target]");
        if (!target) return;
        event.preventDefault();
        closePopover();
        currentTarget = target;
        target.classList.add("pdb-note-active");
        renderPopover(target);
      });
    })();
    </script>
    """


def _not_subscription_action(ctx: AppContext, row: dict[str, Any]) -> str:
    action = html.escape(ctx.action_url("mark_not_subscription"), quote=True)
    subscription_id = html.escape(str(row.get("subscription_id") or ""), quote=True)
    merchant = html.escape(str(row.get("latest_merchant") or row.get("label") or ""), quote=True)
    label = html.escape(str(row.get("label") or merchant), quote=True)
    return (
        f'<form class="subscription-action" method="post" action="{action}">'
        f'<input type="hidden" name="subscription_id" value="{subscription_id}">'
        f'<input type="hidden" name="merchant" value="{merchant}">'
        f'<input type="hidden" name="label" value="{label}">'
        '<input type="hidden" name="category" value="Entertainment">'
        '<input type="hidden" name="bucket" value="entertainment">'
        '<button type="submit">Not subscription</button>'
        "</form>"
    )


def _label_badge(value: Any) -> str:
    label = str(value or "unknown")
    klass = "sub-" + html.escape(label.replace("_", "-"), quote=True)
    return f'<span class="sub-label {klass}">{html.escape(label.replace("_", " "))}</span>'


def _style() -> str:
    return """
    <style>
      .sub-label {
        display: inline-block;
        padding: 2px 8px;
        border: 1px solid #111827;
        background: #f8fafc;
        font-size: 12px;
        line-height: 1.5;
      }
      .sub-high { background: #dcfce7; }
      .sub-medium { background: #dbeafe; }
      .sub-low { background: #fef9c3; }
      .sub-no-observed-usage { background: #fee2e2; }
      .sub-unknown { background: #f3f4f6; }
    </style>
    """


def _evidence_summary(value: Any) -> str:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    labels = []
    for item in parsed[:3] if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        bits = [str(item.get("app") or item.get("domain") or item.get("source") or "")]
        minutes = _float(item.get("minutes"))
        if minutes:
            bits.append(_hours(minutes))
        labels.append(" ".join(bit for bit in bits if bit))
    return ", ".join(labels)


def metrics(cfg) -> list[dict]:
    """Dashboard tile metrics: active subscription count and their combined
    monthly-like cost (same `overview_counts` shape `render_overview` uses --
    latest charge/monthly-average blended per subscription, summed)."""
    try:
        con = connect(cfg.db_path, read_only=True)
    except sqlite3.OperationalError:
        return []
    try:
        row = con.execute(
            "SELECT COUNT(*) AS subscriptions, "
            "SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_subscriptions, "
            "SUM(COALESCE(monthly_avg_amount, latest_amount, 0)) AS latest_monthly_like_cost "
            "FROM subscription_entities"
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    if not row or not row[0]:
        return []
    _subscriptions, active, monthly_cost = row
    return [
        {
            "label": "Active subscriptions",
            "value": str(_int(active)),
            "detail": None,
            "delta": None,
            "good": None,
        },
        {
            "label": "Monthly total",
            "value": _money(monthly_cost),
            "detail": None,
            "delta": None,
            "good": None,
            "sensitive": True,
        },
    ]


def render_overview(ctx: AppContext) -> str:
    counts = _q(ctx, "overview_counts")
    row = counts[0] if counts else {}
    periods = _q(ctx, "latest_periods", limit=20)
    note_refs: list[str] = []
    for period in periods:
        subscription_id = period.get("subscription_id")
        if not subscription_id:
            continue
        note_refs.extend(
            [
                _row_ref(subscription_id),
                _field_ref(subscription_id, "usage_minutes"),
                _field_ref(subscription_id, "utilization_label"),
                _field_ref(subscription_id, "evidence_json"),
            ]
        )
    notes_by_ref = _notes_by_ref(ctx, note_refs)
    period_rows = [
        (
            _note_target(
                ctx,
                primary_ref=_row_ref(r.get("subscription_id")),
                label=str(r.get("label") or "Subscription"),
                inner_html=html.escape(str(r.get("label") or "")),
                refs=_subscription_refs(
                    r,
                    _row_ref(r.get("subscription_id")),
                    "table_row",
                    str(r.get("label") or "Subscription"),
                ),
                notes_by_ref=notes_by_ref,
            ),
            _money(r.get("cost")),
            _note_target(
                ctx,
                primary_ref=_field_ref(r.get("subscription_id"), "usage_minutes"),
                label=f"{r.get('label') or 'Subscription'} usage",
                inner_html=html.escape(_hours(r.get("usage_minutes"))),
                refs=_subscription_refs(
                    r,
                    _field_ref(r.get("subscription_id"), "usage_minutes"),
                    "field",
                    f"{r.get('label') or 'Subscription'} usage",
                ),
                notes_by_ref=notes_by_ref,
            ),
            _int(r.get("active_days")),
            _note_target(
                ctx,
                primary_ref=_field_ref(r.get("subscription_id"), "utilization_label"),
                label=f"{r.get('label') or 'Subscription'} utilization label",
                inner_html=_label_badge(r.get("utilization_label")),
                refs=_subscription_refs(
                    r,
                    _field_ref(r.get("subscription_id"), "utilization_label"),
                    "field",
                    f"{r.get('label') or 'Subscription'} utilization label",
                ),
                notes_by_ref=notes_by_ref,
            ),
            _note_target(
                ctx,
                primary_ref=_field_ref(r.get("subscription_id"), "evidence_json"),
                label=f"{r.get('label') or 'Subscription'} evidence",
                inner_html=html.escape(_evidence_summary(r.get("evidence_json"))),
                refs=_subscription_refs(
                    r,
                    _field_ref(r.get("subscription_id"), "evidence_json"),
                    "field",
                    f"{r.get('label') or 'Subscription'} evidence",
                ),
                notes_by_ref=notes_by_ref,
            ),
        )
        for r in periods
    ]
    utilization = _q(ctx, "utilization_counts", days=90)
    chart = c.chart(
        {
            "data": [
                {
                    "label": str(r.get("utilization_label") or "unknown").replace("_", " "),
                    "cost": round(_float(r.get("cost")), 2),
                    "hours": round(_float(r.get("usage_minutes")) / 60.0, 2),
                }
                for r in utilization
            ],
            "series": [
                {"type": "bar", "xKey": "label", "yKey": "cost", "yName": "Cost", "fill": "#475569"},
                {"type": "bar", "xKey": "label", "yKey": "hours", "yName": "Usage hours", "fill": "#2563eb"},
            ],
            "axes": {"bottom": {"type": "category"}, "left": {"type": "number"}},
            "legend": {"enabled": True, "position": "bottom"},
        },
        height_px=280,
    )
    return c.page(
        "Subscriptions Overview",
        _style(),
        _notes_assets(),
        c.metric_grid(
            [
                ("Subscriptions", f"{_int(row.get('subscriptions')):,}", ""),
                ("Active", f"{_int(row.get('active_subscriptions')):,}", ""),
                ("Latest Cost", _money(row.get("latest_monthly_like_cost")), "sum of latest charges"),
                ("Confidence", f"{_float(row.get('avg_confidence')):.2f}", ""),
            ]
        ),
        c.section("Utilization Mix", chart, subtitle="Recent billing periods grouped by observed utilization label."),
        c.section(
            "Latest Periods",
            c.data_grid(
                period_rows,
                ["Subscription", "Cost", "Usage", "Active Days", "Label", "Evidence"],
                page_size=20,
                height_px=560,
                html_columns={0, 2, 4, 5},
            ),
            subtitle="One latest billing period per subscription.",
        ),
        nav=_nav(ctx, "overview"),
    )


def render_subscriptions(ctx: AppContext) -> str:
    rows = _q(ctx, "subscription_rows", days=180, limit=200)
    note_refs: list[str] = []
    for row in rows:
        subscription_id = row.get("subscription_id")
        if not subscription_id:
            continue
        note_refs.extend([_row_ref(subscription_id), _field_ref(subscription_id, "usage_minutes")])
    notes_by_ref = _notes_by_ref(ctx, note_refs)
    columns = [
        "label",
        "status",
        "cadence",
        "charges",
        "typical_amount",
        "monthly_avg",
        "latest_amount",
        "charge_dates",
        "last_charge",
        "next_expected",
        "usage",
        "active_days",
        "events",
        "confidence",
        "action",
    ]
    table = [
        {
            "label": _note_target(
                ctx,
                primary_ref=_row_ref(row.get("subscription_id")),
                label=str(row.get("label") or "Subscription"),
                inner_html=html.escape(str(row.get("label") or "")),
                refs=_subscription_refs(
                    row,
                    _row_ref(row.get("subscription_id")),
                    "table_row",
                    str(row.get("label") or "Subscription"),
                ),
                notes_by_ref=notes_by_ref,
            ),
            "status": row.get("status") or "",
            "cadence": row.get("cadence") or "",
            "charges": row.get("charge_count") or 0,
            "typical_amount": _money(row.get("typical_amount") or row.get("latest_amount")),
            "monthly_avg": _money(row.get("monthly_avg_amount") or row.get("avg_amount")),
            "latest_amount": _money(row.get("latest_amount")),
            "charge_dates": _charge_dates(row.get("recent_charge_dates")),
            "last_charge": row.get("last_charge_date") or "",
            "next_expected": row.get("next_expected_date") or "",
            "usage": _note_target(
                ctx,
                primary_ref=_field_ref(row.get("subscription_id"), "usage_minutes"),
                label=f"{row.get('label') or 'Subscription'} usage",
                inner_html=html.escape(_hours(row.get("usage_minutes"))),
                refs=_subscription_refs(
                    row,
                    _field_ref(row.get("subscription_id"), "usage_minutes"),
                    "field",
                    f"{row.get('label') or 'Subscription'} usage",
                ),
                notes_by_ref=notes_by_ref,
            ),
            "active_days": row.get("active_days") or 0,
            "events": row.get("event_count") or 0,
            "confidence": f"{_float(row.get('confidence')):.2f}",
            "action": _not_subscription_action(ctx, row),
        }
        for row in rows
    ]
    if not table:
        body = c.section(
            "Detected Subscriptions",
            c.empty_state(
                "No subscriptions detected yet",
                hint="Subscriptions are inferred from synced finance transactions — sync finance first.",
                action=("Go to Finance", "/a/finance"),
            ),
        )
    else:
        body = c.section(
            "Detected Subscriptions",
            c.data_grid(
                table,
                columns,
                page_size=25,
                height_px=680,
                html_columns={0, 10, 14},
            ),
            subtitle="Charges already categorized as Subscriptions in the finance layer, grouped by merchant/rule.",
        )
    return c.page(
        "Subscriptions",
        _style(),
        _notes_assets(),
        body,
        nav=_nav(ctx, "subscriptions"),
    )


def render_evidence(ctx: AppContext) -> str:
    charges = _q(ctx, "recent_charges", limit=100)
    evidence = _q(ctx, "recent_evidence", limit=200)
    charge_rows = [
        (
            row.get("date") or "",
            str(row.get("label") or ""),
            str(row.get("merchant") or ""),
            _money(row.get("amount")),
            str(row.get("category_source") or ""),
        )
        for row in charges
    ]
    evidence_rows = [
        (
            _compact_time(row.get("started_at")),
            str(row.get("label") or ""),
            str(row.get("source") or ""),
            _hours(row.get("minutes")),
            _int(row.get("event_count")),
            str(row.get("app_name") or ""),
            str(row.get("domain") or ""),
            str(row.get("title") or "")[:120],
            f"{_float(row.get('confidence')):.2f}",
            str(row.get("reason") or ""),
        )
        for row in evidence
    ]
    return c.page(
        "Subscription Evidence",
        c.section(
            "Recent Charges",
            c.data_grid(charge_rows, ["Date", "Subscription", "Merchant", "Amount", "Category Source"], page_size=25),
        ),
        c.section(
            "Recent Usage Evidence",
            c.data_grid(
                evidence_rows,
                ["Time", "Subscription", "Source", "Usage", "Events", "App", "Domain", "Title", "Conf", "Reason"],
                page_size=25,
                height_px=680,
            ),
        ),
        nav=_nav(ctx, "evidence"),
    )


def render_rules(ctx: AppContext) -> str:
    rows = _q(ctx, "match_rules")
    table = [
        {
            "merchant_pattern": row.get("merchant_pattern") or "",
            "label": row.get("label") or "",
            "domain_pattern": row.get("domain_pattern") or "",
            "app_pattern": row.get("app_pattern") or "",
            "bundle_id": row.get("bundle_id") or "",
            "enabled": row.get("enabled") or 0,
            "source": row.get("source") or "",
        }
        for row in rows
    ]
    return c.page(
        "Subscription Rules",
        c.section(
            "Match Rules",
            c.data_grid(table, list(table[0].keys()) if table else ["merchant_pattern"], page_size=30, height_px=680),
            subtitle="Rules connect subscription charges to app, bundle, and browser-domain usage evidence.",
        ),
        nav=_nav(ctx, "rules"),
    )
