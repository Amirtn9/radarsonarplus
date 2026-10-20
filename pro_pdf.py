"""Owner-scoped Persian RTL A4 PDF report using WeasyPrint/Pango shaping.

No SSH credentials, config URLs or unmeasured metric values. Optionally reads
Vazirmatn installed separately in private server state (not bundled).
"""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import os
import logging
from pro_store import ProStore

log=logging.getLogger(__name__)


def _cell(value,unit=''):
    if value is None or value=='':return '—'
    return escape(str(value))+(escape(unit) if unit else '')


def render_html(summary,rankings,incidents,owner,servers=None,font_css=''):
    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    rows=''.join(f'<tr><td>{i}</td><td>{escape(str(r["name"])[:64])}</td><td>{_cell(r.get("score"))}</td><td>{_cell(r.get("ping"))}</td></tr>' for i,r in enumerate(rankings,1))
    alerts=''.join(f'<tr><td>{_cell(r.get("severity"))}</td><td>{_cell(r.get("title"))}</td><td>{_cell(r.get("status"))}</td></tr>' for r in incidents)
    server_rows=[]
    for i,r in enumerate(servers or [],1):
        stamp_value=r.get('last_checked_at')
        if isinstance(stamp_value,datetime):stamp_value=stamp_value.strftime('%Y-%m-%d %H:%M')
        server_rows.append('<tr>' + ''.join(f'<td>{x}</td>' for x in (
            str(i),_cell(r.get('name')),_cell(r.get('last_status')),
            _cell(r.get('last_cpu'),'%'),_cell(r.get('last_ram'),'%'),
            _cell(r.get('last_disk'),'%'),_cell(stamp_value),
            _cell(str(r.get('last_error') or '')[:120])))+'</tr>')
    return f'''<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>
    {font_css}
    @page{{size:A4;margin:14mm}}
    html,body{{direction:rtl}}
    body{{font-family:"Vazirmatn","DejaVu Sans",sans-serif;color:#182334;font-size:10px;line-height:1.85}}
    h1{{font-size:22px;margin-bottom:2px;font-weight:800}}h2{{font-size:15px;margin:22px 0 9px;border-bottom:2px solid #d5e4f5;padding-bottom:6px}}
    .sub{{color:#58708a}}.cards{{display:flex;gap:8px;margin:15px 0}}
    .card{{border:1px solid #cbdbee;background:#f3f8fc;border-radius:8px;padding:10px;flex:1}}
    .card strong{{display:block;font-size:19px;color:#214e78;direction:ltr;unicode-bidi:isolate}}
    table{{width:100%;border-collapse:collapse;table-layout:fixed;font-size:9px}}
    th,td{{border-bottom:1px solid #d8e3ed;padding:6px 5px;text-align:right;overflow-wrap:anywhere;vertical-align:top}}
    th{{background:#e7f0fb}}tr{{break-inside:avoid}}footer{{margin-top:24px;font-size:9px;color:#728397}}
    .technical{{direction:ltr;unicode-bidi:isolate;display:inline-block}}b{{font-weight:800}}
    </style></head><body><h1>SONAR RADAR | گزارش سلامت و مانیتورینگ</h1>
    <div class="sub">فضای کاری: {int(owner)} | زمان تولید (UTC): {escape(stamp)}</div>
    <div class="cards"><div class="card">سرور فعال<strong>{summary['servers']['active']} / {summary['servers']['total']}</strong></div>
    <div class="card">کانفیگ سالم<strong>{summary['configs']['healthy']} / {summary['configs']['total']}</strong></div>
    <div class="card">رخداد باز<strong>{summary['incidents']}</strong></div></div>
    <h2>وضعیت سرورها</h2><table><thead><tr><th>ردیف</th><th>سرور</th><th>وضعیت</th><th>CPU</th><th>RAM</th><th>Disk</th><th>آخرین بررسی</th><th>خطای ثبت‌شده</th></tr></thead><tbody>{''.join(server_rows) or '<tr><td colspan="8">داده‌ای وجود ندارد</td></tr>'}</tbody></table>
    <h2>رتبه‌بندی کانفیگ‌ها</h2><table><thead><tr><th>ردیف</th><th>عنوان</th><th>امتیاز /۱۰۰</th><th>پینگ ms</th></tr></thead><tbody>{rows or '<tr><td colspan="4">داده‌ای وجود ندارد</td></tr>'}</tbody></table>
    <h2>رخدادهای حل‌نشده</h2><table><thead><tr><th>اهمیت</th><th>رخداد</th><th>وضعیت</th></tr></thead><tbody>{alerts or '<tr><td colspan="3">رخداد حل‌نشده‌ای وجود ندارد</td></tr>'}</tbody></table>
    <footer>داده‌ها مطابق آخرین نمونه‌های موجود در پایگاه داده هستند. پینگ سرور، Swap یا ترافیک بدون نمونه واقعی نمایش داده نمی‌شود. اطلاعات قدیمی لزوماً وضعیت فعلی سرور را نشان نمی‌دهد.</footer></body></html>'''


def server_health(store,actor,owner):
    """Workspace authorization is enforced before any report SQL."""
    owner=store.workspace(actor,owner)
    sql='''SELECT name,last_status,last_cpu,last_ram,last_disk,last_checked_at,last_error
           FROM servers WHERE owner_id=%s ORDER BY id LIMIT 200'''
    return [dict(x) for x in store.db.execute(sql,(owner,),fetch='all')]


def generate_pdf(actor,owner=None):
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration
    store=ProStore();owner=store.workspace(actor,owner)
    summary=store.summary(actor,owner)
    rankings=store.rankings(actor,owner,20)
    incidents=store.incidents(actor,owner,'unresolved',20)
    servers=server_health(store,actor,owner)
    font=Path(os.getenv('SONAR_STATE_DIR','/var/lib/sonar-radar'))/'fonts'/'Vazirmatn-Regular.ttf'
    font_css=''
    if font.is_file() and not font.is_symlink():
        font_css=f'@font-face{{font-family:"Vazirmatn";src:url("{font.resolve().as_uri()}") format("truetype");font-weight:100 900;font-style:normal}}'
    else:
        log.warning('Vazirmatn font missing; PDF will use an installed RTL fallback')
    page=render_html(summary,rankings,incidents,owner,servers,font_css)
    return HTML(string=page).write_pdf(font_config=FontConfiguration())
