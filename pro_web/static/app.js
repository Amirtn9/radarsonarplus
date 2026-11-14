'use strict';
// Only use Telegram-signed initData; never store credentials in URL or browser storage.
let tg=window.Telegram?.WebApp||null;
// The signed launch payload is available even when Telegram's CDN is slow.
// It is still verified by the server; initDataUnsafe is never authentication.
function signedLaunchData(){
 try{return new URLSearchParams(window.location?.hash?.replace(/^#/,'')||'').get('tgWebAppData')||'';}catch{return '';}
}
let auth=tg?.initData||signedLaunchData();
function isMiniApp(){return !!auth||!!signedLaunchData()||!!(tg?.platform&&tg.platform!=='unknown');}
let telegramSdkPromise=null;
let lastInteraction=Date.now();for(const event of ['pointerdown','keydown','touchstart'])window.addEventListener(event,()=>{lastInteraction=Date.now();},{passive:true});
let activeWorkspace='';try{activeWorkspace=sessionStorage.getItem('sonar-workspace')||'';}catch{}
let panelBooting=false;
let panelBootComplete=false;
let panelAuthenticated=false;
let lateTelegramReady=false;

function loadTelegramWebApp(timeoutMs=1800){
 if(window.Telegram?.WebApp){tg=window.Telegram.WebApp;auth=String(tg.initData||auth||signedLaunchData());return Promise.resolve(tg);}
 if(telegramSdkPromise)return telegramSdkPromise;
 telegramSdkPromise=new Promise(resolve=>{
   let settled=false;
   const script=document.createElement('script');
   script.src='https://telegram.org/js/telegram-web-app.js';
   script.async=true;
   const finish=(value)=>{
     if(!settled){settled=true;clearTimeout(timer);resolve(value||null);}
   };
   script.onload=()=>{
     const loaded=window.Telegram?.WebApp||null;
     if(loaded){tg=loaded;auth=String(loaded.initData||auth||signedLaunchData());}
     if(!settled)finish(loaded);
     else if(loaded&&auth){
       lateTelegramReady=true;
       if(panelBootComplete&&!panelBooting&&!panelAuthenticated)void boot();
     }
   };
   script.onerror=()=>finish(null);
   const timer=setTimeout(()=>finish(null),timeoutMs);
   document.head.appendChild(script);
 });
 return telegramSdkPromise;
}
let cookieAuth=false;
let csrfToken='';
const $=id=>document.getElementById(id);
const state={tab:'overview',index:1,search:'',admin:false,listingRequest:0,resourceRequest:0,onlyFavorites:false,incidentStatus:'unresolved',selectedConfigs:new Set()};
window.SonarWebAuth={getCsrf:()=>csrfToken,setCsrf:value=>{csrfToken=String(value||'');}};
const titles={billing:'اشتراک و کیف پول',users:'مدیریت کاربران و فروش',overview:'داشبورد',servers:'مدیریت سرورها',configs:'کانفیگ‌ها و اشتراک‌ها',rankings:'رتبه‌بندی کیفیت',incidents:'مرکز رخدادها',enterprise:'مرکز سازمانی',team:'اعضای تیم',audit:'فعالیت‌ها',settings:'تنظیمات',reports:'گزارش‌ها',support:'مرکز عیب‌یابی',fleet:'استخر Agentهای چندمنطقه‌ای',balancer:'بالانسر مسیرها',services:'مانیتورینگ سرویس‌ها',channels:'اطلاع‌رسانی', 'server-detail':'جزئیات سرور','config-detail':'جزئیات کانفیگ','web-create':'افزودن منبع جدید'};
const elem=(tag,text,cl)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);if(cl)el.className=cl;return el;};
function toast(text){$('toast').textContent=text;$('toast').classList.add('visible');clearTimeout(toast.delay);toast.delay=setTimeout(()=>$('toast').classList.remove('visible'),4000);}
async function boundedFetch(path,options){
 const attempts=(options.method||'GET').toUpperCase()==='GET'?2:1;
 for(let attempt=0;attempt<attempts;attempt++){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{return await fetch(path,{...options,signal:options.signal||controller.signal});}
  catch(error){if(options.signal?.aborted||attempt+1===attempts)throw Error(error.name==='AbortError'?'زمان دریافت پاسخ تمام شد؛ دوباره تلاش کنید.':'ارتباط با سرور برقرار نشد.');}
  finally{clearTimeout(timer);}
 }
}
async function stepUp(){
 const code=prompt('برای تأیید مجدد، یک کد ورود تازه از ربات بگیرید و وارد کنید:');
 if(!code)throw Error('تأیید مجدد لغو شد');
 await jsonPost('/api/control/step-up',{code:code.trim()});
}
window.SonarStepUp=async()=>{const result=await request('/api/control/step-up');if(!result.elevated)await stepUp();};
async function request(path,options={}){
  // Use signed Telegram initData until it is exchanged for an HttpOnly cookie session.
  const method=String(options.method||'GET').toUpperCase();
  const response=await boundedFetch(path,{...options,credentials:'same-origin',cache:'no-store',headers:{...(Date.now()-lastInteraction<60000?{'X-User-Activity':'1'}:{}),...(activeWorkspace&&!path.startsWith('/api/billing')&&!['/api/me','/api/control/workspaces','/api/control/sessions'].some(p=>path.startsWith(p))?{'X-Workspace-Id':activeWorkspace}:{}),...(auth&&!cookieAuth?{Authorization:'tma '+auth}:{}),...((!auth||cookieAuth)&&method!=='GET'&&method!=='HEAD'?{'X-CSRF-Token':csrfToken}:{}),...options.headers}});
  if(response.status===401){if(document.body?.dataset)delete document.body.dataset.authenticated;if(!isMiniApp()){$('web-auth').hidden=false;if(location.pathname!=='/login')history.replaceState({},'','/login?next='+encodeURIComponent(panelRoutes[state.tab]||'/dashboard'));}else{const banner=$('session-warning');if(banner)banner.hidden=false;}}
  if(!response.ok){let message=`خطای ارتباط (${response.status})`;try{const d=await response.json();message=d.detail||message;}catch{}if(message==='STEP_UP_REQUIRED'&&!options.stepUpRetried){await stepUp();return request(path,{...options,stepUpRetried:true});}throw Error(message);}
  if(options.raw)return response;
  return response.json();
}
async function jsonPost(path,value){return request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});}
window.SonarWebRequest=request;window.SonarWebPost=jsonPost;
async function prepareMiniAppSession(){
 if(!auth)return;
 const initData=auth;
 // Prefer a fresh signed exchange. The server keeps initData's short age limit;
 // subsequent requests use an HttpOnly cookie plus the normal CSRF token.
 try{
  const response=await fetch('/api/web/miniapp-session',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{Authorization:'tma '+initData}});
  if(response.ok){const data=await response.json();csrfToken=data.csrf||'';cookieAuth=true;return;}
 }catch{}
 // A page reload can retain an older initData string while its prior cookie is
 // still valid. Reuse that cookie only when its authenticated user matches the
 // user ID carried by initData; otherwise keep the signed-initData path.
 try{
  const rawUser=new URLSearchParams(initData).get('user');
  const telegramUserId=Number(JSON.parse(rawUser||'null')?.id);
  if(Number.isSafeInteger(telegramUserId)&&telegramUserId>0){
   const meResponse=await fetch('/api/me',{credentials:'same-origin',cache:'no-store'});
   if(meResponse.ok){
    const me=await meResponse.json();
    if(Number(me.id)===telegramUserId){
     const csrfResponse=await fetch('/api/web/csrf',{credentials:'same-origin',cache:'no-store'});
     if(csrfResponse.ok){const data=await csrfResponse.json();csrfToken=data.csrf||'';cookieAuth=true;return;}
    }
   }
  }
 }catch{}
 cookieAuth=false;
}
function setStatus(text,good=true){$('side-state').textContent=text;const top=$('topbar-status');if(top){top.textContent=text;top.classList.toggle('is-error',!good);}const dot=document.querySelector?.('.statusdot');if(dot?.style)dot.style.background=good?'#47d7a0':'#ff7887';}
function renderSummary(data){$('srv').textContent=`${data.servers.active}/${data.servers.total}`;$('cfg').textContent=`${data.configs.healthy}/${data.configs.total}`;$('inc').textContent=data.incidents;$('service-up').textContent=`${data.services?.up??0}/${data.services?.total??0}`;$('last-test').textContent='آخرین تست ثبت‌شده: '+(data.last_test_at||'هنوز داده‌ای وجود ندارد');}
async function summary(){renderSummary(await request('/api/summary'));}
async function loadDashboardRoutes(){
 const panel=$('dashboard-route-panel'),host=$('dashboard-route-status');if(!panel||!host||!state.admin)return;
 panel.hidden=false;host.replaceChildren(elem('p','در حال دریافت وضعیت مسیرهای سالم...','muted'));
 try{const result=await request('/api/fleet/relays');const pool=result.fallback_order||[];host.replaceChildren();
  const primary=elem('div','مسیر اصلی: سرور مرکزی · همیشه اولین تلاش','route-dashboard-primary');host.append(primary);
  host.append(elem('p',`Agentهای جایگزین واجد شرایط: ${pool.length}`,'route-dashboard-count'));
  if(!pool.length)host.append(elem('p','هیچ Agent سالم و تأییدشده‌ای در پنج دقیقه اخیر در دسترس نیست.','relay-warn-text'));
  pool.slice(0,4).forEach((item,index)=>host.append(elem('div',`${index+2}. ${item.name||'Agent #'+item.id} · ${item.location_type==='ir'?'ایران':'خارج'} · اولویت ${item.priority} · ${item.latency_ms==null?'—':Math.round(item.latency_ms)+' ms'}`,'route-dashboard-agent')));
 }catch(error){host.replaceChildren(elem('p',error.message,'muted'));}
}
function choose(name){tabClick(name);}
const panelRoutes={overview:'/dashboard',servers:'/servers',configs:'/configs',services:'/monitoring',channels:'/notifications',team:'/team',settings:'/settings',audit:'/logs',billing:'/billing',users:'/users',reports:'/reports',rankings:'/rankings',incidents:'/incidents',enterprise:'/enterprise',fleet:'/fleet',balancer:'/balancer',support:'/support','web-create':'/resources/new'};
let restoringRoute=false;
function updateRoute(replace=false){
 if(!panelAuthenticated)return;
 let path=panelRoutes[state.tab];if(state.tab==='server-detail')path='/servers/'+window.SonarConsole?.currentServerId?.();if(state.tab==='config-detail')path='/configs/'+window.SonarConsole?.currentConfigId?.();if(!path)return;
 const url=new URL(path,location.origin);
 if(state.search)url.searchParams.set('q',state.search);
 if(state.index>1)url.searchParams.set('page',String(state.index));
 if(state.onlyFavorites)url.searchParams.set('favorites','1');
 if(state.tab==='incidents')url.searchParams.set('status',state.incidentStatus);
 const target=url.pathname+url.search;
 if(location.pathname+location.search!==target)history[replace?'replaceState':'pushState']({tab:state.tab},'',target+(cookieAuth?'':location.hash));
}
function routeFromLocation(){
 const detail=location.pathname.match(/^\/(servers|configs)\/([1-9][0-9]*)$/);if(detail)return detail[1]==='servers'?'server-detail':'config-detail';
 const query=new URLSearchParams(location.search),path=Object.keys(panelRoutes).find(key=>panelRoutes[key]===location.pathname);
 const fallback=query.get('view');
 const next=query.get('next');if(next){try{const url=new URL(next,location.origin);if(url.origin===location.origin){const match=url.pathname.match(/^\/(servers|configs)\/([1-9][0-9]*)$/);if(match){window.SonarConsole?.setActive(match[1]==='servers'?'server':'config',Number(match[2]));return match[1]==='servers'?'server-detail':'config-detail';}const view=Object.keys(panelRoutes).find(k=>panelRoutes[k]===url.pathname);if(view)return view;}}catch{}}
 return (['/','/login','/dashboard'].includes(location.pathname)&&Object.prototype.hasOwnProperty.call(titles,fallback)?fallback:null)||path||(Object.prototype.hasOwnProperty.call(titles,fallback)?fallback:'overview');
}
function restoreRoute(){
 const params=new URLSearchParams(location.search),name=routeFromLocation();
 const detail=location.pathname.match(/^\/(servers|configs)\/([1-9][0-9]*)$/);if(detail)window.SonarConsole?.setActive(detail[1]==='servers'?'server':'config',Number(detail[2]));
 restoringRoute=true;tabClick(name);restoringRoute=false;
 state.search=params.get('q')||'';state.index=Math.max(1,Math.min(10000,Math.trunc(Number(params.get('page'))||1)));
 state.onlyFavorites=params.get('favorites')==='1';state.incidentStatus=params.get('status')||'unresolved';
 $('search').value=state.search;if($('only-favorites'))$('only-favorites').checked=state.onlyFavorites;
 if(['servers','configs','incidents'].includes(name))loadList();updateRoute(true);
}
window.addEventListener('popstate',()=>{if(panelAuthenticated)restoreRoute();});
function tabClick(name){if(!Object.prototype.hasOwnProperty.call(titles,name)||(['support','balancer'].includes(name)&&!state.admin))return;state.listingRequest++;state.tab=name;state.index=1;state.search='';$('search').value='';
  $('page-title').textContent=titles[name]||name;
  document.title=(titles[name]||name)+' | SONAR RADAR PRO';
  const navTarget=({'server-detail':'servers','config-detail':'configs'})[name]||name;
  document.querySelectorAll('#navigation [data-tab]').forEach(btn=>{const active=btn.dataset.tab===navTarget;btn.classList.toggle('active',active);if(active)btn.setAttribute('aria-current','page');else btn.removeAttribute('aria-current');});
  document.querySelectorAll('.view').forEach(el=>el.classList.remove('active'));
  $(['servers','configs','rankings','incidents'].includes(name)?'listing':name).classList.add('active');
  document.querySelector?.('.workspace')?.scrollIntoView({block:'start',behavior:'instant'});
  const pages=['servers','configs','rankings','incidents'];
  if(pages.includes(name))loadList();
  if(name==='overview'){summary().catch(e=>toast(e.message));loadResourceServers();loadNotificationDelivery();window.SonarConsole?.loadRecent();if(state.admin)loadDashboardRoutes();}
  if(!restoringRoute)updateRoute();
  if(name==='billing'||name==='users')window.SonarBilling?.load(name);
  if(name==='services')loadServices();
  if(name==='enterprise')window.SonarEnterprise?.load?.();
  if(name==='team')loadTeam().then(()=>window.SonarControl?.team()).catch(e=>toast(e.message));
  if(name==='audit')loadAudit();
  if(name==='settings'){loadPreferences();window.SonarControl?.sessions().catch(e=>toast(e.message));window.SonarControl?.policy().catch(e=>toast(e.message));}
  if(name==='reports')window.SonarReports?.load();
  if(name==='support')loadHealth();
  if(name==='channels'){window.SonarConsole?.loadChannels();window.SonarControl?.notifications().catch(e=>toast(e.message));}
  if(name==='fleet'){window.SonarNext?.loadFleet?.();window.SonarControl?.agents().catch(e=>toast(e.message));}
  if(name==='balancer')window.SonarBalancer?.load?.();
  if(name==='server-detail')window.SonarConsole?.loadServer().then(()=>window.SonarControl?.serverActions()).catch(e=>toast(e.message));
  if(name==='config-detail')window.SonarConsole?.loadConfig().then(()=>window.SonarControl?.configActions()).catch(e=>toast(e.message));
}
function statusText(item){const raw=String(item.last_status||'Unknown');return raw.length<70?raw:raw.slice(0,67)+'…';}
function rowItem(item){const root=elem('article',null,'item'),detail=elem('div'),name=elem('strong',item.name||item.title||item.kind||'بدون نام');detail.append(name);
 let subtitle='';
 if(state.tab==='servers')subtitle=`سرور #${item.id} | ${statusText(item)} | ${item.location_type||'موقعیت نامشخص'}`;
 if(state.tab==='configs')subtitle=`کانفیگ #${item.id} | ${statusText(item)} | ${item.last_ping??'?'} ms`;
 if(state.tab==='rankings')subtitle=`پینگ: ${item.ping??'—'} ms | جیتر: ${item.jitter??'—'} ms`;
 if(state.tab==='incidents')subtitle=`${item.severity||'warning'} • ${item.status==='open'?'باز':'بررسی‌شده'} • ${item.occurrences||0} مرتبه`;
 detail.append(elem('small',subtitle));root.append(detail);
 if(state.tab==='servers'){const live=elem('span','','live-label');window.SonarTelemetry?.live(live,item);detail.append(live);}
 const controls=elem('div',null,'tools');
 if(state.tab==='configs'&&item.type!=='sub_source'){const select=elem('input');select.type='checkbox';select.setAttribute('aria-label','انتخاب برای آزمون گروهی');select.checked=state.selectedConfigs.has(item.id);select.onchange=()=>{if(select.checked)state.selectedConfigs.add(item.id);else state.selectedConfigs.delete(item.id);};controls.append(select);}
 if(['servers','configs'].includes(state.tab)){const b=elem('button',item.favorite?'★':'☆');b.setAttribute('aria-label','تغییر علاقه‌مندی');b.onclick=async()=>{try{const v=await jsonPost('/api/favorites',{kind:state.tab==='servers'?'server':'config',rid:item.id});item.favorite=v.favorite;b.textContent=v.favorite?'★':'☆';toast('علاقه‌مندی ذخیره شد');}catch(e){toast(e.message);}};controls.append(b);}
 if(['servers','configs'].includes(state.tab)){const tag=elem('button','🏷 برچسب');tag.setAttribute('aria-label','افزودن برچسب');tag.onclick=async()=>{const value=prompt('برچسب کوتاه بنویسید (حداکثر ۳۲ کاراکتر):');if(!value)return;try{await jsonPost('/api/console/tags',{kind:state.tab==='servers'?'server':'config',rid:item.id,tag:value.trim()});toast('برچسب ذخیره شد');}catch(e){toast(e.message);}};controls.append(tag);const details=elem('button','جزئیات');details.className='primary';details.onclick=()=>window.SonarConsole?.openDetails(state.tab==='servers'?'server':'config',item.id);controls.append(details);}
 if(state.tab==='rankings'){controls.append(elem('strong',`${item.score??0}/100`,'score'));const b=elem('button','📈 نمودار');b.onclick=()=>drawTrend(item);controls.append(b);}
 if(state.tab==='incidents'&&item.status==='open'){const b=elem('button','تأیید مشاهده');b.onclick=async()=>{try{await jsonPost('/api/incidents/ack',{incident_id:item.id});await loadList();}catch(e){toast(e.message);}};controls.append(b);}
 root.append(controls);return root;
}
async function loadList(){const requestId=++state.listingRequest,tab=state.tab,page=state.index,search=state.search;const host=$('list-content');host.replaceChildren(elem('p','در حال دریافت اطلاعات...','muted'));
 const title=titles[tab];$('list-title').textContent=title;$('list-subtitle').textContent=tab==='incidents'?'رخدادهای فضای کاری؛ فیلتر بر اساس چرخه رسیدگی':'اطلاعات ثبت‌شده در فضای کاری';
 const resourceTab=['servers','configs'].includes(tab),incidentTab=tab==='incidents';
 $('search').hidden=!resourceTab;
 let bulk=$('config-bulk-test');if(!bulk){bulk=elem('button','آزمون کانفیگ‌های انتخاب‌شده');bulk.type='button';bulk.id='config-bulk-test';$('list-title').after(bulk);bulk.onclick=async()=>{try{await window.SonarControl.probe([...state.selectedConfigs]);state.selectedConfigs.clear();await loadList();}catch(error){toast(error.message);}};}bulk.hidden=tab!=='configs';
 const ftools=document.querySelector?.('.filter-tools');if(ftools)ftools.hidden=!(resourceTab||incidentTab);
 const favoritesFilter=$('favorites-filter');if(favoritesFilter)favoritesFilter.hidden=!resourceTab;
 const hint=$('list-filter-hint');if(hint)hint.hidden=!resourceTab;
 const incidentFilter=$('incident-filter');if(incidentFilter)incidentFilter.hidden=!incidentTab;
 const incidentSelect=$('incident-status-filter');if(incidentSelect)incidentSelect.value=state.incidentStatus;
 $('prev').disabled=state.index===1;
 try{
 let url='';if(resourceTab){url=`/api/resources/${tab==='servers'?'server':'config'}?page=${page}&q=${encodeURIComponent(search)}&favorites=${state.onlyFavorites?'true':'false'}`;}
 else url=tab==='rankings'?'/api/rankings':`/api/incidents?status=${encodeURIComponent(state.incidentStatus)}`;
 if(!restoringRoute)updateRoute();
 const response=await request(url,{raw:true});const list=await response.json();const total=Number(response.headers.get('X-Total-Count'));if(requestId!==state.listingRequest||tab!==state.tab)return;if(!Array.isArray(list))throw Error('پاسخ نامعتبر از سرور');
 host.replaceChildren();if(!list.length)host.append(elem('p',resourceTab&&state.onlyFavorites?'هنوز موردی به علاقه‌مندی‌ها اضافه نشده است.':'موردی برای نمایش وجود ندارد.','muted'));
 list.forEach(item=>host.append(rowItem(item)));
 $('next').disabled=!resourceTab||(response.headers.has('X-Total-Count')?page*12>=total:list.length<12);
 $('page-number').textContent=page.toLocaleString('fa-IR');setStatus('متصل');
 }catch(e){if(requestId!==state.listingRequest)return;host.replaceChildren(elem('p',e.message,'bad'));setStatus('خطای ارتباط',false);}
}
function simpleEntry(primary,secondary){const item=elem('article',null,'item'),container=elem('div');container.append(elem('strong',primary));container.append(elem('small',secondary));item.append(container);return item;}
async function loadNotificationDelivery(){
 const ids={pending:'delivery-pending',delivered:'delivery-delivered',exhausted:'delivery-exhausted',skipped:'delivery-skipped'};
 const message=$('delivery-message'),stamp=$('delivery-updated');if(!message||!stamp)return;
 try{
  const data=await request('/api/notifications/delivery');
  for(const [key,id] of Object.entries(ids))$(id).textContent=Number(data?.[key]??0).toLocaleString('fa-IR');
  const failed=Number(data?.exhausted||0),pending=Number(data?.pending||0);
  message.textContent=failed>0?`${failed.toLocaleString('fa-IR')} اعلان پس از تلاش‌های مجاز ارسال نشده است؛ صف و تنظیمات اعلان را بررسی کنید.`:pending>0?`${pending.toLocaleString('fa-IR')} اعلان در انتظار پردازش است.`:'صف اعلان‌ها در وضعیت عادی است.';
  stamp.textContent='به‌روزرسانی '+new Date().toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'});
 }catch(error){message.textContent='دریافت وضعیت تحویل اعلان‌ها ممکن نیست: '+error.message;stamp.textContent='داده در دسترس نیست';}
}
window.SonarDashboard={loadNotificationDelivery};
async function loadTeam(){const host=$('team-list');host.replaceChildren(elem('p','در حال دریافت...','muted'));
 try{const result=await request('/api/team');host.replaceChildren();if(!result.length)host.append(elem('p','عضوی در این فضای کاری ثبت نشده است.','muted'));result.forEach(row=>host.append(simpleEntry('کاربر '+row.member_id,'نقش: '+row.role)));}
 catch(e){host.replaceChildren(elem('p',e.message,'bad'));}}
