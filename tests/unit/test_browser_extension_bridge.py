import json
import shlex
import shutil
import stat
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

from personal_db.browser_extension.bridge import catalog, host
from personal_db.browser_extension.bridge import install as bridge_install


def test_manifest_is_personal_db_only_and_has_a_stable_distinct_id():
    manifest_path = bridge_install.extension_dir() / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest["name"] == "Personal DB XHS Collector"
    assert manifest["version"] == "0.2.0"
    assert manifest["key"]
    assert bridge_install.extension_id() == "domgbmjbfpbdalanafmgkgjakdhmgphb"
    assert bridge_install.extension_id() != "kaaokpiflaikgaglkmiichebgamelpce"
    assert manifest["host_permissions"] == [
        "https://www.xiaohongshu.com/*",
        "https://xiaohongshu.com/*",
        "https://creator.xiaohongshu.com/*",
    ]
    assert "com.mypalantir.collector" not in manifest_path.read_text()


def test_loadable_chrome_root_contains_no_reserved_filenames():
    """Chrome reserves every unpacked-extension path component starting with ``_``."""
    extension_root = bridge_install.extension_dir()
    reserved = [
        path.relative_to(extension_root)
        for path in extension_root.rglob("*")
        if path.name.startswith("_")
    ]

    assert reserved == []


