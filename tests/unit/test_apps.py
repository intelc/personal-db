import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from personal_db.apps import (
    AppManifestError,
    AppQueryError,
    discover_apps,
    install_app_template,
    list_bundled_apps,
    load_app_manifest,
    load_named_queries,
    update_app_template,
)
from personal_db.config import Config
from personal_db.daemon.http import build_app
from personal_db.db import apply_tracker_schema, connect, init_db

FINANCE_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "personal_db"
    / "templates"
    / "trackers"
    / "finance"
    / "schema.sql"
)


def test_bundled_finance_app_discovered(tmp_root):
    cfg = Config(root=tmp_root)
    apps = discover_apps(cfg)
    assert "finance" in apps
    assert apps["finance"].source == "bundled"
    assert apps["finance"].manifest.default_page.slug == "overview"


def test_installed_app_overrides_bundled_app(tmp_root):
    cfg = Config(root=tmp_root)
    app_dir = cfg.apps_dir / "finance"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "finance",
                "title": "Custom Finance",
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
            }
        )
    )
    apps = discover_apps(cfg)
    assert apps["finance"].source == "installed"
    assert apps["finance"].manifest.title == "Custom Finance"
    assert apps["finance"].manifest.default_page.slug == "home"


def test_app_template_install_and_reinstall_preserve_extra_files(tmp_root):
    cfg = Config(root=tmp_root)
    assert "finance" in list_bundled_apps()

    dest = install_app_template(cfg, "finance")
    assert (dest / "app.yaml").exists()
    assert (dest / "schema.sql").exists()
    assert (dest / "queries.sql").exists()
    extra = dest / "local_note.md"
    extra.write_text("keep me")
    (dest / "views.py").write_text("# stale\n")

    updated = update_app_template(cfg, "finance")
    assert updated == dest
    assert extra.read_text() == "keep me"
    assert "# stale" not in (dest / "views.py").read_text()


def test_app_manifest_validation_rejects_bad_names(tmp_path):
    manifest = tmp_path / "app.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "name": "../bad",
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
            }
        )
    )
    with pytest.raises(AppManifestError):
        load_app_manifest(manifest)


def test_named_queries_parse_and_reject_writes(tmp_path):
    queries = tmp_path / "queries.sql"
    queries.write_text(
        "-- name: good\nSELECT * FROM sample WHERE id = :id\n\n-- name: bad\nDELETE FROM sample\n"
    )
    with pytest.raises(AppQueryError):
        load_named_queries(queries)

    queries.write_text("-- name: good\nSELECT * FROM sample WHERE id = :id\n")
    assert load_named_queries(queries)["good"].startswith("SELECT")


def test_app_route_renders_custom_app_and_named_query(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    con = sqlite3.connect(cfg.db_path)
    try:
        con.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, label TEXT)")
        con.execute("INSERT INTO sample (id, label) VALUES ('one', 'Hello App')")
        con.commit()
    finally:
        con.close()

    app_dir = cfg.apps_dir / "sample"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "sample",
                "title": "Sample App",
                "description": "test app",
                "reads": {"tables": ["sample"]},
                "pages": [
                    {"slug": "home", "title": "Home", "view": "render_home"},
                    {"slug": "details", "title": "Details", "view": "render_details"},
                ],
            }
        )
    )
    (app_dir / "queries.sql").write_text(
        "-- name: sample_rows\nSELECT id, label FROM sample ORDER BY id\n"
    )
    (app_dir / "views.py").write_text(
        "from personal_db.ui import components as c\n"
        "def render_home(ctx):\n"
        "    rows = ctx.query('sample_rows')\n"
        "    return c.page('Sample Home', c.data_grid(rows, [\n"
        "        {'field': 'id', 'headerName': 'ID'},\n"
        "        {'field': 'label', 'headerName': 'Label'},\n"
        "    ]))\n"
        "def render_details(ctx):\n"
        "    return c.page('Details', '<p>details page</p>')\n"
    )

    client = TestClient(build_app(cfg))
    index = client.get("/a")
    assert index.status_code == 200
    assert "Sample App" in index.text

    home = client.get("/a/sample")
    assert home.status_code == 200
    assert "Sample Home" in home.text
    assert "Hello App" in home.text

    details = client.get("/a/sample/details")
    assert details.status_code == 200
    assert "details page" in details.text