let auditRevision=0;
window.addEventListener('sonar:logs-cleared',()=>{auditRevision++;$('audit-list')?.replaceChildren();$('recent-activity')?.replaceChildren();});
async function loadAudit(){const version=++auditRevision;const host=$('audit-list');host.replaceChildren(elem('p','در حال دریافت...','muted'));
 try{const result=await request('/api/audit');if(version!==auditRevision)return;host.replaceChildren();if(!result.length)host.append(elem('p','تاریخچه‌ای ثبت نشده است.','muted'));result.forEach(row=>host.append(simpleEntry(row.event+' | '+row.target,'کاربر '+row.actor_id+' • '+row.created_at)));}
 catch(e){host.replaceChildren(elem('p',e.message,'bad'));}}
async function loadPreferences(){window.SonarConfigAlerts?.load();try{const p=await request('/api/preferences');$('daily_digest').checked=!!p.daily_digest;$('multi_probe').checked=!!p.multi_probe;const rules=await request('/api/rules');const host=$('rule-list');host.replaceChildren();if(!rules.length)host.append(elem('p','قانون هشدار اختصاصی ثبت نشده است.','muted'));rules.forEach(r=>host.append(simpleEntry(r.event,r.action+' | '+(r.enabled?'فعال':'غیرفعال'))));}catch(e){toast(e.message);}}
async function loadHealth(){if(!state.admin)return;window.SonarLogs?.load(true);const host=$('health-output');host.textContent='در حال بررسی سلامت...';try{const data=await request('/api/diagnostics/health');host.textContent=JSON.stringify(data,null,2);}catch(e){host.textContent=e.message;}}
async function supportZip(){const days=Number($('diagnostic-days')?.value||7),remote=$('diagnostic-remote')?.checked!==false;try{$('download-btn').disabled=true;toast(`در حال جمع‌آوری لاگ کامل ${days*24} ساعت اخیر...`);const result=await request(`/api/diagnostics/bundle?days=${days}&include_remote=${remote}`,{raw:true});const blob=await result.blob();const url=URL.createObjectURL(blob);const a=elem('a');a.href=url;a.download=`sonar-v6.9-full-logs-${days*24}h.zip`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('ZIP آماده شد؛ فایل‌های اصلی روی سرور حذف یا تغییر نکرده‌اند.');}catch(e){toast(e.message);}finally{$('download-btn').disabled=false;}}
async function drawTrend(item){
 try{
  const points=await request('/api/metrics/'+Number(item.id)+'?days=7');
  $('trend-title').textContent='تاریخچه کیفیت: '+String(item.name||item.id);
  const values=points.map(p=>({time:p.measured_at,ping:p.ok&&p.ping!=null&&Number(p.ping)>0?Number(p.ping):null}));
  const good=values.filter(p=>p.ping!==null);
  $('trend').showModal();
  window.SonarTelemetry?.render($('trend-canvas'),values,{unit:'ms',series:[{key:'ping',label:'تأخیر',color:'#16ac7b'}]});
  $('trend-info').textContent=good.length?`${good.length.toLocaleString('fa-IR')} تست موفق از ${values.length.toLocaleString('fa-IR')} · حداقل ${Math.min(...good.map(p=>p.ping))} ms · حداکثر ${Math.max(...good.map(p=>p.ping))} ms · زمان محلی شما`:'تست موفقی ثبت نشده است؛ فاصله‌های خالی به معنی نبود سنجش موفق هستند.';
 }catch(e){toast(e.message);}
}
$('refresh').onclick=()=>{tabClick(state.tab);};
document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>tabClick(b.dataset.tab)));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>tabClick(b.dataset.go)));
$('only-favorites')?.addEventListener('change', e=>{state.onlyFavorites=e.target.checked;state.index=1;if(['servers','configs'].includes(state.tab))loadList();});
$('incident-status-filter')?.addEventListener('change',e=>{state.incidentStatus=String(e.target.value||'unresolved');state.index=1;if(state.tab==='incidents')loadList();});
$('prev').onclick=()=>{if(state.index>1){state.index--;loadList();}};$('next').onclick=()=>{if(!$('next').disabled){state.index++;loadList();}};
let searchDebounce;$('search').addEventListener('input',event=>{clearTimeout(searchDebounce);searchDebounce=setTimeout(()=>{state.search=event.target.value;state.index=1;loadList();},300);});
if($('team-form'))$('team-form').onsubmit=async e=>{e.preventDefault();try{await jsonPost('/api/team',{member:Number($('member-id').value),role:$('member-role').value});toast('دسترسی بروزرسانی شد');loadTeam();}catch(err){toast(err.message);}};
for(const key of ['daily_digest','multi_probe'])$(key).onchange=async e=>{try{await jsonPost('/api/preferences',{key,value:e.target.checked});toast('تنظیمات ذخیره شد');}catch(err){e.target.checked=!e.target.checked;toast(err.message);}};
$('rule-form')?.addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter;if(btn)btn.disabled=true;try{await jsonPost('/api/console/rules',{event:$('rule-event').value,action:$('rule-action').value,enabled:$('rule-enabled').value==='true'});toast('قانون ثبت شد');await loadPreferences();}catch(err){toast(err.message);}finally{if(btn)btn.disabled=false;}});
$('health-btn').onclick=loadHealth;$('download-btn').onclick=supportZip;$('trend-close').onclick=()=>$('trend').close();
async function boot(){
 if(panelBooting)return;
 panelBooting=true;
 try{
   await loadTelegramWebApp();
   if(tg){
     if(document.documentElement?.dataset)document.documentElement.dataset.client='telegram';
     tg.ready?.();tg.expand?.();tg.disableVerticalSwipes?.();
     const syncTelegramChrome=()=>{
       const mode=tg.colorScheme==='light'?'light':'dark';
       if(document.documentElement?.dataset&&!window.SonarTheme?.hasPreference?.())document.documentElement.dataset.theme=mode;
       window.SonarTheme?.applyTelegram?.(mode);
       const color=mode==='light'?'#f3f6fb':'#080d17';
       try{tg.setHeaderColor?.(color);tg.setBackgroundColor?.(color);}catch{}
       if(Number.isFinite(Number(tg.viewportHeight)))document.documentElement.style.setProperty('--tg-viewport-height',`${Number(tg.viewportHeight)}px`);
     };
     syncTelegramChrome();
     tg.onEvent?.('themeChanged',syncTelegramChrome);
     tg.onEvent?.('viewportChanged',syncTelegramChrome);
   }else if(document.documentElement?.dataset){document.documentElement.dataset.client='web';}
   await prepareMiniAppSession();
   try{
     const me=await request('/api/me');
     if(!auth&&!cookieAuth){const session=await request('/api/web/csrf');csrfToken=session.csrf||'';cookieAuth=true;}
     state.admin=!!me.admin;
     if($('billing-admin-tab'))$('billing-admin-tab').hidden=!me.admin&&!(me.billing_permissions||[]).length;
     $('admin-tab').hidden=!me.admin;
     if($('balancer-tab'))$('balancer-tab').hidden=!me.admin;
     if($('dashboard-route-panel'))$('dashboard-route-panel').hidden=!me.admin;
     if($('dashboard-balancer-btn'))$('dashboard-balancer-btn').hidden=!me.admin;
     if($('dashboard-balancer-quick'))$('dashboard-balancer-quick').hidden=!me.admin;
     $('web-auth').hidden=true;
     if($('session-warning'))$('session-warning').hidden=true;
     panelAuthenticated=true;
     document.body.dataset.authenticated='true';
     await window.SonarControl?.workspaces();
     window.SonarLive?.connect();
     setStatus('متصل');restoreRoute();
   }catch(e){
     setStatus('اتصال ناموفق',false);
     $('web-auth').hidden=isMiniApp();
     if(isMiniApp()){if($('session-warning')){const banner=$('session-warning');banner.textContent='ورود خودکار انجام نشد: '+e.message+'؛ مینی‌اپ را از دکمه همین ربات دوباره باز کنید.';banner.hidden=false;}toast(e.message);}
     $('last-test').textContent=e.message;
   }
 }finally{
   panelBooting=false;
   panelBootComplete=true;
   if(lateTelegramReady&&auth&&!panelAuthenticated){
     lateTelegramReady=false;
     void boot();
   }
 }
}
void boot();

