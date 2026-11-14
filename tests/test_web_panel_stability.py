from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_telegram_sdk_is_not_a_blocking_deferred_dependency():
    html = (ROOT / "pro_web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "pro_web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'src="https://telegram.org/js/telegram-web-app.js"' not in html
    assert "script.async=true" in app
    assert "timeoutMs=1800" in app
    assert "panelAuthenticated=true" in app


def test_web_service_uses_one_process_while_workers_are_embedded():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--workers 1 --access-log" in installer
    assert "--workers 2 --access-log" not in installer


def test_installer_checks_liveness_and_readiness():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    wait = installer.split("wait_for_web_health() {", 1)[1].split("install_web() {", 1)[0]
    assert "/healthz" in wait
    assert "/readyz" in wait
    assert 'wait_for_web_health "$port" 45' in installer


def test_resource_server_selector_uses_large_pages_without_silent_60_item_cap():
    portal = (ROOT / "pro_portal.py").read_text(encoding="utf-8")
    app = (ROOT / "pro_web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "per_page:int=Query(default=12,ge=1,le=50)" in portal
    assert "request('/api/server-options')" in app
    assert "page<=100" not in app
    assert "SELECT id,name FROM servers WHERE owner_id=%s" in portal


def test_proxy_example_includes_websocket_and_diagnostics_routes():
    proxy = (ROOT / "WEB_PROXY_EXAMPLE.conf").read_text(encoding="utf-8")
    assert "location ^~ /api/terminal/" in proxy
    assert "proxy_set_header Upgrade $http_upgrade;" in proxy
    assert 'proxy_set_header Connection "upgrade";' in proxy
    assert "location = /api/diagnostics/bundle" in proxy
    assert "proxy_read_timeout 3600s;" in proxy
    assert "proxy_read_timeout 900s;" in proxy


def test_every_local_static_asset_referenced_by_the_panel_exists():
    import re

    html_files = (ROOT / "pro_web" / "index.html", ROOT / "pro_web" / "guide.html")
    references = []
    for html_file in html_files:
        html = html_file.read_text(encoding="utf-8")
        references.extend(re.findall(r'(?:src|href)=["\'](/static/[^"\']+)["\']', html))
    assert references, "Expected the panel to reference its local static assets"
    for reference in references:
        asset = ROOT / "pro_web" / "static" / reference.removeprefix("/static/").split("?",1)[0]
        assert asset.is_file(), f"Missing local panel asset: {reference}"

    assert "window.SonarNext={loadFleet,loadSensors}" in (
        ROOT / "pro_web" / "static" / "next.js"
    ).read_text(encoding="utf-8")
    assert "window.SonarBalancer = {load}" in (
        ROOT / "pro_web" / "static" / "balancer.js"
    ).read_text(encoding="utf-8")


def test_visible_panel_version_matches_repository_version():
    import re

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    html = (ROOT / "pro_web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r'<span class="version">v([^<]+)</span>', html)
    assert match, "Panel version badge is missing"
    assert match.group(1) == version


def test_readme_brand_assets_are_present_and_match_runtime_assets():
    import re
    from hashlib import sha256

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    references = re.findall(r'<img[^>]+src="([^"]+)"', readme)
    assert references
    for reference in references:
        asset = ROOT / reference
        runtime_asset = ROOT / "pro_web" / "static" / asset.name
        assert asset.is_file(), f"Missing README brand asset: {reference}"
        assert runtime_asset.is_file(), f"Missing runtime brand asset: {asset.name}"
        assert sha256(asset.read_bytes()).digest() == sha256(runtime_asset.read_bytes()).digest()
