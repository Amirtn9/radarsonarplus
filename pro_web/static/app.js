'use strict';
// Only use Telegram-signed initData; never store credentials in URL or browser storage.
const tg=window.Telegram?.WebApp;
const auth=tg?.initData||'';
const $=id=>document.getElementById(id);
const state={tab:'overview',index:1,search:'',admin:false};
const titles={overview:'داشبورد',servers:'مدیریت سرورها',configs:'کانفیگ و اشتراک',rankings:'رتبه‌بندی کیفیت',incidents:'مرکز رخدادها',team:'اعضای تیم',audit:'فعالیت‌ها',settings:'تنظیمات',support:'مرکز عیب‌یابی'};
const elem=(tag,text,cl)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);if(cl)el.className=cl;return el;};
function toast(text){$('toast').textContent=text;$('toast').classList.add('visible');clearTimeout(toast.delay);toast.delay=setTimeout(()=>$('toast').classList.remove('visible'),4000);}
async function request(path,options={}){
  if(!auth)throw Error('لطفاً پنل را از داخل تلگرام و با دکمه پنل وب باز کنید.');
  const response=await fetch(path,{...options,cache:'no-store',headers:{Authorization:'tma '+auth,...options.headers}});
  if(!response.ok){let message=`خطای ارتباط (${response.status})`;try{const d=await response.json();message=d.detail||message;}catch{}throw Error(message);}
  if(options.raw)return response;
  return response.json();
}
async function jsonPost(path,value){return request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});}
function setStatus(text,good=true){$('side-state').textContent=text;const dot=document.querySelector?.('.statusdot');if(dot?.style)dot.style.background=good?'#75dcac':'#ff938e';}
async function summary(){const data=await request('/api/summary');$('srv').textContent=`${data.servers.active}/${data.servers.total}`;$('cfg').textContent=`${data.configs.healthy}/${data.configs.total}`;$('inc').textContent=data.incidents;$('last-test').textContent='آخرین تست ثبت‌شده: '+(data.last_test_at||'هنوز داده‌ای وجود ندارد');}
function choose(name){tabClick(name);}
function tabClick(name){if(name==='support'&&!state.admin)return;state.tab=name;state.index=1;state.search='';$('search').value='';
  $('page-title').textContent=titles[name]||name;
  document.querySelectorAll('.navbtn').forEach(btn=>btn.classList.toggle('active',btn.dataset.tab===name));
  document.querySelectorAll('.view').forEach(el=>el.classList.remove('active'));
  $(['servers','configs','rankings','incidents'].includes(name)?'listing':name).classList.add('active');
  const pages=['servers','configs','rankings','incidents'];
  if(pages.includes(name))loadList();
  if(name==='overview')summary().catch(e=>toast(e.message));
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
 if(state.tab==='rankings')subtitle=`#${item.id} | Ping: ${item.ping??'?'} ms | Jitter: ${item.jitter??'?'} ms`;
 if(state.tab==='incidents')subtitle=`#${item.id} | ${item.status||'?'} | ${item.occurrences||0} بار`;
 detail.append(elem('small',subtitle));root.append(detail);
 const controls=elem('div',null,'tools');
 if(['servers','configs'].includes(state.tab)){const b=elem('button',item.favorite?'★':'☆');b.setAttribute('aria-label','تغییر علاقه‌مندی');b.onclick=async()=>{try{const v=await jsonPost('/api/favorites',{kind:state.tab==='servers'?'server':'config',rid:item.id});item.favorite=v.favorite;b.textContent=v.favorite?'★':'☆';toast('علاقه‌مندی ذخیره شد');}catch(e){toast(e.message);}};controls.append(b);}
 if(state.tab==='rankings'){controls.append(elem('strong',`${item.score||0}/100`,'score'));const b=elem('button','📈 نمودار');b.onclick=()=>drawTrend(item);controls.append(b);}
 if(state.tab==='incidents'&&item.status==='open'){const b=elem('button','تأیید مشاهده');b.onclick=async()=>{try{await jsonPost('/api/incidents/ack',{incident_id:item.id});await loadList();}catch(e){toast(e.message);}};controls.append(b);}
 root.append(controls);return root;
}
async function loadList(){const host=$('list-content');host.replaceChildren(elem('p','در حال دریافت اطلاعات...','muted'));
 const title=titles[state.tab];$('list-title').textContent=title;$('list-subtitle').textContent='اطلاعات ثبت‌شده در سیستم';$('search').hidden=!['servers','configs'].includes(state.tab);
 $('prev').disabled=state.index===1;
 try{
 let url='';if(['servers','configs'].includes(state.tab)){url=`/api/resources/${state.tab==='servers'?'server':'config'}?page=${state.index}&q=${encodeURIComponent(state.search)}`;}
 else url=state.tab==='rankings'?'/api/rankings':'/api/incidents?status=unresolved';
 const list=await request(url);if(!Array.isArray(list))throw Error('پاسخ نامعتبر از سرور');
 host.replaceChildren();if(!list.length)host.append(elem('p','موردی برای نمایش وجود ندارد.','muted'));
 list.forEach(item=>host.append(rowItem(item)));
 $('next').disabled=!['servers','configs'].includes(state.tab)||list.length<12;
 $('page-number').textContent=state.index.toLocaleString('fa-IR');setStatus('متصل');
 }catch(e){host.replaceChildren(elem('p',e.message,'bad'));setStatus('خطای ارتباط',false);}
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
async function supportZip(){try{$('download-btn').disabled=true;toast('در حال جمع‌آوری بسته گزارش...');const result=await request('/api/diagnostics/bundle',{raw:true});const blob=await result.blob();const url=URL.createObjectURL(blob);const a=elem('a');a.href=url;a.download='sonar-v6-support.zip';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);toast('گزارش آماده شد. قبل از ارسال فایل را بررسی کنید.');}catch(e){toast(e.message);}finally{$('download-btn').disabled=false;}}
async function drawTrend(item){try{const points=await request('/api/metrics/'+Number(item.id)+'?days=7');$('trend-title').textContent='تاریخچه کیفیت: '+String(item.name||item.id);const vals=points.filter(x=>x.ok&&Number.isFinite(Number(x.ping))&&Number(x.ping)>0).slice(0,80).reverse();const canvas=$('trend-canvas'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#111a28';ctx.fillRect(0,0,w,h);
if(!vals.length){$('trend-info').textContent='تست موفقی ثبت نشده است.';$('trend').showModal();return;}
const samples=vals.map(x=>Number(x.ping)),max=Math.max(...samples,100)*1.1;ctx.strokeStyle='#344558';ctx.lineWidth=1;for(let i=0;i<5;i++){let y=15+i*(h-35)/4;ctx.beginPath();ctx.moveTo(35,y);ctx.lineTo(w-10,y);ctx.stroke();}ctx.strokeStyle='#75dcac';ctx.lineWidth=3;ctx.beginPath();samples.forEach((value,i)=>{let x=35+i*(w-55)/Math.max(samples.length-1,1),y=h-20-value*(h-40)/max;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();$('trend-info').textContent=`تعداد: ${vals.length} | حداقل: ${Math.min(...samples)}ms | حداکثر: ${Math.max(...samples)}ms`;$('trend').showModal();
}catch(e){toast(e.message);}}
$('refresh').onclick=()=>{summary().catch(e=>toast(e.message));tabClick(state.tab);};
document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>tabClick(b.dataset.tab)));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>tabClick(b.dataset.go)));
$('prev').onclick=()=>{state.index=Math.max(1,state.index-1);loadList();};$('next').onclick=()=>{state.index++;loadList();};
let searchDebounce;$('search').oninput=event=>{clearTimeout(searchDebounce);searchDebounce=setTimeout(()=>{state.search=event.target.value;state.index=1;loadList();},300);};
$('team-form').onsubmit=async e=>{e.preventDefault();try{await jsonPost('/api/team',{member:Number($('member-id').value),role:$('member-role').value});toast('دسترسی بروزرسانی شد');loadTeam();}catch(err){toast(err.message);}};
for(const key of ['daily_digest','multi_probe'])$(key).onchange=async e=>{try{await jsonPost('/api/preferences',{key,value:e.target.checked});toast('تنظیمات ذخیره شد');}catch(err){e.target.checked=!e.target.checked;toast(err.message);}};
$('health-btn').onclick=loadHealth;$('download-btn').onclick=supportZip;$('trend-close').onclick=()=>$('trend').close();
async function boot(){if(tg){tg.ready();tg.expand();}try{const me=await request('/api/me');state.admin=!!me.admin;$('admin-tab').hidden=!me.admin;setStatus('متصل');await summary();tabClick('overview');}catch(e){setStatus('اتصال ناموفق',false);toast(e.message);$('last-test').textContent=e.message;}}
boot();
