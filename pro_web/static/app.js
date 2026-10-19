'use strict';
// Only use Telegram-signed initData; never store credentials in URL or browser storage.
const tg=window.Telegram?.WebApp;
const auth=tg?.initData||'';
const $=id=>document.getElementById(id);
const state={tab:'overview',index:1,search:'',admin:false,listingRequest:0,resourceRequest:0};
const titles={overview:'داشبورد',servers:'مدیریت سرورها',configs:'کانفیگ و اشتراک',rankings:'رتبه‌بندی کیفیت',incidents:'مرکز رخدادها',team:'اعضای تیم',audit:'فعالیت‌ها',settings:'تنظیمات',support:'مرکز عیب‌یابی',services:'مانیتورینگ سرویس‌ها'};
const elem=(tag,text,cl)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);if(cl)el.className=cl;return el;};
function toast(text){$('toast').textContent=text;$('toast').classList.add('visible');clearTimeout(toast.delay);toast.delay=setTimeout(()=>$('toast').classList.remove('visible'),4000);}
async function request(path,options={}){
  if(!auth)throw Error('لطفاً پنل را از داخل تلگرام و با دکمه پنل وب باز کنید.');
  const response=await fetch(path,{...options,cache:'no-store',headers:{Authorization:'tma '+auth,...options.headers}});
  if(response.status===401){const banner=$('session-warning');if(banner)banner.hidden=false;}
  if(!response.ok){let message=`خطای ارتباط (${response.status})`;try{const d=await response.json();message=d.detail||message;}catch{}throw Error(message);}
  if(options.raw)return response;
  return response.json();
}
async function jsonPost(path,value){return request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});}
function setStatus(text,good=true){$('side-state').textContent=text;const dot=document.querySelector?.('.statusdot');if(dot?.style)dot.style.background=good?'#75dcac':'#ff938e';}
async function summary(){const data=await request('/api/summary');$('srv').textContent=`${data.servers.active}/${data.servers.total}`;$('cfg').textContent=`${data.configs.healthy}/${data.configs.total}`;$('inc').textContent=data.incidents;$('service-up').textContent=`${data.services?.up??0}/${data.services?.total??0}`;$('last-test').textContent='آخرین تست ثبت‌شده: '+(data.last_test_at||'هنوز داده‌ای وجود ندارد');}
function choose(name){tabClick(name);}
function tabClick(name){if(!Object.prototype.hasOwnProperty.call(titles,name)||(name==='support'&&!state.admin))return;state.listingRequest++;state.tab=name;state.index=1;state.search='';$('search').value='';
  $('page-title').textContent=titles[name]||name;
  document.querySelectorAll('.navbtn').forEach(btn=>btn.classList.toggle('active',btn.dataset.tab===name));
  document.querySelectorAll('.view').forEach(el=>el.classList.remove('active'));
  $(['servers','configs','rankings','incidents'].includes(name)?'listing':name).classList.add('active');
  document.querySelector?.('.workspace')?.scrollIntoView({block:'start',behavior:'instant'});
  const pages=['servers','configs','rankings','incidents'];
  if(pages.includes(name))loadList();
  if(name==='overview'){summary().catch(e=>toast(e.message));loadResourceServers();}
  if(name==='services')loadServices();
  if(name==='team')loadTeam();
  if(name==='audit')loadAudit();
  if(name==='settings')loadPreferences();
  if(name==='support')loadHealth();
}
function statusText(item){const raw=String(item.last_status||'Unknown');return raw.length<70?raw:raw.slice(0,67)+'…';}
function rowItem(item){const root=elem('article',null,'item'),detail=elem('div'),name=elem('strong',item.name||item.title||item.kind||'بدون نام');detail.append(name);
 let subtitle='';
 if(state.tab==='servers')subtitle=`سرور #${item.id} | ${statusText(item)} | ${item.location_type||'موقعیت نامشخص'}`;
 if(state.tab==='configs')subtitle=`کانفیگ #${item.id} | ${statusText(item)} | ${item.last_ping??'?'} ms`;
 if(state.tab==='rankings')subtitle=`پینگ: ${item.ping??'—'} ms | جیتر: ${item.jitter??'—'} ms`;
 if(state.tab==='incidents')subtitle=`${item.severity||'warning'} • ${item.status==='open'?'باز':'بررسی‌شده'} • ${item.occurrences||0} مرتبه`;
 detail.append(elem('small',subtitle));root.append(detail);
 const controls=elem('div',null,'tools');
 if(['servers','configs'].includes(state.tab)){const b=elem('button',item.favorite?'★':'☆');b.setAttribute('aria-label','تغییر علاقه‌مندی');b.onclick=async()=>{try{const v=await jsonPost('/api/favorites',{kind:state.tab==='servers'?'server':'config',rid:item.id});item.favorite=v.favorite;b.textContent=v.favorite?'★':'☆';toast('علاقه‌مندی ذخیره شد');}catch(e){toast(e.message);}};controls.append(b);}
 if(state.tab==='rankings'){controls.append(elem('strong',`${item.score??0}/100`,'score'));const b=elem('button','📈 نمودار');b.onclick=()=>drawTrend(item);controls.append(b);}
 if(state.tab==='incidents'&&item.status==='open'){const b=elem('button','تأیید مشاهده');b.onclick=async()=>{try{await jsonPost('/api/incidents/ack',{incident_id:item.id});await loadList();}catch(e){toast(e.message);}};controls.append(b);}
 root.append(controls);return root;
}
async function loadList(){const requestId=++state.listingRequest,tab=state.tab,page=state.index,search=state.search;const host=$('list-content');host.replaceChildren(elem('p','در حال دریافت اطلاعات...','muted'));
 const title=titles[tab];$('list-title').textContent=title;$('list-subtitle').textContent='اطلاعات ثبت‌شده در سیستم';$('search').hidden=!['servers','configs'].includes(tab);
 $('prev').disabled=state.index===1;
 try{
 let url='';if(['servers','configs'].includes(tab)){url=`/api/resources/${tab==='servers'?'server':'config'}?page=${page}&q=${encodeURIComponent(search)}`;}
 else url=tab==='rankings'?'/api/rankings':'/api/incidents?status=unresolved';
 const list=await request(url);if(requestId!==state.listingRequest||tab!==state.tab)return;if(!Array.isArray(list))throw Error('پاسخ نامعتبر از سرور');
 host.replaceChildren();if(!list.length)host.append(elem('p','موردی برای نمایش وجود ندارد.','muted'));
 list.forEach(item=>host.append(rowItem(item)));
 $('next').disabled=!['servers','configs'].includes(tab)||list.length<12;
 $('page-number').textContent=page.toLocaleString('fa-IR');setStatus('متصل');
 }catch(e){if(requestId!==state.listingRequest)return;host.replaceChildren(elem('p',e.message,'bad'));setStatus('خطای ارتباط',false);}
}
function simpleEntry(primary,secondary){const item=elem('article',null,'item'),container=elem('div');container.append(elem('strong',primary));container.append(elem('small',secondary));item.append(container);return item;}
async function loadTeam(){const host=$('team-list');host.replaceChildren(elem('p','در حال دریافت...','muted'));
 try{const result=await request('/api/team');host.replaceChildren();if(!result.length)host.append(elem('p','عضوی در این فضای کاری ثبت نشده است.','muted'));result.forEach(row=>host.append(simpleEntry('کاربر '+row.member_id,'نقش: '+row.role)));}
 catch(e){host.replaceChildren(elem('p',e.message,'bad'));}}