def test_creator_collector_has_safe_empty_result_diagnostics_and_clear_failures():
    creator = (bridge_install.extension_dir() / "collectors" / "xhs" / "creator.js").read_text()
    api_tap = (bridge_install.extension_dir() / "collectors" / "xhs" / "creator_api_tap.js").read_text()
    background = (bridge_install.extension_dir() / "background.js").read_text()

    # The diagnostic payload deliberately exposes presentation metadata only,
    # not page text or a full URL that may contain an XHS access parameter.
    assert "href: safeHref()" in creator
    assert "selectorCounts: selectorCounts()" in creator
    assert "loginMarkers: pageMarkers()" in creator
    assert "rawPageText" not in creator
    assert "login_required" in creator
    assert "verification_required" in creator
    assert "selector_mismatch" in creator
    assert "empty: rows.length === 0" in creator
    assert "safeDiagnostics(mergedState.diagnostics)" in background
    assert "${url.origin}${url.pathname}" in background
    # The API tap is static, MAIN-world document-start code that observes the
    # creator page's exact first-party endpoint only. Its projection has no
    # headers, cookies, query strings, or raw response payload.
    assert "const ALLOWED_ENDPOINTS = new Set([" in api_tap
    assert "'https://creator.xiaohongshu.com/api/galaxy/v2/creator/note/user/posted'" in api_tap
    assert "'https://edith.xiaohongshu.com/web_api/sns/v5/creator/note/managemaent/search'" in api_tap
    assert "ALLOWED_ENDPOINTS.has(`${url.origin}${url.pathname}`)" in api_tap
    assert "window.fetch = function" in api_tap
    assert "XMLHttpRequest.prototype.open" in api_tap
    assert "item.imagesList?.[0]" in api_tap
    assert "item.images_list?.[0]" in api_tap
    assert "item.likes" in api_tap
    assert "item.refTip" in api_tap
    assert "item.permission_msg" in api_tap
    assert "item.comments_count" in api_tap
    assert "item.shared_count" in api_tap
    assert "item.videoInfo?.duration" in api_tap
    assert "credentials:" not in api_tap
    assert "document.cookie" not in api_tap
    assert "headers:" not in api_tap
    assert "responseText" in api_tap  # parsed locally, never retained or returned
    assert "rawResponse" not in api_tap
    assert "rawBody" not in api_tap
    assert "matches: ['https://creator.xiaohongshu.com/new/note-manager*']" in background
    assert "world: 'MAIN'" in background
    assert "removeCreatorApiTap" in background
    assert "creatorApiMayStillPopulate" in background
    assert "creatorApiSuffix(apiState)" in background
    assert "...state," in background
    # DOM cards can expose unrelated IDs. A creator job must wait for the
    # allowlisted API observation and never return those guessed DOM rows.
    assert "CREATOR_API_OBSERVATION_GRACE_MS" in background
    assert "if (apiSummary?.rowCount) return mergedState" in background
    assert "XHS creator API was not observed" in background
    assert "XHS creator API returned no recognizable note rows" in background


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node to exercise extension asset")
def test_creator_api_tap_accepts_only_edith_exact_search_and_normalizes_rows():
    api_tap = bridge_install.extension_dir() / "collectors" / "xhs" / "creator_api_tap.js"
    script = textwrap.dedent(
        """
        const fs = require('fs')
        const vm = require('vm')
        const source = fs.readFileSync(process.argv[1], 'utf8')
        function XMLHttpRequest() {}
        XMLHttpRequest.prototype.open = function () {}
        XMLHttpRequest.prototype.send = function () {}
        const payload = { data: { note_list: [{
          id: '693f43a2000000001e02a45d', displayTitle: 'A note',
          images_list: [{ url: 'https://img.example/cover.jpg?secret=query' }], likes: '12',
          comments_count: 3, shared_count: 4, permission_msg: 'private', time: 1716000000,
        }] } }
        const window = { fetch: async () => ({ ok: true, clone: () => ({ json: async () => payload }) }) }
        const context = {
          window,
          location: {
            origin: 'https://creator.xiaohongshu.com', pathname: '/new/note-manager',
            href: 'https://creator.xiaohongshu.com/new/note-manager',
          },
          XMLHttpRequest, URL, Promise, WeakSet, Object, Array, Date, String, Number, RegExp,
        }
        ;(async () => {
          vm.runInNewContext(source, context)
          // Same search path on the creator origin is intentionally not allowed.
          await window.fetch('https://creator.xiaohongshu.com/web_api/sns/v5/creator/note/managemaent/search')
          await window.fetch('https://creator.xiaohongshu.com/api/galaxy/v2/creator/note/user/posted?page=1&tab=all')
          await window.fetch('https://edith.xiaohongshu.com/web_api/sns/v5/creator/note/managemaent/search?access=ignored')
          await new Promise((resolve) => setImmediate(resolve))
          const state = window.__personalDbXhsCreatorApiTap
          if (state.requestCount !== 2 || state.responseCount !== 2 || state.rows.length !== 1) throw new Error(JSON.stringify(state))
          if (state.rows[0].thumbnail_url !== 'https://img.example/cover.jpg' || state.rows[0].liked_count !== '12' || state.rows[0].comment_count !== 3 || state.rows[0].share_count !== 4 || state.rows[0].visibility_label !== 'private') throw new Error(JSON.stringify(state.rows[0]))
        })().catch((error) => { console.error(error); process.exit(1) })
        """
    )

    subprocess.run(
        ["node", "-e", script, str(api_tap)],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"cmd": "inspect"},
        {"cmd": "collect", "job": {"collectorFile": "arbitrary.js"}},
        {
            "cmd": "collect",
            "job": {
                "source": "xhs_saved", "url": "https://example.com/", "collectorFile": "collectors/xhs/saved.js",
                "globalName": "__personalDbXhsSaved", "cfg": {}, "timeoutMs": 1000,
            },
        },
        {
            "cmd": "collect",
            "job": {
                "source": "xhs", "url": "https://creator.xiaohongshu.com/", "collectorFile": "collectors/xhs/creator.js",
                "globalName": "__personalDbXhsCreator", "cfg": {"focused": True}, "timeoutMs": 1000,
            },
        },
    ],
)
def test_bridge_rejects_non_allowlisted_requests(payload):
    with pytest.raises(host.RequestError):
        host.validate_request(payload)


