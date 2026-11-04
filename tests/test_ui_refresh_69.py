"""Regression checks for the unified web panel and Telegram Mini App refresh."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_web_and_telegram_miniapp_share_one_shell_and_design_system():
    index = (ROOT / 'pro_web/index.html').read_text(encoding='utf8')
    app = (ROOT / 'pro_web/static/app.js').read_text(encoding='utf8')
    portal = (ROOT / 'pro_portal.py').read_text(encoding='utf8')
    assert 'href="/static/premium.css"' in index
    assert "document.documentElement.dataset.client='telegram'" in app
    assert "document.documentElement.dataset.client='web'" in app
    assert "@app.get('/')" in portal and "BASE/'pro_web'/'index.html'" in portal
    assert "@app.post('/api/web/miniapp-session')" in portal
    assert 'Telegram WebView' in (ROOT / 'pro_web/static/premium.css').read_text(encoding='utf8')


def test_dashboard_notification_delivery_uses_existing_owner_scoped_api():
    index = (ROOT / 'pro_web/index.html').read_text(encoding='utf8')
    app = (ROOT / 'pro_web/static/app.js').read_text(encoding='utf8')
    portal = (ROOT / 'pro_portal.py').read_text(encoding='utf8')
    for element_id in ('delivery-pending', 'delivery-delivered', 'delivery-exhausted', 'delivery-skipped'):
        assert f'id="{element_id}"' in index
    assert "request('/api/notifications/delivery')" in app
    assert "@app.get('/api/notifications/delivery')" in portal
    assert 'pro_notification_outbox' in portal


def test_favorites_filter_is_sent_to_the_existing_resource_api():
    app = (ROOT / 'pro_web/static/app.js').read_text(encoding='utf8')
    portal = (ROOT / 'pro_portal.py').read_text(encoding='utf8')
    store = (ROOT / 'pro_store.py').read_text(encoding='utf8')
    assert 'favorites=${state.onlyFavorites?' in app
    assert 'favorites:bool=False' in portal
    assert 'if favorites:' in store


def test_incident_lifecycle_filter_uses_existing_status_contract():
    index = (ROOT / 'pro_web/index.html').read_text(encoding='utf8')
    app = (ROOT / 'pro_web/static/app.js').read_text(encoding='utf8')
    store = (ROOT / 'pro_store.py').read_text(encoding='utf8')
    assert 'id="incident-status-filter"' in index
    for status in ('unresolved', 'open', 'acknowledged', 'resolved', 'all'):
        assert f'value="{status}"' in index
    assert 'incident-status-filter' in app and 'status=${encodeURIComponent(state.incidentStatus)}' in app
    assert "elif status!='all'" in store


def test_premium_styles_respect_mobile_safe_areas_and_accessibility():
    css = (ROOT / 'pro_web/static/premium.css').read_text(encoding='utf8')
    assert 'env(safe-area-inset-bottom)' in css
    assert '@media (prefers-reduced-motion: reduce)' in css
    assert 'backdrop-filter' in css
    assert ':root[data-theme="light"]' in css


def test_refreshed_frontend_keeps_safe_dom_rendering():
    for filename in ('app.js', 'next.js', 'console.js', 'web_ops.js', 'enterprise.js'):
        source = (ROOT / 'pro_web/static' / filename).read_text(encoding='utf8')
        assert '.innerHTML' not in source
    index = (ROOT / 'pro_web/index.html').read_text(encoding='utf8')
    ids = re.findall(r'\bid=["\']([^"\']+)', index)
    assert len(ids) == len(set(ids)), 'HTML element IDs must remain unique'