async function loadAudit(){const host=$('audit-list');host.replaceChildren(elem('p','در حال دریافت...','muted'));
 try{const result=await request('/api/audit');host.replaceChildren();if(!result.length)host.append(elem('p','تاریخچه‌ای ثبت نشده است.','muted'));result.forEach(row=>host.append(simpleEntry(row.event+' | '+row.target,'کاربر '+row.actor_id+' • '+row.created_at)));}
 catch(e){host.replaceChildren(elem('p',e.message,'bad'));}}
async function loadPreferences(){try{const p=await request('/api/preferences');$('daily_digest').checked=!!p.daily_digest;$('multi_probe').checked=!!p.multi_probe;const rules=await request('/api/rules');const host=$('rule-list');host.replaceChildren();if(!rules.length)host.append(elem('p','قانون هشدار اختصاصی ثبت نشده است.','muted'));rules.forEach(r=>host.append(simpleEntry(r.event,r.action+' | '+(r.enabled?'فعال':'غیرفعال'))));}catch(e){toast(e.message);}}
async function loadHealth(){if(!state.admin)return;const host=$('health-output');host.textContent='در حال بررسی سلامت...';try{const data=await request('/api/diagnostics/health');host.textContent=JSON.stringify(data,null,2);}catch(e){host.textContent=e.message;}}
async function supportZip(){try{$('download-btn').disabled=true;toast('در حال جمع‌آوری بسته گزارش...');const result=await request('/api/diagnostics/bundle',{raw:true});const blob=await result.blob();const url=URL.createObjectURL(blob);const a=elem('a');a.href=url;a.download='sonar-v6.3-support.zip';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);toast('گزارش آماده شد. قبل از ارسال فایل را بررسی کنید.');}catch(e){toast(e.message);}finally{$('download-btn').disabled=false;}}
async function drawTrend(item){try{const points=await request('/api/metrics/'+Number(item.id)+'?days=7');$('trend-title').textContent='تاریخچه کیفیت: '+String(item.name||item.id);const vals=points.filter(x=>x.ok&&x.ping!=null&&Number.isFinite(Number(x.ping))&&Number(x.ping)>0).slice(0,80).reverse();const canvas=$('trend-canvas'),ctx=canvas.getContext?.('2d'),w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#111a28';ctx.fillRect(0,0,w,h);
if(!vals.length){$('trend-info').textContent='تست موفقی ثبت نشده است.';$('trend').showModal();return;}
const samples=vals.map(x=>Number(x.ping)),max=Math.max(...samples,100)*1.1;ctx.strokeStyle='#344558';ctx.lineWidth=1;for(let i=0;i<5;i++){let y=15+i*(h-35)/4;ctx.beginPath();ctx.moveTo(35,y);ctx.lineTo(w-10,y);ctx.stroke();}ctx.strokeStyle='#75dcac';ctx.lineWidth=3;ctx.beginPath();samples.forEach((value,i)=>{let x=35+i*(w-55)/Math.max(samples.length-1,1),y=h-20-value*(h-40)/max;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();$('trend-info').textContent=`تعداد: ${vals.length} | حداقل: ${Math.min(...samples)}ms | حداکثر: ${Math.max(...samples)}ms`;$('trend').showModal();
}catch(e){toast(e.message);}}
$('refresh').onclick=()=>{tabClick(state.tab);};
document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>tabClick(b.dataset.tab)));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>tabClick(b.dataset.go)));
$('prev').onclick=()=>{if(state.index>1){state.index--;loadList();}};$('next').onclick=()=>{if(!$('next').disabled){state.index++;loadList();}};
let searchDebounce;$('search').addEventListener('input',event=>{clearTimeout(searchDebounce);searchDebounce=setTimeout(()=>{state.search=event.target.value;state.index=1;loadList();},300);});
$('team-form').onsubmit=async e=>{e.preventDefault();try{await jsonPost('/api/team',{member:Number($('member-id').value),role:$('member-role').value});toast('دسترسی بروزرسانی شد');loadTeam();}catch(err){toast(err.message);}};
for(const key of ['daily_digest','multi_probe'])$(key).onchange=async e=>{try{await jsonPost('/api/preferences',{key,value:e.target.checked});toast('تنظیمات ذخیره شد');}catch(err){e.target.checked=!e.target.checked;toast(err.message);}};
$('health-btn').onclick=loadHealth;$('download-btn').onclick=supportZip;$('trend-close').onclick=()=>$('trend').close();
async function boot(){if(tg){tg.ready();tg.expand();if(document.documentElement?.dataset)document.documentElement.dataset.theme=tg.colorScheme==='light'?'light':'dark';tg.onEvent?.('themeChanged',()=>{if(document.documentElement?.dataset)document.documentElement.dataset.theme=tg.colorScheme==='light'?'light':'dark';});}try{const me=await request('/api/me');state.admin=!!me.admin;$('admin-tab').hidden=!me.admin;setStatus('متصل');tabClick('overview');}catch(e){setStatus('اتصال ناموفق',false);toast(e.message);$('last-test').textContent=e.message;}}
boot();