def test_bridge_accepts_only_the_creator_and_saved_contracts():
    creator = host.validate_request({
        "cmd": "collect",
        "job": {
            "source": "xhs", "url": "https://creator.xiaohongshu.com/creator/home", "collectorFile": "collectors/xhs/creator.js",
            "globalName": "__personalDbXhsCreator", "cfg": {"maxScrolls": 10}, "timeoutMs": 600_000,
        },
    })
    saved = host.validate_request({
        "cmd": "collect",
        "job": {
            "source": "xhs_saved", "url": "https://www.xiaohongshu.com/user/profile/a?tab=fav&subTab=note",
            "collectorFile": "collectors/xhs/saved.js", "globalName": "__personalDbXhsSaved",
            "cfg": {"knownIds": ["a"], "deepBackfill": False}, "timeoutMs": 600_000,
        },
    })

    assert creator["job"]["globalName"] == "__personalDbXhsCreator"
    assert saved["job"]["globalName"] == "__personalDbXhsSaved"


def test_socket_server_creates_owner_only_socket_and_parent():
    bridge = host.NativeBridge(host.DEFAULT_TIMEOUT_MS)
    path = Path("/tmp") / f"pdb-bridge-{uuid.uuid4().hex[:12]}" / "bridge.sock"
    server = host.UnixRequestServer(path, bridge)
    server.bind()
    try:
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        server.close()
        path.parent.rmdir()


def test_v2_request_never_accepts_code_paths_world_or_urls():
    valid = host.validate_request(
        {
            "v": 2,
            "op": "collect",
            "connector": "xhs.creator.v2",
            "input": {"maxScrolls": 12, "delayMs": 900},
            "timeoutMs": 180_000,
        }
    )
    assert valid["connector"] == "xhs.creator.v2"
    assert valid["input"] == {"maxScrolls": 12, "delayMs": 900}

    for hostile_field in ("code", "source", "collectorFile", "globalName", "world", "url", "headers"):
        hostile = {
            "v": 2, "op": "collect", "connector": "xhs.creator.v2",
            "input": {}, "timeoutMs": 180_000, hostile_field: "hostile",
        }
        with pytest.raises(host.RequestError):
            host.validate_request(hostile)
    with pytest.raises(host.RequestError, match="unknown connector"):
        host.validate_request({"v": 2, "op": "collect", "connector": "anything.v2", "input": {}, "timeoutMs": 1})
    with pytest.raises(host.RequestError, match="maxScrolls"):
        host.validate_request({"v": 2, "op": "collect", "connector": "xhs.creator.v2", "input": {"maxScrolls": 101}, "timeoutMs": 1})


def test_connector_catalog_rejects_tampered_source(tmp_path, monkeypatch):
    source_root = Path(catalog._catalog_root())
    copied = tmp_path / "xhs"
    shutil.copytree(source_root, copied)
    (copied / "creator_api_tap.js").write_text("tampered")
    monkeypatch.setattr(catalog, "_catalog_root", lambda: copied)

    with pytest.raises(catalog.ConnectorCatalogError, match="sha256"):
        catalog.load_connector_bundle("xhs.creator.v2")


@pytest.mark.parametrize("source, error", [(b"", "empty"), (b"x" * 250_001, "maximum size")])
def test_connector_catalog_rejects_empty_or_oversized_source_before_hashing(tmp_path, monkeypatch, source, error):
    source_root = Path(catalog._catalog_root())
    copied = tmp_path / "xhs"
    shutil.copytree(source_root, copied)
    (copied / "creator_api_tap.js").write_bytes(source)
    monkeypatch.setattr(catalog, "_catalog_root", lambda: copied)

    with pytest.raises(catalog.ConnectorCatalogError, match=error):
        catalog.load_connector_bundle("xhs.creator.v2")


