import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from personal_db.core.apps import (
    AppManifestError,
    AppQueryError,
    discover_apps,
    install_app_template,
    list_bundled_apps,
    load_app_manifest,
    load_named_queries,
    update_app_template,
)
from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, connect, init_db
from personal_db.enrichments.core import EnrichmentRunRecord, record_enrichment_run
from personal_db.enrichments.finance import RECEIPT_V1_ENRICHMENT
from personal_db.interfaces.email_context import EvidenceRef
from personal_db.services.daemon.http import build_app

from tests._daemon_auth import auth_headers

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
    assert "places" in apps
    assert apps["places"].manifest.default_page.slug == "overview"


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
    assert (dest / "models.py").exists()
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


def test_app_manifest_python_deps_defaults_empty(tmp_path):
    manifest = tmp_path / "app.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"name": "sample", "pages": [{"slug": "home", "title": "Home", "view": "v"}]}
        )
    )
    m = load_app_manifest(manifest)
    assert m.python_deps == ()


def test_app_manifest_parses_python_deps(tmp_path):
    manifest = tmp_path / "app.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "name": "sample",
                "pages": [{"slug": "home", "title": "Home", "view": "v"}],
                "python_deps": ["requests>=2.31"],
            }
        )
    )
    m = load_app_manifest(manifest)
    assert m.python_deps == ("requests>=2.31",)


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
                "reads": {"tables": ["sample"], "models": ["summary"]},
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
        "    query_url = ctx.query_url('sample_rows')\n"
        "    model_url = ctx.model_url('summary')\n"
        "    return c.page('Sample Home', c.data_grid(rows, [\n"
        "        {'field': 'id', 'headerName': 'ID'},\n"
        "        {'field': 'label', 'headerName': 'Label'},\n"
        "    ]), f'<span data-query-url=\"{query_url}\" data-model-url=\"{model_url}\"></span>')\n"
        "def render_details(ctx):\n"
        "    return c.page('Details', '<p>details page</p>')\n"
    )
    (app_dir / "models.py").write_text(
        "def summary(ctx, params):\n"
        "    rows = ctx.query('sample_rows')\n"
        "    return {'count': len(rows), 'params': params, 'rows': rows}\n"
    )

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    index = client.get("/a")
    assert index.status_code == 200
    assert "Sample App" in index.text

    home = client.get("/a/sample")
    assert home.status_code == 200
    assert "Sample Home" in home.text
    assert "Hello App" in home.text
    assert 'data-query-url="/api/v1/apps/sample/queries/sample_rows"' in home.text
    assert 'data-model-url="/api/v1/apps/sample/models/summary"' in home.text

    api = client.get("/api/v1/apps/sample/queries/sample_rows")
    assert api.status_code == 200
    assert api.json()["rows"] == [{"id": "one", "label": "Hello App"}]
    assert api.json()["query"] == "sample_rows"

    missing_api = client.get("/api/v1/apps/sample/queries/missing")
    assert missing_api.status_code == 404

    model = client.get("/api/v1/apps/sample/models/summary?scope=all")
    assert model.status_code == 200
    assert model.json()["count"] == 1
    assert model.json()["params"] == {"scope": "all"}

    undeclared_model = client.get("/api/v1/apps/sample/models/not_declared")
    assert undeclared_model.status_code == 404

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

    r = TestClient(build_app(cfg), headers=auth_headers(cfg)).get("/a/schema_app")
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

    r = TestClient(build_app(cfg), headers=auth_headers(cfg)).get("/a/broken")
    assert r.status_code == 500
    assert "error rendering app page" in r.text