def test_app_route_applies_schema_before_render(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    app_dir = cfg.apps_dir / "schema_app"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "schema_app",
                "title": "Schema App",
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
            }
        )
    )
    (app_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS app_schema_items (id TEXT PRIMARY KEY, label TEXT);\n"
        "INSERT OR IGNORE INTO app_schema_items(id, label) VALUES ('one', 'Schema Seed');\n"
    )
    (app_dir / "queries.sql").write_text(
        "-- name: schema_rows\nSELECT id, label FROM app_schema_items ORDER BY id\n"
    )
    (app_dir / "views.py").write_text(
        "from personal_db.ui import components as c\n"
        "def render_home(ctx):\n"
        "    rows = ctx.query('schema_rows')\n"
        "    return c.page('Schema Home', c.data_grid(rows, [\n"
        "        {'field': 'id', 'headerName': 'ID'},\n"
        "        {'field': 'label', 'headerName': 'Label'},\n"
        "    ]))\n"
    )

    r = TestClient(build_app(cfg)).get("/a/schema_app")
    assert r.status_code == 200
    assert "Schema Seed" in r.text


def test_app_route_returns_500_when_render_fails(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    app_dir = cfg.apps_dir / "broken"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "broken",
                "title": "Broken App",
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
            }
        )
    )
    (app_dir / "views.py").write_text("def render_home(ctx):\n    raise RuntimeError('boom')\n")

    r = TestClient(build_app(cfg)).get("/a/broken")
    assert r.status_code == 500
    assert "error rendering app page" in r.text


def test_bundled_finance_route_renders_without_finance_tables(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg))
    r = client.get("/a/finance")
    assert r.status_code == 200
    assert "Finance Overview" in r.text
    assert "No combined finance data yet" in r.text


def test_finance_review_actions_write_app_state(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg))

    marked = client.post(
        "/api/apps/finance/actions/mark_reviewed",
        json={
            "review_key": "txn-parent-draw-1",
            "kind": "parent_draw",
            "status": "ignored",
            "note": "handled elsewhere",
        },
    )
    assert marked.status_code == 200
    assert marked.json()["ok"] is True

    con = connect(cfg.db_path)
    try:
        row = con.execute(
            """
            SELECT kind, status, note
            FROM app_finance_reviews
            WHERE review_key='txn-parent-draw-1'
            """
        ).fetchone()
    finally:
        con.close()
    assert row == ("parent_draw", "ignored", "handled elsewhere")

    cleared = client.post(
        "/api/apps/finance/actions/clear_review",
        json={"review_key": "txn-parent-draw-1"},
    )
    assert cleared.status_code == 200
    con = connect(cfg.db_path)
    try:
        count = con.execute("SELECT COUNT(*) FROM app_finance_reviews").fetchone()[0]
    finally:
        con.close()
    assert count == 0


def test_undeclared_app_action_returns_404(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg))
    r = client.post("/api/apps/finance/actions/not_declared", json={})
    assert r.status_code == 404


def test_app_action_rejects_cross_origin_browser_writes(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg))

    rejected = client.post(
        "/api/apps/finance/actions/set_transaction_category",
        json={"finance_transaction_id": "txn-food-1", "category": "Dining"},
        headers={"origin": "https://evil.example"},
    )
    assert rejected.status_code == 403

    accepted = client.post(
        "/api/apps/finance/actions/set_transaction_category",
        json={"finance_transaction_id": "txn-food-1", "category": "Dining"},
        headers={"origin": "http://testserver"},
    )
    assert accepted.status_code == 200