// Resource telemetry: historical samples only; no synthetic sensor values.
async function loadResourceServers(){
 const select=$('resource-server');if(!select)return;
 const current=select.value;select.disabled=true;
 try{
   const servers=[];
   for(let page=1;page<=5;page++){
     const batch=await request(`/api/resources/server?page=${page}`);
     if(!Array.isArray(batch))throw Error('فهرست سرورها نامعتبر است');
     servers.push(...batch);
     if(batch.length<12)break;
   }
   select.replaceChildren(elem('option','انتخاب سرور'));
   for(const server of servers){const opt=elem('option',server.name||`سرور ${server.id}`);opt.value=String(server.id);select.append(opt);}
   if(current&&servers.some(s=>String(s.id)===current))select.value=current;
   else if(servers.length)select.value=String(servers[0].id);
   if(select.value)await loadResourceChart();
   else resetResource('هنوز سروری ثبت نشده است. از بخش «سرورها» شروع کنید.');
 }catch(err){resetResource(err.message);}finally{select.disabled=false;}
}
function resetResource(message){
 for(const id of ['resource-cpu','resource-ram','resource-time'])$(id).textContent='—';
 $('resource-temp').textContent='داده‌ای موجود نیست';$('resource-traffic').textContent='داده‌ای موجود نیست';
 $('resource-message').textContent=message;
 const canvas=$('resource-chart'),ctx=canvas?.getContext?.('2d');if(ctx)ctx.clearRect(0,0,canvas.width,canvas.height);
}
function validPercent(v){return v===null||v===undefined?null:(Number.isFinite(Number(v))?Math.min(100,Math.max(0,Number(v))):null);}
function renderResourceChart(samples){
 const canvas=$('resource-chart'),ctx=canvas.getContext?.('2d');if(!ctx)return;
 const w=canvas.width,h=canvas.height,top=24,bottom=h-34,left=50,right=w-20;
 const light=document.documentElement.dataset.theme==='light';
 ctx.clearRect(0,0,w,h);
 ctx.fillStyle=light?'#fbfcff':'#111c30';ctx.fillRect(0,0,w,h);
 ctx.font='13px system-ui';ctx.textAlign='left';
 for(let i=0;i<=4;i++){
   const y=top+(bottom-top)*i/4;
   ctx.strokeStyle=light?'#e6eaf2':'#304156';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
   ctx.fillStyle=light?'#64748b':'#a2b4cb';ctx.fillText(`${100-i*25}%`,8,y+4);
 }
 const palette=[['cpu','#0a84ff'],['ram','#a78bfa']];
 for(const [key,color] of palette){
   ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=3;ctx.lineCap='round';ctx.lineJoin='round';let connected=false;
   samples.forEach((point,index)=>{
     const value=validPercent(point[key]);if(value===null){connected=false;return;}
     const x=left+index*(right-left)/Math.max(1,samples.length-1),y=bottom-value*(bottom-top)/100;
     if(!connected)ctx.moveTo(x,y);else ctx.lineTo(x,y);connected=true;
   });
   ctx.stroke();
 }
}
async function loadResourceChart(){
 const id=Number($('resource-server').value),sequence=++state.resourceRequest;
 if(!Number.isSafeInteger(id)||id<1){resetResource('یک سرور انتخاب کنید.');return;}
 $('resource-message').textContent='در حال دریافت اطلاعات واقعی سرور…';
 try{
   const result=await request('/api/server-resources/'+id);
   if(sequence!==state.resourceRequest||Number($('resource-server').value)!==id)return;
   const samples=Array.isArray(result.samples)?result.samples:[],last=samples.at(-1);
   const cpu=validPercent(last?.cpu),ram=validPercent(last?.ram);
   $('resource-cpu').textContent=cpu===null?'—':cpu.toFixed(1)+'٪';
   $('resource-ram').textContent=ram===null?'—':ram.toFixed(1)+'٪';
   $('resource-time').textContent=last?.time?new Date(last.time).toLocaleString('fa-IR'):'—';
   $('resource-temp').textContent=result.temperature==null?'پشتیبانی نشده':`${result.temperature}°C`;
   $('resource-traffic').textContent=result.network==null?'پشتیبانی نشده':String(result.network);
   renderResourceChart(samples);
   $('resource-message').textContent=samples.length?`${samples.length.toLocaleString('fa-IR')} نمونه ثبت‌شده · تاریخچه حداکثر ۲۴ ساعت · آبی CPU / بنفش RAM`:'هنوز داده‌ای از مصرف منابع ثبت نشده است. وضعیت اتصال ایجنت را بررسی کنید.';
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