def test_v2_host_loads_internal_bundle_then_collects():
    class FakeBridge:
        def __init__(self):
            self.requests = []

        def ask(self, request):
            self.requests.append(request)
            if request["cmd"] == "load_user_script_bundle":
                return {"result": {"loaded": True}}
            return {"result": {"source": "xhs", "data": {"rows": []}}}

    bridge = FakeBridge()
    result = host._run_v2_request(
        bridge,
        {"v": 2, "op": "collect", "connector": "xhs.creator.v2", "input": {"maxScrolls": 12}, "timeoutMs": 180_000},
    )

    assert result["data"] == {"rows": []}
    assert [request["cmd"] for request in bridge.requests] == ["load_user_script_bundle", "collect_v2"]
    bundle = bridge.requests[0]["bundle"]
    assert bundle["id"] == "xhs.creator.v2"
    assert bundle.get("source")
    assert bridge.requests[1] == {
        "cmd": "collect_v2",
        "request": {"connector": "xhs.creator.v2", "input": {"maxScrolls": 12}, "timeoutMs": 180_000},
    }


def test_extension_runtime_policy_allows_app_hash_updates_without_reload():
    background = (bridge_install.extension_dir() / "background.js").read_text()
    catalog_data = json.loads((Path(catalog._catalog_root()) / "catalog.json").read_text())
    digest = catalog_data["connectors"][0]["sha256"]

    assert digest not in background
    assert "rawBundle.sha256" in background
    assert "policy.maxSourceBytes" in background
    assert "chrome.userScripts.getScripts()" in background
    assert "user_scripts_disabled" in background
    assert "unregister({ ids: [`${USER_SCRIPT_BUNDLE_PREFIX}${bundle.id}`] })" in background
    assert "async function cleanupUserScriptOrphans()" in background
    assert "Object.keys(USER_SCRIPT_POLICIES).map" in background
    assert "let startupPromise = null" in background
    assert "if (!startupPromise)" in background
    assert "startupPromise = cleanupUserScriptOrphans().finally(() => sweepWindows().finally(connectBridge))" in background
    assert "loadedUserScriptBundles.delete(XHS_CREATOR_V2_ID)" in background


def test_old_socket_server_close_does_not_unlink_replacement_host_socket():
    path = Path("/tmp") / f"pdb-bridge-{uuid.uuid4().hex[:12]}" / "bridge.sock"
    old = host.UnixRequestServer(path, host.NativeBridge(host.DEFAULT_TIMEOUT_MS))
    old.bind()
    try:
        # A new native host follows the normal stale-socket bind path while
        # the old process still has its AF_UNIX descriptor open.
        path.unlink()
        replacement = host.UnixRequestServer(path, host.NativeBridge(host.DEFAULT_TIMEOUT_MS))
        replacement.bind()
        try:
            replacement_identity = (path.stat().st_dev, path.stat().st_ino)

            old.close()

            assert path.exists()
            assert (path.stat().st_dev, path.stat().st_ino) == replacement_identity
        finally:
            replacement.close()
    finally:
        # ``old.close`` is safe to repeat and should have no effect on a
        # replacement path or an already-removed pathname.
        old.close()
        path.parent.rmdir()


def test_native_host_install_is_root_scoped_and_owner_only(tmp_path, monkeypatch):
    chrome_hosts = tmp_path / "Chrome" / "NativeMessagingHosts"
    monkeypatch.setattr(bridge_install, "_chrome_host_dir", lambda: chrome_hosts)
    root = tmp_path / "Personal DB's data"

    result = bridge_install.install_native_host(root)

    launcher = Path(result["launcher"])
    host_manifest = Path(result["host_manifest"])
    socket_path = root / "state" / "browser-collector.sock"
    assert f"PDB_BROWSER_BRIDGE_SOCK={shlex.quote(str(socket_path))}" in launcher.read_text()
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700
    assert stat.S_IMODE(host_manifest.stat().st_mode) == 0o600
    manifest = json.loads(host_manifest.read_text())
    assert manifest["name"] == bridge_install.HOST_NAME
    assert manifest["path"] == str(launcher)
    assert manifest["allowed_origins"] == [
        f"chrome-extension://{bridge_install.extension_id()}/"
    ]