def test_finance_category_actions_write_app_state(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg))

    set_category = client.post(
        "/api/apps/finance/actions/set_transaction_category",
        json={
            "finance_transaction_id": "txn-food-1",
            "category": "Dining",
            "note": "manual override",
        },
    )
    assert set_category.status_code == 200
    assert set_category.json()["category"] == "Dining"

    con = connect(cfg.db_path)
    try:
        row = con.execute(
            """
            SELECT category, note
            FROM app_finance_transaction_categories
            WHERE finance_transaction_id='txn-food-1'
            """
        ).fetchone()
        preset = con.execute(
            """
            SELECT category
            FROM app_finance_category_presets
            WHERE category='Dining'
            """
        ).fetchone()
    finally:
        con.close()
    assert row == ("Dining", "manual override")
    assert preset == ("Dining",)

    cleared = client.post(
        "/api/apps/finance/actions/clear_transaction_category",
        json={"finance_transaction_id": "txn-food-1"},
    )
    assert cleared.status_code == 200
    con = connect(cfg.db_path)
    try:
        count = con.execute("SELECT COUNT(*) FROM app_finance_transaction_categories").fetchone()[0]
    finally:
        con.close()
    assert count == 0


def _seed_finance_app_db(cfg: Config) -> None:
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, FINANCE_SCHEMA.read_text())
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executemany(
            """
            INSERT INTO finance_accounts(
              finance_account_id, source, source_account_id, owner, account_group,
              institution_name, account_name, subtype, current_balance,
              available_balance, iso_currency_code, include_in_net_worth,
              parent_draw_source, as_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "self-cash",
                    "demo",
                    "self-cash",
                    "self",
                    "cash",
                    "Demo Bank",
                    "Self Checking",
                    "checking",
                    1200,
                    1100,
                    "USD",
                    1,
                    0,
                    "2026-05-31T00:00:00+00:00",
                ),
                (
                    "self-card",
                    "demo",
                    "self-card",
                    "self",
                    "credit_card",
                    "Demo Cards",
                    "Self Visa",
                    "credit card",
                    200,
                    None,
                    "USD",
                    1,
                    0,
                    "2026-05-31T00:00:00+00:00",
                ),
                (
                    "self-invest",
                    "demo",
                    "self-invest",
                    "self",
                    "investments",
                    "Demo Broker",
                    "Self Brokerage",
                    "brokerage",
                    4000,
                    25,
                    "USD",
                    1,
                    0,
                    "2026-05-31T00:00:00+00:00",
                ),
                (
                    "parent-cash",
                    "demo",
                    "parent-cash",
                    "parents",
                    "cash",
                    "Parent Bank",
                    "Parent Checking",
                    "checking",
                    5000,
                    5000,
                    "USD",
                    0,
                    1,
                    "2026-05-31T00:00:00+00:00",
                ),
                (
                    "parent-invest",
                    "demo",
                    "parent-invest",
                    "parents",
                    "investments",
                    "Parent Broker",
                    "Parent Brokerage",
                    "brokerage",
                    7000,
                    100,
                    "USD",
                    0,
                    0,
                    "2026-05-31T00:00:00+00:00",
                ),
            ],
        )
        con.executemany(
            """
            INSERT INTO finance_daily_net_worth(
              date, owner, cash, investments, credit_card_debt, other, assets, debts, net_worth
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-05-30", "self", 1000, 3900, 200, 0, 4900, 200, 4700),
                ("2026-05-31", "self", 1200, 4000, 200, 0, 5200, 200, 5000),
                ("2026-05-31", "parents", 5000, 7000, 0, 0, 12000, 0, 12000),
            ],
        )
        con.executemany(
            """
            INSERT INTO finance_daily_cashflow(
              date, owner, income, spending, net, parent_draw,
              credit_card_payments, internal_transfers, txn_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-05-30", "self", 100, 25, 75, 0, 0, 0, 2),
                ("2026-05-31", "self", 0, 50, -50, 0, 0, 0, 1),
                ("2026-05-30", "parents", 0, 40, -40, 40, 0, 0, 1),
                ("2026-05-31", "parents", 0, 60, -60, 60, 0, 0, 1),
            ],
        )
        con.executemany(
            """
            INSERT INTO finance_holdings(
              finance_holding_id, source, source_holding_id, finance_account_id,
              source_account_id, owner, account_group, institution_name,
              account_name, security_id, security_name, ticker, type, quantity,
              cost_basis, price, value, as_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "self-vti",
                    "demo",
                    "self-vti",
                    "self-invest",
                    "self-invest",
                    "self",
                    "investments",
                    "Demo Broker",
                    "Self Brokerage",
                    "sec-vti",
                    "Total Market ETF",
                    "VTI",
                    "etf",
                    10,
                    3000,
                    400,
                    4000,
                    "2026-05-31T00:00:00+00:00",
                ),
                (
                    "parent-bnd",
                    "demo",
                    "parent-bnd",
                    "parent-invest",
                    "parent-invest",
                    "parents",
                    "investments",
                    "Parent Broker",
                    "Parent Brokerage",
                    "sec-bnd",
                    "Bond ETF",
                    "BND",
                    "etf",
                    20,
                    6000,
                    350,
                    7000,
                    "2026-05-31T00:00:00+00:00",
                ),
            ],
        )
        con.executemany(
            """
            INSERT INTO finance_parent_draws(
              finance_transaction_id, source, source_transaction_id, date, owner,
              finance_account_id, source_account_id, institution, account_name,
              merchant_name, name, amount, category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "parent-draw-1",
                    "demo",
                    "parent-draw-1",
                    "2026-05-30",
                    "parents",
                    "parent-cash",
                    "parent-cash",
                    "Parent Bank",
                    "Parent Checking",
                    "Pharmacy",
                    "Pharmacy",
                    40,
                    "Health",
                ),
            ],
        )
        con.executemany(
            """
            INSERT INTO finance_transactions(
              finance_transaction_id, source, source_transaction_id,
              finance_account_id, source_account_id, date, name, merchant_name,
              amount, pending, category, owner, account_group,
              is_credit_card_payment, is_internal_transfer, parent_draw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "sub-1",
                    "demo",
                    "sub-1",
                    "self-cash",
                    "self-cash",
                    "2026-03-01",
                    "Streaming",
                    "StreamCo",
                    12,
                    0,
                    "Entertainment",
                    "self",
                    "cash",
                    0,
                    0,
                    0,
                ),
                (
                    "sub-2",
                    "demo",
                    "sub-2",
                    "self-cash",
                    "self-cash",
                    "2026-04-01",
                    "Streaming",
                    "StreamCo",
                    12,
                    0,
                    "Entertainment",
                    "self",
                    "cash",
                    0,
                    0,
                    0,
                ),
                (
                    "sub-3",
                    "demo",
                    "sub-3",
                    "self-cash",
                    "self-cash",
                    "2026-05-01",
                    "Streaming",
                    "StreamCo",
                    12,
                    0,
                    "Entertainment",
                    "self",
                    "cash",
                    0,
                    0,
                    0,
                ),
            ],
        )
        con.commit()
    finally:
        con.close()


