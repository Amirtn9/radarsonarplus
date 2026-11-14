'use strict';
// Server authorization remains authoritative; rendering always uses textContent.
window.SonarLogs=(()=>{
 const el=id=>document.getElementById(id);
 let page=1,revision=0;
 function text(tag,value){const node=document.createElement(tag);node.textContent=String(value);return node;}
 async function load(reset=false){
  const host=el('managed-log-list');if(!host)return;
  if(reset)page=1;
  const version=++revision;
  const params=new URLSearchParams({window:el('managed-log-window').value,level:el('managed-log-level').value,category:el('managed-log-category').value,page:String(page)});
  const custom=params.get('window')==='custom';el('managed-log-custom').hidden=!custom;
  host.replaceChildren(text('p','در حال دریافت لاگ‌ها…'));
  try{
   if(custom){
    for(const key of ['start','end']){
     const value=el('managed-log-'+key).value;
     if(!value)throw Error('ابتدا زمان شروع و پایان را انتخاب کنید.');
     params.set(key,new Date(value).toISOString());
    }
   }
   const data=await window.SonarWebRequest('/api/admin/logs?'+params);
   if(version!==revision)return;
   host.replaceChildren();
   for(const row of data.items){
    const card=text('article','');card.className='item';
    const body=text('div','');
    body.append(text('strong',row.level+' · '+row.category+' · '+row.source),
      text('p',new Date(row.created_at).toLocaleString('fa-IR')+' · '+row.status),text('pre',row.message));
    card.append(body);host.append(card);
   }
   if(!data.items.length)host.append(text('p','لاگی در این بازه وجود ندارد.'));
   el('managed-log-count').textContent=`${data.total} رویداد · صفحه ${page}`;
   const health=data.logging_health;
   if(el('managed-log-health'))el('managed-log-health').textContent=health?
    `صف حافظه: ${health.memory_pending} · صف دیسکی: ${health.durable_pending??'نامعلوم'} · رکورد از دست رفته در این پردازش: ${health.dropped} · خطای نوشتن: ${health.write_failures} · خطای دوام دیسکی: ${health.durability_failures||0}${health.spool_error?' · هشدار: ذخیره دیسکی نیازمند بررسی است':''}${health.durable_mode===false?' · حالت حافظه‌ای فعال است':''}`:'';
   el('managed-log-prev').disabled=page<=1;
   el('managed-log-next').disabled=page*data.per_page>=data.total;
  }catch(error){if(version===revision){host.replaceChildren(text('p',error.message));el('managed-log-count').textContent='';el('managed-log-next').disabled=true;}}
 }
 el('managed-log-form')?.addEventListener('submit',event=>{event.preventDefault();void load(true);});
 el('managed-log-window')?.addEventListener('change',()=>{el('managed-log-custom').hidden=el('managed-log-window').value!=='custom';});
 el('managed-log-prev')?.addEventListener('click',()=>{if(page>1){page--;void load();}});
 el('managed-log-next')?.addEventListener('click',()=>{page++;void load();});
 el('managed-log-delete')?.addEventListener('click',async()=>{
  if(!await window.SonarUI.confirm('تمام لاگ‌های پایگاه داده و تاریخچه فعالیت همه کاربران برای همیشه پاک شوند؟ این عملیات بازگشت ندارد.'))return;
  const confirmation=await window.SonarUI.ask('برای تأیید نهایی عبارت DELETE ALL LOGS را دقیق وارد کنید.');
  if(confirmation!=='DELETE ALL LOGS')return;
  const button=el('managed-log-delete');button.disabled=true;
  try{
   await window.SonarWebRequest('/api/admin/logs',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation})});
   // Invalidate requests already in flight and cached activity DOM immediately.
   revision++;window.dispatchEvent(new Event('sonar:logs-cleared'));el('managed-log-list').replaceChildren();
   await load(true);
  }catch(error){el('managed-log-count').textContent=error.message;}
  finally{button.disabled=false;}
 });
 const statusNames={cleared:'پاک شد',not_present:'وجود ندارد',blocked_shared:'باقی مانده: journal مشترک؛ حذف انتخابی امن در دسترس نیست',
  unavailable:'در دسترس نیست؛ نصب/دسترسی را بررسی کنید',failed:'ناموفق',unsupported:'این مسیر سفارشی پشتیبانی نمی‌شود'};
 const sourceNames={database:'دیتابیس',retry_spool:'صف دیسکی',application_files:'فایل‌های برنامه و چرخشی',installer_file:'لاگ نصب',
  namespace_journal:'journal اختصاصی Sonar',shared_journal:'journal قدیمی مشترک',local_files_and_journals:'فایل‌ها و journal مرکز',
  agent_files:'فایل Agent',legacy_agent_file:'فایل Agent قدیمی',agent_home_files:'لاگ خانه سفارشی Agent',custom_log_directory:'مسیر سفارشی لاگ'};
 function renderPurge(job){
  const host=el('managed-log-purge-result');host.replaceChildren();
  host.append(text('strong',`شناسه عملیات: ${job.id} · ${job.state}`));
  host.append(text('p',job.include_remote?'محدوده: مرکز و Agentهای همه نودهای ذخیره‌شده':'محدوده: مرکز؛ حذف لاگ نودها درخواست نشده است.'));
  for(const [key,value] of Object.entries(job.result?.sources||{}))host.append(text('p',`${sourceNames[key]||key}: ${statusNames[value.status]||value.status}`));
  for(const [id,value] of Object.entries(job.result?.remote||{})){
   host.append(text('strong',`نود #${id}: ${value.ok?'پاک‌سازی کامل منابع مجاز':'بعضی منابع باقی مانده‌اند'}`));
   if(value.sources)for(const [key,row] of Object.entries(value.sources))host.append(text('p',`${sourceNames[key]||key}: ${statusNames[row.status]||row.status}`));
   else host.append(text('p',value.error||value.status||'در دسترس نیست'));
  }
  if(job.state==='partial')host.append(text('p','پاک‌سازی ناقص است؛ موارد باقی‌مانده بالا مشخص شده‌اند.'));
 }
 el('managed-log-purge-all')?.addEventListener('click',async()=>{
  const remote=el('managed-log-remote').checked;
  if(!await window.SonarUI.confirm(`لاگ‌های دیتابیس، فایل‌ها، نصب و journal اختصاصی Sonar${remote?' و Agentهای همه نودهای ذخیره‌شده':''} حذف شوند؟ حذف میان چند منبع تراکنش مشترک ندارد؛ journal مشترک سرویس‌های دیگر پاک نمی‌شود.`))return;
  if(await window.SonarUI.ask('برای تأیید نهایی DELETE ALL LOGS را وارد کنید.')!=='DELETE ALL LOGS')return;
  const button=el('managed-log-purge-all');button.disabled=true;
  try{
   const created=await window.SonarWebRequest('/api/admin/logs/purge',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({confirmation:'DELETE ALL LOGS',include_remote:remote})});
   let job;
   do{
    job=await window.SonarWebRequest('/api/admin/logs/purge/'+created.id);renderPurge(job);
    if(['pending','running'].includes(job.state))await new Promise(resolve=>setTimeout(resolve,1000));
   }while(['pending','running'].includes(job.state));
   revision++;window.dispatchEvent(new Event('sonar:logs-cleared'));await load(true);
  }catch(error){el('managed-log-purge-result').replaceChildren(text('p',error.message));}
  finally{button.disabled=false;}
 });
 return {load};
})();