// Resource telemetry: historical samples only; no synthetic sensor values.
async function loadResourceServers(){
 const select=$('resource-server');if(!select)return;
 const current=select.value;select.disabled=true;
 try{
   const servers=await request('/api/server-options');
   select.replaceChildren(elem('option','انتخاب سرور'));
   for(const server of servers){const opt=elem('option',server.name||`سرور ${server.id}`);opt.value=String(server.id);select.append(opt);}
   if(current&&servers.some(s=>String(s.id)===current))select.value=current;
   else if(servers.length)select.value=String(servers[0].id);
   if(select.value)await loadResourceChart();
   else resetResource('هنوز سروری ثبت نشده است. از بخش «سرورها» شروع کنید.');
 }catch(err){resetResource(err.message);}finally{select.disabled=false;}
}
function resetResource(message){
 for(const id of ['resource-cpu','resource-ram','resource-time','resource-disk'])$(id).textContent='—';
 $('resource-temp').textContent='داده‌ای موجود نیست';$('resource-traffic').textContent='داده‌ای موجود نیست';
 $('resource-message').textContent=message;
 window.SonarTelemetry?.live($('resource-live'),null,null);
 window.SonarTelemetry?.render($('resource-chart'),[],{hours:Number($('resource-hours')?.value||24)});
}
function validPercent(v){return v===null||v===undefined?null:(Number.isFinite(Number(v))&&Number(v)>=0&&Number(v)<=100?Number(v):null);}
function renderResourceChart(samples,latest){
 window.SonarTelemetry?.render($('resource-chart'),samples,{hours:Number($('resource-hours')?.value||24),latest});
}
async function loadResourceChart(){
 const id=Number($('resource-server').value),sequence=++state.resourceRequest;
 if(!Number.isSafeInteger(id)||id<1){resetResource('یک سرور انتخاب کنید.');return;}
 $('resource-message').textContent='در حال دریافت اطلاعات واقعی سرور…';
 try{
   const result=await request('/api/server-resources/'+id+'?hours='+Number($('resource-hours')?.value||24));
   if(sequence!==state.resourceRequest||Number($('resource-server').value)!==id)return;
   const samples=Array.isArray(result.samples)?result.samples:[],last=result.latest||samples[samples.length-1];
   window.SonarTelemetry?.live($('resource-live'),result.server,last?.time);
   const cpu=validPercent(last?.cpu),ram=validPercent(last?.ram);
   $('resource-cpu').textContent=cpu===null?'—':cpu.toFixed(1)+'٪';
   $('resource-ram').textContent=ram===null?'—':ram.toFixed(1)+'٪';
   $('resource-time').textContent=last?.time?new Date(last.time).toLocaleString('fa-IR'):'—';
   if($('resource-disk'))$('resource-disk').textContent=validPercent(last?.disk)===null?'—':validPercent(last?.disk).toFixed(1)+'٪';
   $('resource-traffic').parentElement.hidden=result.network==null;
   $('resource-temp').textContent=result.temperature==null?'پشتیبانی نشده':`${result.temperature}°C`;
   $('resource-traffic').textContent=result.network==null?'پشتیبانی نشده':String(result.network);
   renderResourceChart(samples,result.latest);
   $('resource-message').textContent=samples.length?`${samples.length.toLocaleString('fa-IR')} نمونه ثبت‌شده · بازه انتخاب‌شده · آبی CPU / بنفش RAM / سبز دیسک`:'هنوز داده‌ای از مصرف منابع ثبت نشده است. وضعیت اتصال ایجنت را بررسی کنید.';
 }catch(err){if(sequence===state.resourceRequest)resetResource(err.message);}
}
$('resource-server')?.addEventListener('change',loadResourceChart);


