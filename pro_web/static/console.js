'use strict';
// v6.5.1: Browser presentation only. No SSH / Agent / WSS / routing changes.
(() => {
  const active = {kind:null, id:null, sequence:0, working:false};
  const byId = name => document.getElementById(name);
  const node = (tag, content, klass='') => {
    const element=document.createElement(tag);
    if(content!==undefined && content!==null) element.textContent=String(content);
    if(klass)element.className=klass;
    return element;
  };
  function empty(host, message) {host.replaceChildren(node('p',message,'muted'));}
  function localTime(v) {if(!v)return 'هنوز ثبت نشده';const d=new Date(v);return Number.isNaN(d.valueOf())?String(v).slice(0,36):d.toLocaleString('fa-IR');}
  function metric(v,unit='') {if(v===null||v===undefined||v==='')return '—'; const n=Number(v);return Number.isFinite(n)?`${n.toLocaleString('fa-IR',{maximumFractionDigits:1})}${unit}`:'—';}
  function pairs(items) {
    const container=node('div',null,'console-details-grid');
    for(const [key,value] of items){const cell=node('div',null,'console-detail-cell');cell.append(node('span',key,'muted'),node('strong',value===null||value===undefined||value===''?'—':value));container.append(cell);}
    return container;
  }
  function setHeading(container,emoji,name,sub) {
    const title=node('div',null,'console-record-title');title.append(node('span',emoji,'console-record-emoji'));
    const lines=node('div');lines.append(node('h2',name||'بدون نام'));lines.append(node('p',sub,'muted'));title.append(lines);container.append(title);
  }
  function isCurrent(kind,id,serial){return active.kind===kind && active.id===id && active.sequence===serial;}
  function openDetails(kind,id){
    if(!['server','config'].includes(kind) || !Number.isSafeInteger(Number(id))||Number(id)<=0)return;
    active.kind=kind;active.id=Number(id);active.sequence++;
    tabClick(kind==='server'?'server-detail':'config-detail');
  }
  async function loadServer(){
    if(active.kind!=='server')return;
    const id=active.id, serial=++active.sequence,host=byId('server-details');
    empty(host,'در حال دریافت جزئیات سرور…');byId('detail-chart-status').textContent='در حال بارگذاری نمودار…';
    try{
      const info=await request(`/api/console/servers/${id}`);
      if(!isCurrent('server',id,serial))return;
      host.replaceChildren();setHeading(host,'🖥',info.name,`شناسه سرور: ${id} · ${info.is_active?'فعال':'غیرفعال'}`);
      host.append(pairs([
        ['آدرس سرور',info.ip],['پورت SSH',metric(info.port)],['پورت Agent',metric(info.ws_port)],
        ['وضعیت سرور',info.last_status],['وضعیت Agent',info.last_agent_status],
        ['CPU',metric(info.last_cpu,'٪')],['RAM',metric(info.last_ram,'٪')],['Disk',metric(info.last_disk,'٪')],
        ['آخرین نمونه',localTime(info.last_checked_at)],['آخرین اتصال Agent',info.agent_last_seen||'ثبت نشده'],
        ['محل سرور',info.location_type||'—'],['گروه',info.group_id??'بدون گروه']
      ]));
      const note=node('p','نمایش داده‌های ذخیره‌شده؛ هیچ دستور جدیدی روی سرور اجرا نمی‌شود.','info-strip');host.append(note);
      const chartData=await request(`/api/server-resources/${id}`);
      if(!isCurrent('server',id,serial))return;
      drawChart(chartData.samples||[]);
    }catch(err){if(isCurrent('server',id,serial)){empty(host,err.message);byId('detail-chart-status').textContent='نمایش نمودار ممکن نیست.';}}
  }
  function drawChart(values){
    const cvs=byId('detail-chart'),ctx=cvs?.getContext?.('2d');if(!ctx)return;
    const w=cvs.width,h=cvs.height;
    ctx.clearRect(0,0,w,h);ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--surface2')||'#172434';ctx.fillRect(0,0,w,h);
    const x0=48,x1=w-20,y0=20,y1=h-34;
    for(let k=0;k<=4;k++){const y=y0+(y1-y0)*k/4;ctx.strokeStyle='#52667d55';ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();ctx.fillStyle='#8599b2';ctx.font='12px system-ui';ctx.fillText(String(100-25*k)+'%',4,y+4);}
    for(const [key,color] of [['cpu','#0a84ff'],['ram','#a78bfa']]) {
      ctx.beginPath();ctx.lineWidth=2.7;ctx.strokeStyle=color;let continuous=false;
      values.forEach((p,i)=>{const v=Number(p[key]);if(p[key]==null||!Number.isFinite(v)){continuous=false;return;}const x=x0+(x1-x0)*i/Math.max(1,values.length-1);const y=y1-(y1-y0)*Math.max(0,Math.min(100,v))/100;if(!continuous)ctx.moveTo(x,y);else ctx.lineTo(x,y);continuous=true;});ctx.stroke();
    }
    byId('detail-chart-status').textContent=values.length?`📈 ${values.length.toLocaleString('fa-IR')} نمونه · آبی CPU / بنفش RAM · حداکثر ۲۴ ساعت`:'داده‌ای ثبت نشده؛ اتصال Agent و مانیتورینگ را بررسی کنید.';
  }
  async function loadConfig(){
    if(active.kind!=='config')return;
    const id=active.id,serial=++active.sequence,host=byId('config-details');empty(host,'در حال دریافت اطلاعات کانفیگ…');
    try{
      const info=await request(`/api/console/configs/${id}`);if(!isCurrent('config',id,serial))return;
      host.replaceChildren();setHeading(host,'🔗',info.name,`شناسه کانفیگ: ${id} · ${info.type}`);
      host.append(pairs([['وضعیت تست',info.last_status],['پینگ',metric(info.last_ping,' ms')],['جیتر',metric(info.last_jitter,' ms')],['امتیاز',metric(info.quality_score,'/100')],['آپلود',info.last_speed_up],['دانلود',info.last_speed_down],['تاریخ اضافه‌شدن',info.added_at||'نامشخص']]));
      host.append(node('p','لینک و مشخصات محرمانه کانفیگ در وب نمایش داده نمی‌شود. تنظیمات و تست عملیاتی از داخل ربات تلگرام قابل انجام است.','info-strip'));
    }catch(err){if(isCurrent('config',id,serial))empty(host,err.message);}
  }
  async function loadChannels(){
    const host=byId('channels-list');if(!host)return;empty(host,'در حال دریافت کانال‌ها…');
    try{
      const items=await request('/api/console/channels');if(state.tab!=='channels')return;
      if(!Array.isArray(items))throw Error('پاسخ نامعتبر از سرویس کانال‌ها');
      if(!items.length){empty(host,'هنوز کانال یا گروه اطلاع‌رسانی ثبت نشده است.');return;}
      host.replaceChildren();items.forEach(entry=>{const item=node('article',null,'item');const description=node('div');description.append(node('strong','📢 '+entry.name),node('small',`نوع: ${entry.usage_type} · مقصد: ${entry.chat_hint} · تاپیک: ${entry.topic_id||'عمومی'}`));item.append(description);host.append(item);});
    }catch(err){if(state.tab==='channels')empty(host,err.message);}
  }
  async function loadRecent(){
    const host=byId('recent-activity');if(!host)return;
    try{
      const items=await request('/api/console/activity?limit=6');if(state.tab!=='overview')return;
      if(!Array.isArray(items)||!items.length){empty(host,'هنوز رویدادی ثبت نشده است.');return;}
      host.replaceChildren();items.forEach(x=>{const wrap=node('div',null,'console-timeline-item');wrap.append(node('span','●','console-timeline-point'));const d=node('div');d.append(node('strong',x.event),node('small',`کاربر ${x.actor_id} · ${localTime(x.time)}`));wrap.append(d);host.append(wrap);});
    }catch(err){if(state.tab==='overview')empty(host,err.message);}
  }
  // Poll reads only, respecting hidden tabs and existing short-lived signed Telegram sessions.
  // This is not an Agent WebSocket; data remains last-known until underlying collectors update.
  let refreshing=false;
  async function refreshVisible(){
    if(document.hidden||refreshing||!window.Telegram?.WebApp?.initData||!byId('session-warning')?.hidden)return;
    refreshing=true;
    try{
      if(state.tab==='overview'){await summary();await loadRecent();}
      else if(state.tab==='servers'||state.tab==='configs'||state.tab==='incidents')await loadList();
      else if(state.tab==='services')await loadServices();
      else if(state.tab==='server-detail')await loadServer();
      else if(state.tab==='config-detail')await loadConfig();
      else if(state.tab==='channels')await loadChannels();
    }catch(err){if(err.message)toast(err.message);}finally{refreshing=false;}
  }
  byId('channels-refresh')?.addEventListener('click',loadChannels);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshVisible();});
  window.setInterval(refreshVisible,45000);
  window.SonarConsole={openDetails,loadServer,loadConfig,loadChannels,loadRecent};
})();