def test_bundled_finance_route_renders_without_finance_tables(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/a/finance")
    assert r.status_code == 200
    assert "Finance Overview" in r.text
    assert "No finance data yet" in r.text


def test_finance_receipts_page_shows_latest_and_queues_rerun(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_V1_ENRICHMENT,
            input_table="finance_transactions",
            input_id="transport-1",
            status="no_match",
            result={
                "decision": "receipt_not_matched",
                "receipt_candidate_count": 2,
                "candidate_evidence_count": 2,
                "agent_result": {
                    "receipt_match": "no",
                    "reasoning": "Older Lyft receipts did not explain the charge.",
                },
            },
            evidence=[
                EvidenceRef(
                    source="spark_email",
                    ref="spark_email:message:80888",
                    kind="email_message",
                    title="Spark email message 80888",
                )
            ],
            result_summary="Receipt match: no (Lyft)",
            confidence=0.6,
        ),
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    page = client.get("/a/finance/receipts")
    assert page.status_code == 200
    assert "Finance Receipts" in page.text
    assert "Receipt Enrichment" in page.text
    assert "Older Lyft receipts did not explain the charge." in page.text
    assert "spark_email:message:80888" in page.text
    assert "rerun_receipt_enrichment" in page.text

    rerun = client.post(
        "/api/v1/apps/finance/actions/rerun_receipt_enrichment",
        data={"finance_transaction_id": "transport-1"},
        headers={"referer": "/a/finance/receipts"},
        follow_redirects=False,
    )
    assert rerun.status_code == 303
    assert rerun.headers["location"] == "/a/finance/receipts"
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            """
            SELECT enrichment_name, status, priority, payload_json
            FROM enrichment_jobs
            WHERE input_id='transport-1'
            """
        ).fetchone()
    finally:
        con.close()
    assert row[0] == RECEIPT_V1_ENRICHMENT
    assert row[1] == "pending"
    assert row[2] == 50
    assert '"max_candidate_threads": 20' in row[3]


