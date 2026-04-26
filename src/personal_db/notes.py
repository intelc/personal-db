from __future__ import annotations

import re
from datetime import date

from personal_db.config import Config
from personal_db.db import connect

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-") or "note"


def write_note(cfg: Config, title: str, body: str) -> str:
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{date.today().isoformat()}-{_slugify(title)}.md"
    p = cfg.notes_dir / fname
    p.write_text(body)
    excerpt = body.strip().splitlines()[0][:200] if body.strip() else ""
    con = connect(cfg.db_path)
    con.execute(
        "INSERT INTO notes(path,title,created_at,body_excerpt) VALUES (?,?,datetime('now'),?) "
        "ON CONFLICT(path) DO UPDATE SET title=excluded.title, body_excerpt=excluded.body_excerpt",
        (fname, title, excerpt),
    )
    con.commit()
    con.close()
    return fname


def list_notes(cfg: Config, query: str | None = None) -> list[dict]:
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    con = connect(cfg.db_path)
    indexed = {r[0] for r in con.execute("SELECT path FROM notes")}
    for note_file in cfg.notes_dir.glob("*.md"):
        if note_file.name in indexed:
            continue
        body = note_file.read_text()
        excerpt = body.strip().splitlines()[0][:200] if body.strip() else ""
        con.execute(
            "INSERT INTO notes(path,title,created_at,body_excerpt) VALUES (?,?,datetime('now'),?)",
            (note_file.name, note_file.stem, excerpt),
        )
    con.commit()
    if query:
        rows = con.execute(
            "SELECT path,title,created_at,body_excerpt FROM notes "
            "WHERE title LIKE ? OR body_excerpt LIKE ? ORDER BY created_at DESC",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT path,title,created_at,body_excerpt FROM notes ORDER BY created_at DESC"
        ).fetchall()
    con.close()
    return [{"path": r[0], "title": r[1], "created_at": r[2], "excerpt": r[3]} for r in rows]


def read_note(cfg: Config, rel_path: str) -> str:
    return (cfg.notes_dir / rel_path).read_text()
