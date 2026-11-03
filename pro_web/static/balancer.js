'use strict';
// Global multi-region relay controls. Every mutation remains behind the admin API.
(() => {
  const byId = id => document.getElementById(id);
  const node = (tag, text, cls) => {
    const item = document.createElement(tag);
    if (text !== undefined && text !== null) item.textContent = String(text);
    if (cls) item.className = cls;
    return item;
  };
  const locationName = value => value === 'ir' ? 'ایران' : value === 'ext' ? 'خارج' : 'نامشخص';
  let current = [];

  async function load() {
    const order = byId('relay-fallback-order');
    const candidates = byId('relay-candidates');
    const status = byId('relay-status');
    if (!order || !candidates) return;
    order.replaceChildren(node('p', 'در حال دریافت مسیرهای سالم...', 'muted'));
    candidates.replaceChildren(node('p', 'در حال دریافت وضعیت Agentها...', 'muted'));
    try {
      const result = await request('/api/fleet/relays');
      current = result.nodes || [];
      byId('relay-strategy').value = result.policy || 'balanced';
      order.replaceChildren();
      const pool = result.fallback_order || [];
      if (!pool.length) {
        order.append(node('p', 'فعلاً Agent پشتیبان واجد شرایط وجود ندارد. Agent باید TLS-enrolled باشد و heartbeat موفق پنج دقیقه اخیر داشته باشد.', 'muted'));
      }
      pool.forEach((item, index) => {
        const card = node('article', null, 'relay-order-card');
        card.append(node('span', String(index + 2).padStart(2, '0'), 'relay-order-number'));
        const body = node('div', null, 'relay-order-body');
        body.append(node('strong', item.name || `Agent #${item.id}`));
        body.append(node('small', `${locationName(item.location_type)} · اولویت ${item.priority} · تأخیر ${item.latency_ms == null ? '—' : `${Math.round(item.latency_ms)} ms`} · CPU ${item.cpu == null ? '—' : `${Math.round(item.cpu)}٪`}`));
        card.append(body, node('span', 'فعال', 'relay-good'));
        order.append(card);
      });

      candidates.replaceChildren();
      if (!current.length) candidates.append(node('p', 'سرور فعالی برای مدیریت Agent پیدا نشد.', 'muted'));
      current.forEach(item => {
        const card = node('article', null, 'relay-candidate');
        const header = node('div', null, 'relay-candidate-head');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.className = 'relay-check'; checkbox.value = String(item.id);
        checkbox.checked = !!(item.enabled && item.approved);
        const canDisable = !!(item.enabled && item.approved);
        checkbox.disabled = (!(item.tls_enrolled && item.token_configured) && !canDisable) || (!(item.owned_by_admin || item.owner_opted_in) && !canDisable);
        checkbox.setAttribute('aria-label', `انتخاب Agent ${item.name || item.id}`);
        header.append(checkbox, node('strong', item.name || `سرور #${item.id}`));
        const region = node('span', locationName(item.location_type), 'relay-region');
        header.append(region); card.append(header);
        card.append(node('p', `#${item.id} · ${item.ip}:${item.port} · Agent port ${item.ws_port || '—'}`, 'muted'));
        const tlsText = item.tls_enrolled && item.token_configured ? 'TLS و توکن ثبت‌شده' : 'TLS/توکن ثبت نشده؛ نصب یا تعمیر Agent لازم است';
        const consentText = item.owned_by_admin ? 'متعلق به مدیر اصلی' : item.owner_opted_in ? 'مالک برای اشتراک Agent رضایت داده' : 'نیازمند رضایت صریح مالک؛ فعلاً قابل تأیید نیست';
        const readyText = item.relay_ready && item.last_probe_ok ? 'سالم و آماده مسیر' : 'هنوز آماده مسیر نیست';
        card.append(node('p', `${tlsText} · ${readyText}`, item.relay_ready && item.last_probe_ok ? 'relay-good-text' : 'relay-warn-text'));
        card.append(node('p', consentText, item.owned_by_admin || item.owner_opted_in ? 'muted' : 'relay-warn-text'));
        card.append(node('small', `تأخیر: ${item.latency_ms == null ? '—' : `${Math.round(item.latency_ms)} ms`} · CPU: ${item.cpu_pct == null ? '—' : `${Math.round(item.cpu_pct)}٪`} · اولویت: ${item.route_priority ?? 100}`));
        if (item.enabled && item.approved) {
          const priority = node('button', 'تنظیم اولویت');
          priority.type = 'button';
          priority.onclick = async () => {
            const raw = prompt(`اولویت مسیر برای ${item.name || item.id} (۰ تا ۱۰۰۰؛ عدد کمتر یعنی اولویت بالاتر):`, String(item.route_priority ?? 100));
            if (raw === null) return;
            if (!/^\d{1,4}$/.test(raw.trim()) || Number(raw) > 1000) { toast('اولویت باید عددی بین ۰ تا ۱۰۰۰ باشد.'); return; }
            try { await jsonPost(`/api/fleet/relays/${item.id}/priority`, {priority: Number(raw)}); toast('اولویت ذخیره شد.'); await load(); }
            catch (error) { toast(error.message); }
          };
          card.append(priority);
        }
        candidates.append(card);
      });
      if (status) status.textContent = `${current.length} سرور فعال بررسی شد؛ ${pool.length} Agent اکنون برای مسیر جایگزین واجد شرایط است.`;
    } catch (error) {
      order.replaceChildren(node('p', error.message, 'bad'));
      candidates.replaceChildren(node('p', error.message, 'bad'));
      if (status) status.textContent = 'دریافت اطلاعات بالانسر ناموفق بود.';
    }
  }

  byId('relay-pool-refresh')?.addEventListener('click', load);
  byId('relay-policy-save')?.addEventListener('click', async () => {
    try {
      await jsonPost('/api/fleet/policy', {strategy: byId('relay-strategy').value});
      toast('سیاست بالانسر ذخیره شد.'); await load();
    } catch (error) { toast(error.message); }
  });
  byId('relay-approve-selected')?.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('#relay-candidates .relay-check:checked')).map(input => Number(input.value));
    if (!ids.length) { toast('ابتدا Agentهای موردنظر را انتخاب کنید.'); return; }
    if (!confirm(`تأیید و فعال‌سازی ${ids.length} Agent انتخاب‌شده برای مسیرهای پشتیبان؟`)) return;
    try {
      await jsonPost('/api/fleet/approve', {server_ids: ids, enabled: true});
      toast('تأیید ثبت شد؛ Agentها پس از heartbeat موفق وارد استخر می‌شوند.'); await load();
    } catch (error) { toast(error.message); }
  });
  byId('relay-disable-selected')?.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('#relay-candidates .relay-check:checked')).map(input => Number(input.value));
    if (!ids.length) { toast('ابتدا Agentهای موردنظر را انتخاب کنید.'); return; }
    if (!confirm(`مسیر جایگزین ${ids.length} Agent انتخاب‌شده غیرفعال شود؟ نصب Agent حذف نمی‌شود.`)) return;
    try {
      await jsonPost('/api/fleet/approve', {server_ids: ids, enabled: false});
      toast('Agentهای انتخاب‌شده از استخر مسیرها خارج شدند.'); await load();
    } catch (error) { toast(error.message); }
  });
  window.SonarBalancer = {load};
})();
