'use strict';
// Extra web UX for 6.5.4; API handles all access control and never sends secrets.
(() => {
 const el=id=>document.getElementById(id);
 const safeNode=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=String(text);if(cls)e.className=cls;return e;};
 const authOverlay=el('web-auth');
 el('login-form')?.addEventListener('submit',async event=>{
   event.preventDefault();const code=el('login-code').value.trim();const btn=event.submitter;
   if(btn)btn.disabled=true;el('login-error').textContent='';
   try{
     const response=await fetch('/api/web/login',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
     const result=await response.json();if(!response.ok)throw new Error(result.detail||'ورود ناموفق بود');
     csrfToken=result.csrf;authOverlay.hidden=true;el('login-code').value='';await boot();
   }catch(err){el('login-error').textContent=err.message;}finally{if(btn)btn.disabled=false;}
 });
 async function initSession(){
    if(auth)return;
    try {const r=await request('/api/web/csrf');csrfToken=r.csrf||'';}
    catch(e){/* anonymous sessions see the login form after boot */}
 }
 initSession();
 function closeMenu(){document.body.classList.remove('menu-open');el('nav-scrim').hidden=true;}
 function openMenu(){document.body.classList.add('menu-open');el('nav-scrim').hidden=false;}
 el('menu-trigger')?.addEventListener('click',()=>document.body.classList.contains('menu-open')?closeMenu():openMenu());
 el('nav-scrim')?.addEventListener('click',closeMenu);
 document.querySelectorAll('#side-navigation .navbtn').forEach(b=>b.addEventListener('click',closeMenu));
 document.querySelectorAll('[data-mobile-tab]').forEach(button=>button.addEventListener('click',()=>{
   const tab=button.dataset.mobileTab;
   if(tab==='more'){openMenu();return;}
   tabClick(tab);closeMenu();
 }));
 const existingTabClick=tabClick;
 // Track active view for the five-icon mobile dock, even for nested pages.
 tabClick=function(name){existingTabClick(name);const map={'server-detail':'servers','config-detail':'configs',rankings:'configs',incidents:'services'};
    document.querySelectorAll('[data-mobile-tab]').forEach(b=>b.classList.toggle('active',b.dataset.mobileTab===(map[name]||name)));};
 const preferred=()=>{try{return localStorage.getItem('sonar:theme')}catch{return null}};
 function setTheme(mode){document.documentElement.dataset.theme=mode;el('theme-switch').textContent=mode==='light'?'☾':'☀︎';}
 setTimeout(()=>setTheme(preferred()||document.documentElement.dataset.theme||'light'),10);
 el('theme-switch')?.addEventListener('click',()=>{
   const mode=document.documentElement.dataset.theme==='light'?'dark':'light';setTheme(mode);
   try{localStorage.setItem('sonar:theme',mode)}catch{ /* private mode */ }
 });
 function pct(v){if(v===null||v===undefined||v==='')return null;const x=Number(v);return Number.isFinite(x)&&x>=0&&x<=100?x:null;}
 function dial(metric,value){const host=document.querySelector(`[data-dial="${metric}"]`);if(!host)return;
  const val=pct(value),needle=host.querySelector('.needle');host.querySelector('.dial-number').textContent=val===null?'—':`${Math.round(val)}٪`;
  if(needle)needle.style.transform=`translateX(-50%) rotate(${val===null?-90:(-90+180*val/100)}deg)`;
  host.classList.toggle('unavailable',val===null);
 }
 let lastId=0,lastConfigId=0,loading=false;
 async function loadSensors(){
   if(state.tab!=='server-detail'||!window.SonarConsole)return;
   const id=Number(window.SonarConsole.currentServerId?.()||0);
   if(!id||loading)return;
   loading=true;
   try{
     const x=await request(`/api/console/servers/${id}`);
     if(state.tab!=='server-detail'||Number(window.SonarConsole.currentServerId?.())!==id)return;
     dial('cpu',x.last_cpu);dial('ram',x.last_ram);dial('disk',x.last_disk);dial('swap',null);
     const stamp=x.last_checked_at?new Date(x.last_checked_at):null;
     const age=stamp&&Number.isFinite(stamp.getTime())?Math.max(0,Math.round((Date.now()-stamp.getTime())/1000)):null;
     el('sensor-freshness').textContent=age===null?'هیچ نمونه‌ای ثبت نشده':age>180?`⚠️ نمونه قدیمی: ${Math.round(age/60)} دقیقه پیش`:`آخرین نمونه: ${age} ثانیه پیش`;
     el('sensor-freshness').classList.toggle('stale',age===null||age>180);
   }catch(err){el('sensor-freshness').textContent='Agent یا دیتابیس در دسترس نیست';}
   finally{loading=false;}
 }
 const originalOpen=window.SonarConsole?.openDetails;
 if(originalOpen){window.SonarConsole.openDetails=(kind,id)=>{
   originalOpen(kind,id);if(kind==='server'){lastId=id;loadSensors();window.SonarWebAgent?.watchInstall?.(id);}if(kind==='config'){lastConfigId=id;}
 };window.SonarConsole.currentServerId=()=>lastId;window.SonarConsole.currentConfigId=()=>lastConfigId;}
 setInterval(loadSensors,15000);
 const fleetList=el('fleet-list');let fleetData=[];let fleetAdmin=false;
 async function loadFleet(){
   if(!fleetList)return;
   fleetList.replaceChildren(safeNode('p','در حال دریافت اطلاعات...','muted'));
   try{
    const [result,me,serverList]=await Promise.all([
      request('/api/fleet/nodes'),request('/api/me'),request('/api/resources/server?page=1')]);
    fleetData=result.nodes||[];fleetAdmin=!!me.admin;
    el('fleet-strategy').value=result.strategy||'balanced';el('fleet-strategy').disabled=!fleetAdmin;
    el('fleet-save').hidden=!fleetAdmin;el('fleet-approve').hidden=!fleetAdmin;
    fleetList.replaceChildren();
    if(!fleetData.length)fleetList.append(safeNode('p','هنوز سروری برای همکاری به استخر Agent پیشنهاد نشده است.','muted'));
    fleetData.forEach(node=>{
      const card=safeNode('article',null,'fleet-node');
      const title=safeNode('div',null,'fleet-node-title');
      const input=document.createElement('input');input.type='checkbox';input.className='fleet-check';input.value=String(node.id);input.checked=!!node.enabled;input.hidden=!fleetAdmin;title.append(input);
      const region=node.location_type==='ir'?'ایران':'خارج';
      title.append(safeNode('strong',`${region} · ${node.name}`));
      const status=node.last_probe_ok===true?'🟢 WSS تأییدشده':(node.last_probe_ok===false?'🔴 ناموفق':'⚪️ بررسی نشده');
      card.append(title,safeNode('p',`شناسه ${node.id} • پورت ${node.port} • ${status}`,'muted'),
                safeNode('p',`RTT: ${node.latency_ms==null?'—':node.latency_ms+' ms'} • CPU: ${node.cpu==null?'—':node.cpu+'٪'}`,'muted'));
      if(fleetAdmin){const disable=safeNode('button','⛔ غیرفعال‌سازی','fleet-disable');disable.onclick=async()=>{if(!confirm('این Agent از استخر خارج شود؟'))return;try{await jsonPost('/api/fleet/approve',{server_ids:[node.id],enabled:false});await loadFleet();}catch(e){toast(e.message);}};card.append(disable);}
      fleetList.append(card);
    });
    const target=el('fleet-target'),selected=target.value;target.replaceChildren();
    const known=new Map();for(const s of Array.isArray(serverList)?serverList:[])known.set(s.id,{id:s.id,name:s.name});
    for(const s of fleetData)known.set(s.id,{id:s.id,name:s.name});
    for(const s of known.values()){const option=safeNode('option',s.name||String(s.id));option.value=String(s.id);target.append(option);}
    if(selected&&known.has(Number(selected)))target.value=selected;
    const choice=await request('/api/fleet/choice');el('fleet-choice').textContent=choice.selected_id?`Agent پیشنهادی فعلی: شماره ${choice.selected_id}`:'فعلاً Agent سالم با آزمون اخیر موجود نیست؛ نصب یا سلامت WSS را بررسی کنید.';
    const offered=new Set(fleetData.map(x=>x.id));
    for(const s of Array.isArray(serverList)?serverList:[]){
      if(offered.has(s.id))continue;
      const region=s.location_type==='ir'?'ایران':'خارج';
      const btn=safeNode('button',`🤝 پیشنهاد سرور ${region} «${s.name}» برای Agent`,`fleet-offer`);
      btn.onclick=async()=>{if(!confirm('با اشتراک منابع فنی این سرور با استخر مانیتورینگ موافقید؟'))return;try{await jsonPost('/api/fleet/offer',{server_id:s.id,enabled:true});await loadFleet();}catch(e){toast(e.message);}};
      fleetList.append(btn);
    }
   }catch(e){fleetList.replaceChildren(safeNode('p',e.message,'bad'));}
 }
 el('fleet-refresh')?.addEventListener('click',loadFleet);
 el('fleet-save')?.addEventListener('click',async()=>{try{await jsonPost('/api/fleet/policy',{strategy:el('fleet-strategy').value});toast('سیاست انتخاب ذخیره شد');await loadFleet();}catch(e){toast(e.message);}});
 el('fleet-approve')?.addEventListener('click',async()=>{
   const ids=Array.from(fleetList.querySelectorAll('.fleet-check:checked')).map(x=>Number(x.value));
   if(!ids.length){toast('ابتدا یک یا چند Agent را تیک بزنید.');return;}
   if(!confirm(`تأیید و فعال‌سازی ${ids.length} Agent انتخاب‌شده؟`))return;
   try{await jsonPost('/api/fleet/approve',{server_ids:ids,enabled:true});toast('انتخاب ثبت شد');await loadFleet();}catch(e){toast(e.message);}
 });
 el('fleet-diagnose')?.addEventListener('click',async()=>{
   const id=Number(el('fleet-target').value),host=el('fleet-results');host.replaceChildren(safeNode('p','در حال بررسی TCP و WSS…','muted'));
   if(!id)return;
   try{const r=await jsonPost('/api/fleet/diagnose',{server_id:id});host.replaceChildren();for(const item of r.ports){host.append(safeNode('article',`پورت ${item.port} · TCP: ${item.tcp} · WSS: ${item.wss||'تأیید نشده'} · RTT: ${item.rtt_ms??'—'}ms`,'fleet-result'));}}
   catch(e){host.replaceChildren(safeNode('p',e.message,'bad'));}
 });
 window.SonarNext={loadFleet,loadSensors};
})();