// v6.3: tenant-bound uptime console, strictly textContent (never unsafe HTML).
function serviceDate(v){if(!v)return 'هنوز بررسی نشده';const d=new Date(v);return Number.isNaN(d.getTime())?'—':d.toLocaleString('fa-IR');}
const statusName={up:'آنلاین',down:'آفلاین',pending:'منتظر اولین تست',paused:'متوقف'};
let editingServiceId=null;
async function loadServices(){
 const host=$('service-grid');if(!host)return;
 host.replaceChildren(elem('p','در حال دریافت مانیتورها...','muted'));
 try{
  const rows=await request('/api/services');host.replaceChildren();
  if(!rows.length)host.append(elem('p','هنوز مانیتوری ایجاد نشده است.','muted'));
  for(const x of rows){
   const card=elem('article',null,'service-card');
   const head=elem('div',null,'service-card-head');
   head.append(elem('strong',x.name));
   head.append(elem('span',statusName[x.status]||'نامشخص','service-chip '+x.status));
   card.append(head,elem('div',x.kind.toUpperCase()+' · '+x.target,'service-target'));
   const stat=elem('p',(x.last_latency_ms==null?'—':Math.round(x.last_latency_ms)+' ms')+' | '+(x.last_detail||'بدون نتیجه')+' | '+serviceDate(x.last_checked_at),'muted');
   card.append(stat);
   const buttons=elem('div',null,'service-actions');
   const history=elem('button','تاریخچه');history.onclick=()=>loadServiceHistory(x);buttons.append(history);
   const edit=elem('button','ویرایش');edit.onclick=()=>{
     editingServiceId=x.id;$('service-name').value=x.name;$('service-kind').value=x.kind;
     $('service-target').value=x.target;$('service-interval').value=x.interval_seconds;
     $('service-timeout').value=x.timeout_seconds;$('service-failures').value=x.failure_threshold;
     $('service-recoveries').value=x.recovery_threshold;$('service-expected').value=x.expected_status??'';
     $('service-save').textContent='ذخیره تغییرات';$('service-cancel').hidden=false;
     $('service-form').scrollIntoView?.({block:'center',behavior:'smooth'});
   };buttons.append(edit);
   const pause=elem('button',x.enabled?'توقف':'فعال‌سازی');pause.onclick=async()=>{
     try{await jsonPost('/api/services/'+x.id+'/enabled',{enabled:!x.enabled});await loadServices();toast('وضعیت ذخیره شد');}catch(err){toast(err.message);}
   };buttons.append(pause);
   const del=elem('button','حذف');del.classList.add('destructive');del.onclick=async()=>{
    if(!confirm('مانیتور «'+x.name+'» و تاریخچه آن حذف شود؟'))return;
    try{await request('/api/services/'+x.id,{method:'DELETE'});await loadServices();toast('مانیتور حذف شد');}
    catch(err){toast(err.message);}
   };buttons.append(del);card.append(buttons);host.append(card);
  }
 }catch(err){host.replaceChildren(elem('p',err.message,'bad'));}
}
async function loadServiceHistory(item){
 const host=$('service-history');$('service-history-title').textContent='آخرین تست‌های '+item.name;
 host.replaceChildren(elem('p','در حال دریافت...','muted'));
 try{
   const data=await request('/api/services/'+item.id+'/history?days=7');host.replaceChildren();
   const latest=data.slice(-120).reverse();
   if(!latest.length)host.append(elem('p','هنوز داده‌ای ثبت نشده است.','muted'));
   // Uptime visualization in the style of a compact heartbeat strip.
   const strip=elem('div',null,'uptime-strip');strip.setAttribute('aria-label','نمایش وضعیت موفق یا ناموفق آخرین ۱۲۰ تست');
   data.slice(-120).forEach(sample=>{const bar=elem('span',null,sample.ok?'ok':'fail');bar.title=serviceDate(sample.checked_at)+' | '+sample.detail;strip.append(bar);});
   if(data.length)host.append(strip);
   const up=data.filter(x=>x.ok).length;
   if(data.length)host.append(elem('p','نمونه‌های ۷ روز اخیر (تا ۱۵۰۰ نتیجه): '+up+' موفق از '+data.length+' تست · '+(100*up/data.length).toFixed(1)+'٪','muted'));
   latest.slice(0,25).forEach(sample=>host.append(simpleEntry((sample.ok?'🟢 ':'🔴 ')+sample.detail,
         serviceDate(sample.checked_at)+' · '+(sample.latency_ms==null?'—':sample.latency_ms+'ms'))));
   await window.SonarControl?.regions(x);
 }catch(err){host.replaceChildren(elem('p',err.message,'bad'));}
}
$('services-refresh')?.addEventListener?.('click',loadServices);
function resetServiceForm(){editingServiceId=null;$('service-form').reset();$('service-save').textContent='+ ایجاد مانیتور';$('service-cancel').hidden=true;}
$('service-cancel')?.addEventListener?.('click',resetServiceForm);
$('service-kind')?.addEventListener?.('change',()=>{$('service-target').placeholder=$('service-kind').value==='tcp'?'example.com:443':'https://example.com/health';});
$('service-form')?.addEventListener?.('submit',async ev=>{
 ev.preventDefault();const btn=ev.submitter;if(btn)btn.disabled=true;
 try{
   const body={name:$('service-name').value,kind:$('service-kind').value,target:$('service-target').value,
      interval:Number($('service-interval').value),timeout:Number($('service-timeout').value),
      failure_threshold:Number($('service-failures').value),recovery_threshold:Number($('service-recoveries').value)};
   const value=$('service-expected').value.trim();if(value)body.expected_status=Number(value);
   if(editingServiceId){await request('/api/services/'+editingServiceId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('مانیتور ویرایش شد');}
   else{await jsonPost('/api/services',body);toast('مانیتور ایجاد شد');}
   resetServiceForm();await loadServices();
 }catch(err){toast(err.message);}finally{if(btn)btn.disabled=false;}
});

$('resource-hours')?.addEventListener('change',loadResourceChart);
$('resource-refresh')?.addEventListener('click',loadResourceChart);
window.setInterval?.(()=>{if(state.tab==='overview'&&document.visibilityState==='visible'&&$('resource-server')?.value)void loadResourceChart();},15000);

let liveSource=null;
window.SonarLive={connect(){
 if(liveSource)liveSource.close();
 if(!cookieAuth||typeof EventSource==='undefined')return;
 liveSource=new EventSource('/api/events'+(activeWorkspace?'?owner='+encodeURIComponent(activeWorkspace):''));
 liveSource.addEventListener('summary',event=>{try{if(state.tab==='overview')renderSummary(JSON.parse(event.data));}catch{}});
 liveSource.onerror=()=>{if(!panelAuthenticated)liveSource.close();};
}};
