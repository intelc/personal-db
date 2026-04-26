from __future__ import annotations

import json
from datetime import UTC, datetime

import yaml

from personal_db.config import Config
from personal_db.db import connect


class EntityStore:
    """Helper namespace; keep state inside the SQLite db."""


def sync_entities_from_yaml(cfg: Config) -> None:
    """Reconcile people/topics tables to the YAML files (YAML is source of truth).
    Writes-through: any DB-only entries (e.g. auto-created) are kept;
    any YAML entry is upserted. Aliases are fully replaced from YAML for each person."""
    con = connect(cfg.db_path)
    for _kind, table_e, table_a, id_col, yaml_name in (
        ("people", "people", "people_aliases", "person_id", "people.yaml"),
        ("topics", "topics", "topics_aliases", "topic_id", "topics.yaml"),
    ):
        path = cfg.entities_dir / yaml_name
        if not path.exists():
            continue
        entries = yaml.safe_load(path.read_text()) or []
        for entry in entries:
            display = entry["display_name"]
            row = con.execute(
                f"SELECT {id_col} FROM {table_e} WHERE display_name=?", (display,)
            ).fetchone()
            if row:
                eid = row[0]
            else:
                cur = con.execute(f"INSERT INTO {table_e}(display_name) VALUES (?)", (display,))
                eid = cur.lastrowid
            for alias in entry.get("aliases", []):
                con.execute(
                    f"INSERT OR IGNORE INTO {table_a}(alias,{id_col}) VALUES (?,?)",
                    (alias, eid),
                )
    con.commit()
    con.close()


def _resolve(cfg: Config, alias: str, kind: str, *, auto_create: bool) -> int | None:
    table_a = f"{kind}_aliases"
    table_e = kind
    id_col = "person_id" if kind == "people" else "topic_id"
    con = connect(cfg.db_path)
    row = con.execute(f"SELECT {id_col} FROM {table_a} WHERE alias=?", (alias,)).fetchone()
    if row:
        con.close()
        return row[0]
    if not auto_create:
        con.close()
        return None
    cur = con.execute(f"INSERT INTO {table_e}(display_name) VALUES (?)", (alias,))
    eid = cur.lastrowid
    con.execute(f"INSERT INTO {table_a}(alias,{id_col}) VALUES (?,?)", (alias, eid))
    con.commit()
    con.close()
    review_path = cfg.state_dir / "entities_needs_review.jsonl"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "kind": kind,
                    "alias": alias,
                    "id": eid,
                    "ts": datetime.now(UTC).isoformat(),
                }
            )
            + "\n"
        )
    return eid


def resolve_person(cfg: Config, alias: str, *, auto_create: bool = True) -> int | None:
    return _resolve(cfg, alias, "people", auto_create=auto_create)


def resolve_topic(cfg: Config, alias: str, *, auto_create: bool = True) -> int | None:
    return _resolve(cfg, alias, "topics", auto_create=auto_create)
