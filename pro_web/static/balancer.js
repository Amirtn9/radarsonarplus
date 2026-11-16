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
  let current = [],loadSequence=0;

  async function load() {
    const sequence=++loadSequence;
    const order = byId('relay-fallback-order');
    const candidates = byId('relay-candidates');
    const status = byId('relay-status');
    if (!order || !candidates) return;
    order.replaceChildren(node('p', 'در حال دریافت مسیرهای سالم...', 'muted'));
    candidates.replaceChildren(node('p', 'در حال دریافت وضعیت Agentها...', 'muted'));
    try {
      const result = await request('/api/fleet/relays');
      if(sequence!==loadSequence)return;
      current = (result.nodes || []).filter(item=>item&&Number.isSafeInteger(Number(item.id)));
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
        card.dataset.probeAt=item.last_probe_at||'';
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
        const actions=window.SonarAgentCards.attach(card,item);
        const consentText=item.owned_by_admin?'متعلق به مدیر اصلی':item.owner_opted_in?'مالک برای اشتراک Agent رضایت داده':'نیازمند رضایت صریح مالک؛ فعلاً قابل تأیید نیست';
        card.append(node('p',consentText,'muted'));
        card.append(node('small',`اولویت: ${item.route_priority ?? 100} · ${item.enabled&&item.approved?'تأییدشده و فعال':'فعال نشده'} · ${item.relay_ready?'آماده‌سازی مسیر ثبت شده':'مسیر آماده نشده'}`));
        if (item.enabled && item.approved) {
          const priority = node('button', 'تنظیم اولویت');
          priority.type = 'button';
          priority.onclick = () => priorityDialog(item);
          actions.append(priority);
        }
        candidates.append(card);
      });
      if (status) status.textContent = `${current.length} سرور فعال بررسی شد؛ ${pool.length} Agent اکنون برای مسیر جایگزین واجد شرایط است.`;
    } catch (error) {
      if(sequence!==loadSequence)return;
      current=[];
      order.replaceChildren(node('p', error.message, 'bad'));
      candidates.replaceChildren(node('p', error.message, 'bad'));
      if (status) status.textContent = 'دریافت اطلاعات بالانسر ناموفق بود.';
    }
  }

  function dialog(title,description){
    const previous=document.activeElement,root=node('dialog',null,'sonar-dialog relay-dialog');
    const heading=node('h2',title);heading.id='relay-dialog-title';root.setAttribute('aria-labelledby',heading.id);
    const form=node('form'),message=node('p','', 'relay-save-status');message.setAttribute('role','status');
    root.append(heading,node('p',description,'muted'),form,message);
    const actions=node('div',null,'dialog-actions'),cancel=node('button','انصراف'),save=node('button','ذخیره','primary');
    cancel.type='button';save.type='submit';actions.append(cancel,save);
    let busy=false;
    function close(){if(busy)return;window.removeEventListener('sonar:view-change',close);root.close();root.remove();if(previous?.isConnected)previous.focus();}
    cancel.onclick=close;root.addEventListener('cancel',event=>{event.preventDefault();close();});
    window.addEventListener('sonar:view-change',close);document.body.append(root);
    return {root,form,actions,message,save,cancel,open(){form.append(actions);root.showModal();cancel.focus();},
      submit(action){form.onsubmit=async event=>{event.preventDefault();if(busy)return;busy=true;save.disabled=cancel.disabled=true;form.inert=true;form.setAttribute('aria-busy','true');message.textContent='در حال ذخیره…';
       try{await action();busy=false;close();toast('تغییرات مسیر ذخیره شد.');await load();if(state.tab==='balancer')byId('relay-pool-refresh')?.focus();}
       catch(error){message.textContent='ذخیره نشد: '+error.message;}
       finally{busy=false;save.disabled=cancel.disabled=false;form.inert=false;form.removeAttribute('aria-busy');}
      };}};
  }
  function priorityDialog(item){
    const ui=dialog('اولویت مسیر '+(item.name||item.id),'عدد کمتر، اولویت بالاتر دارد. مسیر مرکزی همیشه اول است؛ تغییر اولویت، سلامت یا تأیید Agent را تغییر نمی‌دهد.');
    const range=node('input'),number=node('input');range.type='range';number.type='number';
    range.id='relay-priority-range';number.id='relay-priority-number';
    for(const input of [range,number]){input.min='0';input.max='1000';input.step='1';input.value=String(item.route_priority??100);}
    number.required=true;const sliderLabel=node('label','تنظیم با اسلایدر'),numberLabel=node('label','اولویت عددی');
    sliderLabel.htmlFor=range.id;numberLabel.htmlFor=number.id;sliderLabel.append(range);numberLabel.append(number);
    range.oninput=()=>{number.value=range.value;};number.oninput=()=>{if(number.validity.valid&&number.value!=='')range.value=number.value;};
    ui.form.append(sliderLabel,numberLabel);ui.submit(()=>jsonPost(`/api/fleet/relays/${item.id}/priority`,{priority:Number(number.value)}));ui.open();number.focus();
  }
  function orderDialog(){
    const items=current.filter(item=>item.enabled&&item.approved);
    if(items.length<2){toast('حداقل دو مسیر فعال برای مرتب‌سازی لازم است.');return;}
    if(items.length>1001){toast('مرتب‌سازی هم‌زمان حداکثر ۱۰۰۱ مسیر پشتیبانی می‌شود.');return;}
    const ui=dialog('مرتب‌سازی مسیرهای Agent','تغییرات تا انتخاب ذخیره اعمال نمی‌شوند. مسیر مرکزی ثابت است. با ذخیره، اولویت‌ها از صفر به ترتیب شماره‌گذاری می‌شوند. جابه‌جایی با کشیدن یا دکمه‌های بالا و پایین انجام می‌شود؛ مسیر ناسالم با این تغییر واجد شرایط نمی‌شود.');
    ui.form.append(node('p','۱. سرور مرکزی — ثابت','relay-pinned'));
    const list=node('ol',null,'relay-sort-list');let draft=items.map(item=>Number(item.id)),dragged=null;
    const lookup=new Map(items.map(item=>[Number(item.id),item]));
    function move(id,index){const old=draft.indexOf(id);if(old<0||index<0||index>=draft.length)return;draft.splice(old,1);draft.splice(index,0,id);render();ui.message.textContent='ترتیب پیشنهادی؛ هنوز ذخیره نشده است.';}
    function render(){
     list.replaceChildren();draft.forEach((id,index)=>{
      const row=node('li',null,'relay-sort-row'),name=lookup.get(id).name||`Agent #${id}`;row.dataset.serverId=String(id);row.draggable=true;
      row.append(node('strong',`${index+2}. ${name}`));
      const tools=node('div',null,'relay-sort-tools');
      for(const [label,delta]of [['بالا',-1],['پایین',1]]){const button=node('button',label);button.type='button';button.setAttribute('aria-label',`${label}: ${name}`);button.disabled=index+delta<0||index+delta>=draft.length;button.onclick=()=>{move(id,index+delta);list.querySelector(`[data-server-id="${id}"] button:not(:disabled)`)?.focus();};tools.append(button);}
      row.append(tools);row.addEventListener('dragstart',event=>{dragged=id;event.dataTransfer.setData('text/plain',String(id));event.dataTransfer.effectAllowed='move';});
      row.addEventListener('dragover',event=>event.preventDefault());row.addEventListener('drop',event=>{event.preventDefault();if(dragged!==null){move(dragged,index);dragged=null;}});row.addEventListener('dragend',()=>{dragged=null;});list.append(row);
     });
    }
    render();ui.form.append(list);ui.submit(()=>jsonPost('/api/fleet/relays/order',{server_ids:draft,expected_priorities:draft.map(id=>lookup.get(id).route_priority??100)}));ui.open();
  }
  byId('relay-reorder')?.addEventListener('click',orderDialog);
  setInterval(()=>{for(const card of document.querySelectorAll('#relay-fallback-order .relay-order-card')){
    const badge=card.querySelector('.relay-good');
    const fresh=navigator.onLine!==false&&window.SonarAgentCards.fresh(card.dataset.probeAt);
    if(badge)badge.textContent=fresh?'فعال':'نیازمند تازه‌سازی';
    if(!fresh&&byId('relay-status'))byId('relay-status').textContent='وضعیت برخی مسیرها قدیمی یا مرورگر آفلاین است؛ وضعیت را تازه‌سازی کنید.';
  }},15000);

  byId('relay-pool-refresh')?.addEventListener('click', load);
  byId('relay-version-check')?.addEventListener('click', async event => {
    const button=event.currentTarget, output=byId('relay-versions');
    const ids=current.map(item=>Number(item.id));
    if(!ids.length){toast('ابتدا فهرست سرورها را دریافت کنید.');return;}
    button.disabled=true;output.replaceChildren(node('p','در حال بررسی ارتباط و نسخه‌ها…','muted'));
    const labels={current:'✅ مطابق نسخه مرکزی',different:'🟡 نسخه یا بیلد متفاوت',incompatible:'🔴 پروتکل ناسازگار',unknown:'⚪ نامشخص'};
    try {
      output.replaceChildren();
      for(let offset=0;offset<ids.length;offset+=12){
        const report=await jsonPost('/api/fleet/versions',{server_ids:ids.slice(offset,offset+12)});
        window.SonarAgentCards.record(report);
        for(const item of report.nodes){
          const card=node('article',null,'relay-candidate');
          card.append(node('strong',`${item.name} · ${labels[item.state]||labels.unknown}`));
          card.append(node('p',`Agent: ${item.agent_version||'—'} · پروتکل: ${item.agent_protocol??'—'} · بیلد: ${item.agent_build?.slice(0,12)||'—'}`));
          card.append(node('p',`Xray: ${item.xray_version||'—'} (${labels[item.xray_version_state]}) · سازگاری: ${item.xray_compat_version||'—'} (${labels[item.xray_compat_version_state]})`));
          card.append(node('small',`بررسی: ${new Date(item.checked_at).toLocaleString('fa-IR')} · نسخه هدف Agent: ${report.expected_version}`));
          output.append(card);
        }
      }
    }catch(error){output.append(node('p',error.message,'bad'));}
    finally{button.disabled=false;}
  });
  byId('relay-policy-save')?.addEventListener('click', async () => {
    try {
      await jsonPost('/api/fleet/policy', {strategy: byId('relay-strategy').value});
      toast('سیاست بالانسر ذخیره شد.'); await load();
    } catch (error) { toast(error.message); }
  });
  byId('relay-approve-selected')?.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('#relay-candidates .relay-check:checked')).map(input => Number(input.value));
    if (!ids.length) { toast('ابتدا Agentهای موردنظر را انتخاب کنید.'); return; }
    if (!await window.SonarUI.confirm(`تأیید و فعال‌سازی ${ids.length} Agent انتخاب‌شده برای مسیرهای پشتیبان؟`)) return;
    try {
      await jsonPost('/api/fleet/approve', {server_ids: ids, enabled: true});
      toast('تأیید ثبت شد؛ Agentها پس از heartbeat موفق وارد استخر می‌شوند.'); await load();
    } catch (error) { toast(error.message); }
  });
  byId('relay-disable-selected')?.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('#relay-candidates .relay-check:checked')).map(input => Number(input.value));
    if (!ids.length) { toast('ابتدا Agentهای موردنظر را انتخاب کنید.'); return; }
    if (!await window.SonarUI.confirm(`مسیر جایگزین ${ids.length} Agent انتخاب‌شده غیرفعال شود؟ نصب Agent حذف نمی‌شود.`)) return;
    try {
      await jsonPost('/api/fleet/approve', {server_ids: ids, enabled: false});
      toast('Agentهای انتخاب‌شده از استخر مسیرها خارج شدند.'); await load();
    } catch (error) { toast(error.message); }
  });
  window.SonarBalancer = {load};
})();