// The following view binds to the same existing authenticated web API.
// Installer jobs are explicitly confirmed and executed by the original SSH installer.
(() => {
 const byId=id=>document.getElementById(id);
 let admin=false;
 const element=(tag,value,cls)=>{const d=document.createElement(tag);if(value!=null)d.textContent=String(value);if(cls)d.className=cls;return d;};
 async function loadInstall(){
  const host=byId('fleet-installer');if(!host)return;
  try{const me=await request('/api/me');admin=!!me.admin;host.hidden=!admin;if(!admin)return;
   const candidates=await request('/api/fleet/candidates');const list=byId('fleet-candidates');list.replaceChildren();
   if(!candidates.length)list.append(element('p','سرور فعالی برای نصب Agent ثبت نشده است.','muted'));
   for(const s of candidates){const label=element('label',null,'fleet-candidate');const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=String(s.id);
      const region=s.location_type==='ir'?'ایران':'خارج';label.append(checkbox,element('span',`${s.name} · ${region} · ${s.ip} · پورت ${s.ws_port}`));list.append(label);}
   const jobs=await request('/api/fleet/install/jobs');const out=byId('fleet-jobs');out.replaceChildren();
   if(!jobs.length)out.append(element('p','هنوز درخواست نصب جدیدی ثبت نشده است.','muted'));
   for(const j of jobs.slice(0,15))out.append(element('article',`#${j.id} · سرور ${j.server_id} · پورت ${j.requested_port} · ${j.state} / ${j.stage} ${j.error_code||''}`,'fleet-result'));
  }catch(err){byId('fleet-jobs').replaceChildren(element('p',err.message,'bad'));}
 }
 byId('fleet-install-run')?.addEventListener('click',async()=>{
   const ids=Array.from(document.querySelectorAll('#fleet-candidates input:checked')).map(x=>Number(x.value));
   if(!ids.length){toast('یک یا چند سرور را انتخاب کنید');return;}
   const mode=byId('fleet-port-mode').value;
   if(!confirm(`برای ${ids.length} سرور نصب خودکار انجام شود؟ تغییرات روی سرور واقعی اجرا خواهد شد.`))return;
   try{await jsonPost('/api/fleet/install',{server_ids:ids,port_mode:mode,confirmed:true});toast('درخواست نصب در صف ثبت شد');await loadInstall();}catch(err){toast(err.message);}
 });
 const parent=window.SonarNext?.loadFleet;
 if(parent){window.SonarNext.loadFleet=async()=>{await Promise.all([parent(),loadInstall()]);};}
 setInterval(()=>{if(state.tab==='fleet'&&admin)loadInstall();},30000);
})();