def test_finance_review_actions_write_app_state(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    marked = client.post(
        "/api/v1/apps/finance/actions/mark_reviewed",
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
        "/api/v1/apps/finance/actions/clear_review",
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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/apps/finance/actions/not_declared", json={})
    assert r.status_code == 404


def test_app_action_rejects_cross_origin_browser_writes(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    rejected = client.post(
        "/api/v1/apps/finance/actions/set_transaction_category",
        json={"finance_transaction_id": "txn-food-1", "category": "Dining"},
        headers={"origin": "https://evil.example"},
    )
    assert rejected.status_code == 403

    accepted = client.post(
        "/api/v1/apps/finance/actions/set_transaction_category",
        json={"finance_transaction_id": "txn-food-1", "category": "Dining"},
        headers={"origin": "http://testserver"},
    )
    assert accepted.status_code == 200


def test_finance_category_actions_write_canonical_finance_state(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    set_category = client.post(
        "/api/v1/apps/finance/actions/set_transaction_category",
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
            SELECT user_category, note
            FROM finance_transaction_user_categories
            WHERE finance_transaction_id='txn-food-1'
            """
        ).fetchone()
        preset = con.execute(
            """
            SELECT category
            FROM finance_categories
            WHERE category='Dining'
            """
        ).fetchone()
    finally:
        con.close()
    assert row == ("Dining", "manual override")
    assert preset == ("Dining",)

    cleared = client.post(
        "/api/v1/apps/finance/actions/clear_transaction_category",
        json={"finance_transaction_id": "txn-food-1"},
    )
    assert cleared.status_code == 200
    con = connect(cfg.db_path)
    try:
        count = con.execute("SELECT COUNT(*) FROM finance_transaction_user_categories").fetchone()[0]
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
                (
                    "rent-1",
                    "demo",
                    "rent-1",
                    "self-cash",
                    "self-cash",
                    "2026-05-05",
                    "BILT CARD HOUSING Yiheng Chen",
                    "BILT CARD HOUSING Yiheng Chen",
                    3000,
                    0,
                    "RENT_AND_UTILITIES_RENT",
                    "self",
                    "cash",
                    0,
                    0,
                    0,
                ),
                (
                    "rent-greystar-1",
                    "demo",
                    "rent-greystar-1",
                    "parent-cash",
                    "parent-cash",
                    "2026-05-04",
                    "To Greystar Paches Web Ypep System Generated",
                    "Greystar",
                    2500,
                    0,
                    "Mortgage",
                    "parents",
                    "cash",
                    0,
                    0,
                    0,
                ),
                (
                    "rent-bilt-transfer-1",
                    "demo",
                    "rent-bilt-transfer-1",
                    "self-cash",
                    "self-cash",
                    "2026-05-03",
                    "ACH PAYMENT TO BILT CARD-PMT",
                    "To Bilt Ypep System Generated",
                    2500,
                    0,
                    "Transfer",
                    "self",
                    "cash",
                    0,
                    1,
                    0,
                ),
                (
                    "rent-reimb-1",
                    "demo",
                    "rent-reimb-1",
                    "self-cash",
                    "self-cash",
                    "2026-05-06",
                    "ZELLE FROM OLIVER ZOU RENT",
                    "ZELLE FROM OLIVER ZOU RENT",
                    -1000,
                    0,
                    "OTHER_OTHER",
                    "self",
                    "cash",
                    0,
                    1,
                    0,
                ),
                (
                    "rent-reimb-2",
                    "demo",
                    "rent-reimb-2",
                    "self-cash",
                    "self-cash",
                    "2026-05-06",
                    "Curiosity Research reimbursement",
                    "Curiosity Research reimbursement",
                    -500,
                    0,
                    "INCOME_CONTRACTOR",
                    "self",
                    "cash",
                    0,
                    0,
                    0,
                ),
                (
                    "food-1",
                    "demo",
                    "food-1",
                    "self-card",
                    "self-card",
                    "2026-05-07",
                    "Dinner",
                    "Dinner",
                    90,
                    0,
                    "FOOD_AND_DRINK_RESTAURANT",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "transport-1",
                    "demo",
                    "transport-1",
                    "self-card",
                    "self-card",
                    "2026-05-08",
                    "Lyft",
                    "Lyft",
                    45,
                    0,
                    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "ai-1",
                    "demo",
                    "ai-1",
                    "self-card",
                    "self-card",
                    "2026-05-09",
                    "OpenAI",
                    "OpenAI",
                    60,
                    0,
                    "GENERAL_SERVICES_OTHER_GENERAL_SERVICES",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "subscription-1",
                    "demo",
                    "subscription-1",
                    "self-card",
                    "self-card",
                    "2026-05-10",
                    "Netflix",
                    "Netflix",
                    30,
                    0,
                    "ENTERTAINMENT_TV_AND_MOVIES",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "other-1",
                    "demo",
                    "other-1",
                    "self-card",
                    "self-card",
                    "2026-05-11",
                    "Amazon",
                    "Amazon",
                    20,
                    0,
                    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "ai-omi-1",
                    "demo",
                    "ai-omi-1",
                    "self-card",
                    "self-card",
                    "2026-05-12",
                    "Omi Based Hardware",
                    "Omi Based Hardware",
                    19.99,
                    0,
                    "HOME_IMPROVEMENT_HARDWARE",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "ai-grok-1",
                    "demo",
                    "ai-grok-1",
                    "self-card",
                    "self-card",
                    "2026-05-13",
                    "Grok Xai",
                    "Grok Xai",
                    5,
                    0,
                    "GENERAL_SERVICES_OTHER_GENERAL_SERVICES",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "ai-benjaminfire-1",
                    "demo",
                    "ai-benjaminfire-1",
                    "self-card",
                    "self-card",
                    "2026-05-13",
                    "Benjaminfire",
                    "Benjaminfire",
                    99.99,
                    0,
                    "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "health-1",
                    "demo",
                    "health-1",
                    "self-card",
                    "self-card",
                    "2026-05-14",
                    "My Penn Medicine",
                    "My Penn Medicine",
                    212.09,
                    0,
                    "MEDICAL_OTHER_MEDICAL",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "health-2",
                    "demo",
                    "health-2",
                    "self-card",
                    "self-card",
                    "2026-05-15",
                    "LabCorp",
                    "LabCorp",
                    6.46,
                    0,
                    "MEDICAL_OTHER_MEDICAL",
                    "self",
                    "credit_card",
                    0,
                    0,
                    0,
                ),
                (
                    "subscription-apple-1",
                    "demo",
                    "subscription-apple-1",
                    "self-card",
                    "self-card",
                    "2026-05-16",
                    "Apple",
                    "Apple",
                    9.99,
                    0,
                    "GENERAL_MERCHANDISE_ELECTRONICS",
                    "self",
                    "credit_card",
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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    overview = client.get("/a/finance")
    assert overview.status_code == 200
    assert 'class="finance-app-controls" data-finance-page' in overview.text
    assert 'class="finance-dashboard"' in overview.text
    assert "data-finance-dashboard" in overview.text
    assert "data-finance-self-only checked" in overview.text
    assert 'data-finance-parent="1"' in overview.text
    assert 'data-finance-parent-tab="1"' in overview.text
    assert "Parent Cashflow" in overview.text
    assert "Card Payments" not in overview.text
    assert '"label": "Income"' in overview.text
    assert '"label": "Spending"' in overview.text
    assert '"label": "Transactions"' in overview.text
    assert "Personal Burn Rate" in overview.text
    assert "Smoothed monthly estimate from up to 180 days" in overview.text
    assert "smoothed / mo" in overview.text
    assert "🏠 Rent" in overview.text
    assert "🤖 AI spending" in overview.text
    assert "🗑️ Wasted" in overview.text
    assert "data-burn-rate" in overview.text
    assert 'data-pdb-island="finance-burn-rate"' in overview.text
    assert 'data-burn-rate-state-url="/api/v1/apps/finance/models/burn_rate"' in overview.text
    assert 'class="burn-rate-card" data-burn-bucket="rent"' in overview.text
    assert 'class="burn-rate-card" data-burn-bucket="food"' in overview.text
    assert 'class="burn-rate-card" data-burn-bucket="health"' in overview.text
    assert 'class="burn-rate-card has-color" data-burn-bucket="wasted"' in overview.text
    assert 'style="--burn-bucket-color:red"' in overview.text
    assert overview.text.index('data-burn-bucket="wasted"') < overview.text.index(
        'data-burn-bucket="other"'
    )
    assert overview.text.index('data-burn-bucket="other"') < overview.text.index("data-burn-add")
    assert 'data-burn-add-button' in overview.text
    assert 'name="emoji" placeholder="Emoji"' in overview.text
    assert 'name="label" placeholder="New category"' in overview.text
    assert 'class=\\"burn-action\\"' in overview.text
    assert '<option value=\\"merchant\\">merchant<\\/option>' in overview.text
    assert '<option value=\\"category\\">category<\\/option>' in overview.text
    assert "burn-rate-tx-grid" in overview.text
    assert "data-pdb-grid" in overview.text
    assert "__burnBucket" in overview.text
    assert "Matched Rule" in overview.text
    assert "AI spending" in overview.text
    assert "Health" in overview.text
    assert "Other subscriptions" in overview.text
    assert "rent reimbursement" in overview.text
    assert "Greystar" in overview.text
    assert "To Bilt Ypep System Generated" not in overview.text
    assert "Omi Based Hardware" in overview.text
    assert "Grok Xai" in overview.text
    assert "Benjaminfire" in overview.text
    assert "My Penn Medicine" in overview.text
    assert "LabCorp" in overview.text
    assert "Apple" in overview.text
    assert "OpenAI" in overview.text
    assert "Netflix" in overview.text

    self_page = client.get("/a/finance/self")
    assert self_page.status_code == 200
    assert "Self Checking" in self_page.text
    assert "VTI" in self_page.text
    assert "Self Net Worth" in self_page.text
    assert "Card Payments" in self_page.text

    parents = client.get("/a/finance/parents")
    assert parents.status_code == 200
    assert "Parent Checking" in parents.text
    assert "BND" in parents.text
    assert "Parents Cashflow" in parents.text

    review = client.get("/a/finance/review")
    assert review.status_code == 200
    assert "StreamCo" in review.text
    assert "Finance Review" in review.text
    assert "Transaction Categorization" in review.text
    assert "Source Category" in review.text
    assert "App Category" in review.text
    assert "Parent Draws" in review.text
    assert "Parent Bank" in review.text
    assert "Pharmacy" in review.text
    assert "Recurring Candidates" in review.text
    assert 'data-pdb-island="finance-categorize"' in review.text
    assert 'data-categorize-state-url="/api/v1/apps/finance/models/categorize"' in review.text
    assert "Needs review" in review.text
    assert "reviewed" in review.text
    assert "ignore" in review.text
    assert 'id="finance-category-presets"' in review.text
    assert 'list=\\"finance-category-presets\\"' in review.text
    datalist = review.text.split("</datalist>", 1)[0]
    assert 'value="Subscriptions"' in review.text
    assert 'value="Entertainment"' not in review.text
    assert 'value="Restaurants &amp; Bars"' not in datalist
    assert 'value="FOOD_AND_DRINK_RESTAURANT"' not in datalist
    assert "save" in review.text

    parents_page = client.get("/a/finance/parents")
    assert parents_page.status_code == 200
    assert "Parent Account Draws" in parents_page.text
    assert "Outflows from parent-managed accounts" in parents_page.text

    rules = client.get("/a/finance/rules")
    assert rules.status_code == 200
    assert "Finance Rules" in rules.text
    assert 'data-pdb-island="finance-rules"' in rules.text
    assert "Burn Rate Buckets" in rules.text
    assert "Emoji" in rules.text
    assert "Wasted" in rules.text
    assert "🗑️" in rules.text
    assert "set_burn_bucket_color" in rules.text
    assert "Burn Rate Rules" in rules.text
    assert "Seed rules plus inline merchant/category rules" in rules.text
    assert "Benjaminfire AI merchant" in rules.text
    assert "Auditable burn-rate classification rules" in rules.text

    settings = client.get("/a/finance/settings")
    assert settings.status_code == 200
    assert "Finance Settings" in settings.text
    assert "Burn Rate Rules" not in settings.text


def test_finance_category_form_action_redirects_back(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    marked = client.post(
        "/api/v1/apps/finance/actions/set_transaction_category",
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


def test_finance_categorize_model_reflects_inline_edits(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    marked = client.post(
        "/api/v1/apps/finance/actions/set_transaction_category",
        json={
            "finance_transaction_id": "sub-1",
            "category": "Pet Projects",
        },
    )
    assert marked.status_code == 200

    reviewed = client.post(
        "/api/v1/apps/finance/actions/mark_reviewed",
        json={
            "review_key": "parent-draw-1",
            "kind": "parent_draw",
            "status": "ignored",
        },
    )
    assert reviewed.status_code == 200

    state = client.get("/api/v1/apps/finance/models/categorize")
    assert state.status_code == 200
    body = state.json()
    assert body["actions"]["set_category"].endswith("/set_transaction_category")
    assert "Pet Projects" in body["category_presets"]
    transaction = next(
        row for row in body["transactions"] if row["finance_transaction_id"] == "sub-1"
    )
    assert transaction["app_category"] == "Pet Projects"
    parent_draw = next(row for row in body["parent_draws"] if row["review_key"] == "parent-draw-1")
    assert parent_draw["status"] == "ignored"
    assert parent_draw["status_label"] == "Ignored"


def test_finance_burn_inline_classification_updates_overview(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    marked = client.post(
        "/api/v1/apps/finance/actions/set_burn_classification",
        data={
            "finance_transaction_id": "other-1",
            "merchant": "Amazon",
            "source_category": "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
            "bucket": "ai",
            "scope": "transaction",
        },
        headers={"referer": "/a/finance/overview"},
        follow_redirects=False,
    )
    assert marked.status_code == 303
    assert marked.headers["location"] == "/a/finance/overview"

    overview = client.get("/a/finance")
    assert overview.status_code == 200
    assert "transaction override" in overview.text

    rule = client.post(
        "/api/v1/apps/finance/actions/set_burn_classification",
        json={
            "merchant": "Amazon",
            "source_category": "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
            "bucket": "subscriptions",
            "scope": "merchant",
        },
    )
    assert rule.status_code == 200
    assert rule.json()["bucket"] == "subscriptions"
    assert rule.json()["burn_rate"]["bucket_counts"]["subscriptions"] >= 1

    bucket = client.post(
        "/api/v1/apps/finance/actions/create_burn_bucket",
        data={"label": "Pet Projects", "emoji": "💡"},
        headers={"referer": "/a/finance/overview"},
        follow_redirects=False,
    )
    assert bucket.status_code == 303
    assert bucket.headers["location"] == "/a/finance/overview"

    overview = client.get("/a/finance")
    assert overview.status_code == 200
    assert 'data-burn-bucket="pet_projects"' in overview.text
    assert "💡 Pet Projects" in overview.text
    assert '<option value=\\"pet_projects\\">💡 Pet Projects<\\/option>' in overview.text

    colored_bucket = client.post(
        "/api/v1/apps/finance/actions/set_burn_bucket_color",
        json={"bucket": "pet_projects", "label": "Pet Projects", "color": "blue"},
    )
    assert colored_bucket.status_code == 200
    assert colored_bucket.json()["color"] == "blue"
    assert colored_bucket.json()["burn_rate"]["bucket_counts"]["pet_projects"] == 0

    overview = client.get("/a/finance")
    assert overview.status_code == 200
    assert 'data-burn-bucket="pet_projects"' in overview.text
    assert "💡 Pet Projects" in overview.text
    assert 'style="--burn-bucket-color:blue"' in overview.text


def test_finance_burn_merchant_rules_replace_legacy_bucket_rules(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    assert client.get("/a/finance").status_code == 200
    con = connect(cfg.db_path)
    try:
        con.executemany(
            """
            INSERT INTO app_finance_burn_rules(
              rule_key, priority, label, bucket, merchant_pattern,
              amount_direction, reason, source
            )
            VALUES (?, 30, ?, ?, 'amazon', 'positive', 'user merchant rule', 'user')
            """,
            [
                ("user:merchant:amazon:education", "Amazon education", "education"),
                ("user:merchant:amazon:other", "Amazon other", "other"),
            ],
        )
        con.commit()
    finally:
        con.close()

    rule = client.post(
        "/api/v1/apps/finance/actions/set_burn_classification",
        json={
            "merchant": "Amazon",
            "source_category": "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
            "bucket": "subscriptions",
            "scope": "merchant",
        },
    )

    assert rule.status_code == 200
    assert rule.json()["bucket"] == "subscriptions"
    amazon_rows = [
        row
        for row in rule.json()["burn_rate"]["rows"]
        if row["finance_transaction_id"] == "other-1"
    ]
    assert amazon_rows[0]["bucket"] == "subscriptions"

    con = connect(cfg.db_path)
    try:
        rows = con.execute(
            """
            SELECT rule_key, bucket
            FROM app_finance_burn_rules
            WHERE source='user'
              AND merchant_pattern='amazon'
            ORDER BY rule_key
            """
        ).fetchall()
    finally:
        con.close()
    assert rows == [("user:merchant:amazon", "subscriptions")]


def test_finance_burn_bucket_metadata_migrates_legacy_table(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_app_db(cfg)
    con = sqlite3.connect(cfg.db_path)
    try:
        con.execute(
            """
            CREATE TABLE app_finance_burn_buckets (
              bucket     TEXT PRIMARY KEY,
              label      TEXT NOT NULL,
              sort_order INTEGER NOT NULL DEFAULT 1000,
              source     TEXT NOT NULL DEFAULT 'user',
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        con.execute(
            """
            INSERT INTO app_finance_burn_buckets(bucket, label, sort_order, source)
            VALUES ('wasted', 'Wasted', 900, 'user')
            """
        )
        con.commit()
    finally:
        con.close()

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    overview = client.get("/a/finance")
    assert overview.status_code == 200
    assert "🏠 Rent" in overview.text
    assert "🗑️ Wasted" in overview.text

    con = sqlite3.connect(cfg.db_path)
    try:
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(app_finance_burn_buckets)")}
        assert {"color", "emoji"} <= columns
        assert con.execute(
            "SELECT emoji, color FROM app_finance_burn_buckets WHERE bucket='wasted'"
        ).fetchone() == ("🗑️", "red")
    finally:
        con.close()


def _seed_places_app_db(cfg: Config) -> None:
    init_db(cfg.db_path)
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executescript(
            """
            CREATE TABLE raw_locations (
              id INTEGER PRIMARY KEY,
              lat REAL NOT NULL,
              lon REAL NOT NULL,
              ts TEXT NOT NULL
            );
            CREATE TABLE geocoded_locations (
              source_id INTEGER PRIMARY KEY REFERENCES raw_locations(id),
              place_name TEXT
            );
            CREATE TABLE daily_locations (
              date TEXT NOT NULL,
              place_name TEXT NOT NULL,
              visits INTEGER NOT NULL,
              PRIMARY KEY (date, place_name)
            );
            """
        )
        con.executemany(
            "INSERT INTO raw_locations(id, lat, lon, ts) VALUES (?, ?, ?, ?)",
            [
                (1, 37.7749, -122.4194, "2026-05-29T09:00:00+00:00"),
                (2, 37.7750, -122.4195, "2026-05-29T10:00:00+00:00"),
                (3, 37.7890, -122.4010, "2026-05-29T14:00:00+00:00"),
                (4, 37.7891, -122.4011, "2026-05-30T15:00:00+00:00"),
                (5, 37.7610, -122.4260, "2026-05-30T21:00:00+00:00"),
            ],
        )
        con.executemany(
            "INSERT INTO geocoded_locations(source_id, place_name) VALUES (?, ?)",
            [
                (1, "Home Address"),
                (2, "Home Address"),
                (3, "Studio"),
                (4, "Studio"),
                (5, "Park"),
            ],
        )
        con.executemany(
            "INSERT INTO daily_locations(date, place_name, visits) VALUES (?, ?, ?)",
            [
                ("2026-05-29", "Home Address", 2),
                ("2026-05-29", "Studio", 1),
                ("2026-05-30", "Studio", 1),
                ("2026-05-30", "Park", 1),
            ],
        )
        con.commit()
    finally:
        con.close()


def test_bundled_places_route_renders_without_location_tables(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    overview = client.get("/a/places")
    assert overview.status_code == 200
    assert "Places" in overview.text
    assert "mobile export pending" in overview.text
    assert "Maps use exact local GPS coordinates" in overview.text


def test_bundled_places_pages_render_with_synthetic_data(tmp_root, frozen_datetime):
    # Places views window to the last `default_days` (30) via datetime.now();
    # the fixture data below is dated 2026-05-29/30, so freeze "now" nearby
    # rather than let it drift out of the window as real time passes.
    frozen_datetime(2026, 6, 5)
    cfg = Config(root=tmp_root)
    _seed_places_app_db(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    overview = client.get("/a/places")
    assert overview.status_code == 200
    assert "Places" in overview.text
    assert overview.text.count("<h1>Places</h1>") == 1
    assert "Home Address" in overview.text
    assert "Frequent Places" in overview.text
    assert 'class="places-leaflet-map places-map"' in overview.text
    assert "tile.openstreetmap.org" in overview.text
    assert "leaflet.heat" in overview.text

    timeline = client.get("/a/places/timeline")
    assert timeline.status_code == 200
    assert "Recent Place Timeline" in timeline.text
    assert "Studio" in timeline.text

    map_page = client.get("/a/places/map")
    assert map_page.status_code == 200
    assert "Location Heatmap" in map_page.text
    assert "Movement (24h)" in map_page.text

    rhythm = client.get("/a/places/rhythm")
    assert rhythm.status_code == 200
    assert "Week By Hour" in rhythm.text
    assert "Place Regularity" in rhythm.text

    privacy = client.get("/a/places/privacy")
    assert privacy.status_code == 200
    assert "Display Settings" in privacy.text
    assert "Place Aliases" in privacy.text


def test_places_pages_render_with_raw_points_before_geocoding(tmp_root, frozen_datetime):
    frozen_datetime(2026, 6, 5)
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executescript(
            """
            CREATE TABLE raw_locations (
              id INTEGER PRIMARY KEY,
              lat REAL NOT NULL,
              lon REAL NOT NULL,
              ts TEXT NOT NULL
            );
            """
        )
        con.executemany(
            "INSERT INTO raw_locations(id, lat, lon, ts) VALUES (?, ?, ?, ?)",
            [
                (1, 37.7749, -122.4194, "2026-05-29T09:00:00+00:00"),
                (2, 37.7890, -122.4010, "2026-05-29T14:00:00+00:00"),
            ],
        )
        con.commit()
    finally:
        con.close()

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    overview = client.get("/a/places")
    assert overview.status_code == 200
    assert "(unlabeled)" in overview.text
    assert 'class="places-leaflet-map places-map"' in overview.text

    timeline = client.get("/a/places/timeline")
    assert timeline.status_code == 200
    assert "(unlabeled)" in timeline.text


def test_places_pages_render_with_installed_location_tracker_schema(tmp_root, frozen_datetime):
    frozen_datetime(2026, 6, 5)
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executescript(
            """
            CREATE TABLE location_points (
              id TEXT PRIMARY KEY,
              recorded_at TEXT NOT NULL,
              latitude REAL NOT NULL,
              longitude REAL NOT NULL,
              accuracy REAL
            );
            CREATE TABLE geocoded_locations (
              recorded_at TEXT PRIMARY KEY,
              formatted_address TEXT,
              place_id TEXT
            );
            """
        )
        con.executemany(
            """
            INSERT INTO location_points(id, recorded_at, latitude, longitude, accuracy)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("a", "2026-05-29T09:00:00+00:00", 37.7749, -122.4194, 5),
                ("b", "2026-05-29T10:00:00+00:00", 37.7750, -122.4195, 8),
                ("c", "2026-05-29T14:00:00+00:00", 37.7890, -122.4010, 12),
            ],
        )
        con.executemany(
            """
            INSERT INTO geocoded_locations(recorded_at, formatted_address, place_id)
            VALUES (?, ?, ?)
            """,
            [
                ("2026-05-29T09:00:00+00:00", "Home Address", "home"),
                ("2026-05-29T10:00:00+00:00", "Home Address", "home"),
                ("2026-05-29T14:00:00+00:00", "Studio", "studio"),
            ],
        )
        con.commit()
    finally:
        con.close()

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    overview = client.get("/a/places")
    assert overview.status_code == 200
    assert "Home Address" in overview.text
    assert "Studio" in overview.text
    assert "Points" in overview.text
    assert 'class="places-leaflet-map places-map"' in overview.text
    assert "tile.openstreetmap.org" in overview.text

    rhythm = client.get("/a/places/rhythm")
    assert rhythm.status_code == 200
    assert "Week By Hour" in rhythm.text
    assert "Home Address" in rhythm.text


def test_places_privacy_actions_write_app_state(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_places_app_db(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    settings = client.post(
        "/api/v1/apps/places/actions/set_privacy",
        json={"blur_precision_m": "1000", "default_days": "14", "hide_coordinates": "1"},
    )
    assert settings.status_code == 200
    assert settings.json()["settings"]["default_days"] == "14"

    alias = client.post(
        "/api/v1/apps/places/actions/set_place_alias",
        json={"place_name": "Home Address", "alias": "Home", "hidden": "1"},
    )
    assert alias.status_code == 200
    assert alias.json()["alias"] == "Home"
    assert alias.json()["hidden"] is True

    overview = client.get("/a/places")
    assert overview.status_code == 200
    assert "Maps use exact local GPS coordinates" in overview.text
    assert "Home Address" not in overview.text

    cleared = client.post(
        "/api/v1/apps/places/actions/clear_place_alias",
        json={"place_name": "Home Address"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["removed"] == 1