def test_bundled_finance_pages_render_with_synthetic_data(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    client = TestClient(build_app(cfg))

    self_page = client.get("/a/finance/self")
    assert self_page.status_code == 200
    assert "Self Checking" in self_page.text
    assert "VTI" in self_page.text
    assert "Self Net Worth" in self_page.text

    parents = client.get("/a/finance/parents")
    assert parents.status_code == 200
    assert "Parent Checking" in parents.text
    assert "BND" in parents.text
    assert "Parents Cashflow" in parents.text

    review = client.get("/a/finance/review")
    assert review.status_code == 200
    assert "StreamCo" in review.text
    assert "Transaction Categorization" in review.text
    assert "Source Category" in review.text
    assert "App Category" in review.text
    assert 'id="finance-category-presets"' in review.text
    assert 'list=\\"finance-category-presets\\"' in review.text
    datalist = review.text.split("</datalist>", 1)[0]
    assert 'value="Entertainment"' in review.text
    assert 'value="Restaurants &amp; Bars"' in datalist
    assert 'value="FOOD_AND_DRINK_RESTAURANT"' not in datalist
    assert "save" in review.text


def test_finance_category_form_action_redirects_back(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    client = TestClient(build_app(cfg))

    marked = client.post(
        "/api/apps/finance/actions/set_transaction_category",
        data={
            "finance_transaction_id": "sub-1",
            "category": "Pet Projects",
        },
        headers={"referer": "/a/finance/review"},
        follow_redirects=False,
    )
    assert marked.status_code == 303
    assert marked.headers["location"] == "/a/finance/review"

    review = client.get("/a/finance/review")
    assert review.status_code == 200
    assert "Pet Projects" in review.text
    assert 'id="finance-category-presets"' in review.text
    assert 'value="Pet Projects"' in review.text
    assert "clear" in review.text