// Administrator-only measured WSS/SSH-relay traffic. No raw credentials exposed.
(() => {
 const byId=id=>document.getElementById(id);
 const node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=String(text??'');if(cls)el.className=cls;return el;};
 const gb=value=>(Number(value||0)/1073741824).toFixed(3)+' GB';
 async function loadTraffic(){
   const panel=byId('fleet-traffic-panel');if(!panel)return;
   try {
     const me=await request('/api/me');panel.hidden=!me.admin;if(!me.admin)return;
     const list=byId('fleet-traffic-rows');list.replaceChildren(node('p','در حال دریافت نمونه‌ها…','muted'));
     const data=await request('/api/fleet/traffic');list.replaceChildren();
     if(!(data.rows||[]).length)list.append(node('p','هنوز نمونه مصرف ثبت نشده است.','muted'));
     for(const row of data.rows||[]){
       const card=node('article','', 'fleet-result');
       card.append(node('strong',`${row.name} · مالک ${row.owner_id} · Agent #${row.agent_id}`),
         node('p',`کل از آخرین بازنشانی: ${gb(row.total)} | امروز: ${gb(row.today)} | ۷ روز: ${gb(row.week)} | ۳۰ روز: ${gb(row.month)}`),
         node('small',`آخرین نمونه: ${row.sampled_at||'داده‌ای موجود نیست'}`));
       if(row.sampled_at){const b=node('button','بازنشانی شمارنده');
         b.onclick=async()=>{if(!confirm(`شمارنده Agent #${row.agent_id} ریست شود؟ تاریخچه قبلی حفظ می‌شود.`))return;
           try{await jsonPost(`/api/fleet/traffic/${row.agent_id}/reset`,{confirmed:true});toast('شمارنده بازنشانی شد');await loadTraffic();}
           catch(err){toast(err.message);}};card.append(b);}
       list.append(card);
     }
   }catch(err){byId('fleet-traffic-rows')?.replaceChildren(node('p',err.message,'bad'));}
 }
 byId('fleet-traffic-refresh')?.addEventListener('click',loadTraffic);
 const orig=window.SonarNext?.loadFleet;
 if(orig)window.SonarNext.loadFleet=async()=>{await Promise.all([orig(),loadTraffic()]);};
})();
