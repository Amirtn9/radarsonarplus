'use strict';
(()=>{
 const el=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
 const roles={manager:'مدیر',technical:'مدیر فنی',monitor:'اپراتور پایش',server:'اپراتور سرور',viewer:'ناظر',auditor:'ممیز'};
 const button=(text,fn)=>{const b=el('button',text);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){toast(e.message);}finally{b.disabled=false;}};return b;};
 const panel=(parent,id,title)=>{document.getElementById(id)?.remove();const p=el('section');p.className='panel';p.id=id;p.append(el('h2',title));parent.append(p);return p;};
 const input=(parent,label,type='text')=>{const l=el('label',label),i=el('input');i.type=type;i.required=true;l.append(i);parent.append(l);return i;};
 const select=(parent,label,options)=>{const l=el('label',label),s=el('select');for(const [value,text] of options){const o=el('option',text);o.value=value;s.append(o);}l.append(s);parent.append(l);return s;};
 async function team(){
  const root=document.getElementById('team');if(!root)return;document.getElementById('team-form')?.setAttribute('hidden','');
  const p=panel(root,'control-team','دعوت و دسترسی تیم');p.append(el('p','دعوت فقط با تأیید گیرنده فعال می‌شود. منابع شخصی اعضا ادغام یا جابه‌جا نمی‌شوند.'));
  const f=el('form');f.className='billing-form';const member=input(f,'شناسه عددی تلگرام عضو','number'),role=select(f,'نقش',Object.entries(roles));const submit=el('button','ارسال دعوت');submit.type='submit';f.append(submit);p.append(f);
  f.onsubmit=async e=>{e.preventDefault();submit.disabled=true;try{await jsonPost('/api/control/invites',{member_id:Number(member.value),role:role.value});toast('دعوت ثبت شد؛ گیرنده باید در حساب خود تأیید کند.');await team();}catch(error){toast(error.message);}finally{submit.disabled=false;}};
  const me=await request('/api/me');for(const invite of await request('/api/control/invites')){const line=el('article',`دعوت ${invite.owner_id} ← ${invite.member_id} · ${roles[invite.role]}`);line.className='item';if(invite.member_id===me.id)for(const [yes,title] of [[true,'پذیرش'],[false,'رد']])line.append(button(title,async()=>{await jsonPost(`/api/control/invites/${invite.id}/decision`,{accept:yes});await team();await workspaces();}));p.append(line);}
  for(const item of await request('/api/team')){const line=el('article',`عضو ${item.member_id} · ${roles[item.role]||item.role}`);line.className='item';const options=select(line,'نقش',Object.entries({...roles,remove:'حذف دسترسی'}));options.value=item.role;line.append(button('اعمال',async()=>{await request('/api/control/team/'+item.member_id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:options.value})});await team();}));p.append(line);}
 }
 async function policy(){
  const root=document.getElementById('settings'),p=panel(root,'control-policy','فاصله نمونه‌برداری و آستانه منابع سرورها');
  const values=await request('/api/control/monitor-policy'),form=el('form');form.className='billing-form';p.append(form);
  const fields={interval_seconds:'فاصله نمونه‌برداری (ثانیه؛ حداقل ۶۰)',cpu_threshold:'آستانه CPU (درصد)',ram_threshold:'آستانه RAM (درصد)',disk_threshold:'آستانه دیسک (درصد)',cooldown_seconds:'فاصله تکرار هشدار منابع (ثانیه)'};
  const inputs={};for(const [key,label] of Object.entries(fields)){inputs[key]=input(form,label,'number');inputs[key].value=values[key];}
  const submit=el('button','ذخیره سیاست پایش');submit.type='submit';form.append(submit);form.onsubmit=async e=>{e.preventDefault();try{const body={};for(const key of Object.keys(fields))body[key]=Number(inputs[key].value);await request('/api/control/monitor-policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('از چرخه بعدی پایش اعمال می‌شود؛ راه‌اندازی مجدد لازم نیست.');}catch(error){toast(error.message);}};
 }
 async function serverActions(){
  const sid=window.SonarConsole?.currentServerId?.();if(!sid)return;
  const p=panel(document.getElementById('server-detail'),'control-server','حالت تعمیرات سرور');
  p.append(el('p','در حالت تعمیرات، داده و تغییر وضعیت ثبت می‌شوند و هشدار منابع و قطعی ارسال نمی‌شود.'));
  p.append(button('چرخش توکن VPS و بازنصب امن Agent',async()=>{if(!confirm('Agent با SSH تأییدشده بازنصب و دوباره راه‌اندازی می‌شود. توکن فقط پس از آزمون موفق WSS ثبت می‌شود. ادامه؟'))return;await jsonPost('/api/operations/server/'+sid+'/agent/rotate-token',{confirmed:true});toast('در صف بازنصب امن قرار گرفت؛ نتیجه را در سابقه نصب بررسی کنید.');}));
  for(const [minutes,title] of [[30,'توقف هشدار برای ۳۰ دقیقه'],[0,'پایان تعمیرات']])p.append(button(title,async()=>{const reason=prompt('دلیل تغییر حالت تعمیرات:');if(!reason)return;await jsonPost('/api/control/servers/'+sid+'/maintenance',{minutes,reason});toast('حالت تعمیرات ذخیره شد');}));
 }
 async function sessions(){
  const root=document.getElementById('settings'),p=panel(root,'control-sessions','نشست‌ها و دستگاه‌های فعال');
  for(const session of await request('/api/control/sessions')){const line=el('article',`${session.current?'این دستگاه · ':''}${session.client_label||'مرورگر'} · آخرین فعالیت ${new Date(session.last_seen_at).toLocaleString('fa-IR')}`);line.className='item';line.append(button('خروج این دستگاه',async()=>{if(!confirm('نشست این دستگاه باطل شود؟'))return;await request('/api/control/sessions/'+session.id,{method:'DELETE'});if(session.current)location.assign('/login');else await sessions();}));p.append(line);}
 }
 async function workspaces(){
  const options=await request('/api/control/workspaces'),me=await request('/api/me');let selectBox=document.getElementById('workspace-selector');if(!selectBox){selectBox=el('select');selectBox.id='workspace-selector';selectBox.setAttribute('aria-label','انتخاب فضای کاری');document.getElementById('navigation').before(selectBox);}selectBox.replaceChildren();
  const personal=el('option','فضای کاری پیش‌فرض');personal.value='';selectBox.append(personal);for(const item of options){const o=el('option',`تیم ${item.owner_id} · ${roles[item.role]||item.role}`);o.value=String(item.owner_id);selectBox.append(o);}selectBox.value=String(activeWorkspace||'');if(selectBox.selectedIndex<0){activeWorkspace='';selectBox.value='';}
  selectBox.onchange=()=>{activeWorkspace=selectBox.value;state.selectedConfigs.clear();try{sessionStorage.setItem('sonar-workspace',activeWorkspace);}catch{}window.SonarLive?.connect();tabClick(state.tab);};
 }
 async function notifications(){
  const root=document.getElementById('channels'),p=panel(root,'control-destinations','مدیریت مقصدهای اطلاع‌رسانی');
  const categories={all:'همه',servers:'سرورها',configs:'کانفیگ‌ها',monitoring:'پایش وب‌سایت',agents:'Agent',alerts:'هشدار بحرانی',team:'تیم',system:'سیستم',report:'گزارش‌ها'};
  const form=el('form');form.className='billing-form';const name=input(form,'نام مقصد'),chat=input(form,'شناسه عددی گروه یا کانال','text'),topic=input(form,'شناسه موضوع (صفر: بدون موضوع)','number');topic.value='0';const checks=el('fieldset');checks.append(el('legend','دسته‌های قابل ارسال'));const selected={};for(const [key,title] of Object.entries(categories)){const l=el('label',title),c=el('input');c.type='checkbox';c.checked=key==='all';l.append(c);checks.append(l);selected[key]=c;}form.append(checks);const submit=el('button','افزودن و بررسی دسترسی ربات');submit.type='submit';form.append(submit);p.append(form);
  form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;try{await jsonPost('/api/notifications/destinations',{name:name.value,chat_id:Number(chat.value),topic_id:Number(topic.value),categories:Object.keys(selected).filter(key=>selected[key].checked),enabled:true});toast('مقصد با مجوز ارسال ثبت شد');await notifications();}catch(error){toast(error.message);}finally{submit.disabled=false;}};
  for(const d of await request('/api/notifications/destinations')){const line=el('article',`${d.name} · ${d.chat_id} · ${(d.categories||[d.usage_type]).map(x=>categories[x]||x).join('، ')} · ${d.enabled?'فعال':'غیرفعال'}`);line.className='item';line.append(button('پیام آزمایشی',()=>jsonPost(`/api/notifications/destinations/${d.id}/test`,{})),button(d.enabled?'غیرفعال':'فعال',async()=>{await request('/api/notifications/destinations/'+d.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:d.name,chat_id:Number(d.chat_id),topic_id:d.topic_id,categories:d.categories||['all'],enabled:!d.enabled})});await notifications();}),button('حذف مقصد',async()=>{if(!confirm('این مقصد حذف شود؟'))return;await request('/api/notifications/destinations/'+d.id,{method:'DELETE'});await notifications();}));p.append(line);}
  const pending=panel(root,'control-outbox','صف اعلان و تلاش دوباره');for(const entry of await request('/api/notifications/outbox')){const line=el('article',`اعلان ${entry.id} · ${entry.event_type} · ${entry.sent_at?'ارسال‌شده':entry.dead_at?'ناموفق':'در انتظار'} · تلاش ${entry.attempts}`);line.className='item';if(entry.dead_at)line.append(button('ارسال مجدد',async()=>{await jsonPost(`/api/notifications/outbox/${entry.id}/retry`,{});await notifications();}));pending.append(line);}
  const history=panel(root,'control-deliveries','تاریخچه تحویل به مقصدها');for(const entry of await request('/api/notifications/history'))history.append(el('p',`مقصد ${entry.chat_id} · ${entry.sent_at?'تحویل‌شده':entry.last_error||'در انتظار'} · تلاش ${entry.attempts}`));
 }
 async function regions(service){
  const host=document.getElementById('service-history'),box=panel(host,'service-regions','مقایسه ایران و خارج');
  const report=await request(`/api/services/${service.id}/regions?days=7`);for(const summary of report.summary){const region=summary.region==='ir'?'ایران':summary.region==='ext'?'خارج':'مرکزی';box.append(el('h3',region));box.append(el('p',`اندازه‌گیری معتبر: ${summary.measured} · نامشخص: ${summary.unknown} · دسترس‌پذیری نمونه‌ها: ${summary.sample_availability==null?'نامشخص':Number(summary.sample_availability).toFixed(2)+'٪'} · میانگین ${summary.average_ms==null?'—':Number(summary.average_ms).toFixed(1)+' ms'}`));}
  for(const r of report.history.slice(0,18)){const diag=r.diagnostics||{};box.append(el('p',`${r.region==='ir'?'ایران':r.region==='ext'?'خارج':'مرکزی'} · ${r.ok===null?'اندازه‌گیری موجود نیست':r.ok?'سالم':'ناموفق'} · ${r.detail} · ${new Date(r.checked_at).toLocaleString('fa-IR')}${diag.http_status?' · HTTP '+diag.http_status:''}${diag.dns_ms!=null?' · DNS '+diag.dns_ms+' ms':''}${diag.tls_expires_at?' · انقضای TLS '+new Date(diag.tls_expires_at*1000).toLocaleDateString('fa-IR'):''}`));}
 }
 async function agents(){
  const p=panel(document.getElementById('fleet'),'hosting-agents','Agent سبک هاست PHP');
  p.append(el('p','این Agent فقط مصرف فرایند PHP و فضای فایل‌سیستم قابل دسترسی را گزارش می‌دهد؛ دسترسی کامل CPU، RAM سرور، SSH یا اجرای فرمان ندارد. فایل را ترجیحاً خارج از public_html با cron هر یک دقیقه اجرا کنید. پس از چرخش توکن فایل را دوباره دریافت کنید.'));
  const form=el('form'),name=input(form,'نام هاست'),create=el('button','ایجاد Agent اختصاصی');create.type='submit';form.append(create);p.append(form);
  form.onsubmit=async e=>{e.preventDefault();try{await jsonPost('/api/hosting/agents',{name:name.value});await agents();}catch(error){toast(error.message);}};
  for(const agent of await request('/api/hosting/agents')){const line=el('article',`${agent.name} · ${agent.revoked_at?'باطل‌شده':agent.last_seen_at?'آخرین ارتباط '+new Date(agent.last_seen_at).toLocaleString('fa-IR'):'هنوز متصل نشده'}`);line.className='item';
   if(agent.latest)line.append(el('p',`نسخه PHP ${agent.latest.php_version} · حافظه فرایند ${(agent.latest.memory_bytes/1048576).toFixed(1)} MiB · فضای آزاد ${agent.latest.disk_free_bytes==null?'نامشخص':(agent.latest.disk_free_bytes/1073741824).toFixed(2)+' GiB'}`));
   if(!agent.revoked_at)line.append(button('دریافت فایل PHP',async()=>{const response=await request(`/api/hosting/agents/${agent.id}/download`,{raw:true});const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='sonar-hosting-agent.php';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}),button('ابطال توکن',async()=>{if(!confirm('ارتباط این Agent باطل شود؟'))return;await request('/api/hosting/agents/'+agent.id,{method:'DELETE'});await agents();}));
   line.append(button('چرخش توکن',async()=>{if(!confirm('فایل نصب فعلی از کار می‌افتد؛ توکن جدید تولید شود؟'))return;await jsonPost(`/api/hosting/agents/${agent.id}/rotate`,{});await agents();}));p.append(line);
  }
 }
 async function probe(ids){
  if(!ids.length)throw Error('ابتدا کانفیگ‌ها را انتخاب کنید');
  const requestKey=crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-');
  const result=await jsonPost('/api/config-probes',{config_ids:ids,request_key:requestKey});toast(result.length+' آزمون در صف قرار گرفت');return result;
 }
 let probeTimer=null;
 async function configActions(){
  if(probeTimer)clearTimeout(probeTimer);
  const cid=window.SonarConsole?.currentConfigId?.();if(!cid)return;
  const p=panel(document.getElementById('config-detail'),'control-config','آزمون عملیاتی کانفیگ');
  const info=await request('/api/console/configs/'+cid);
  p.append(button('ویرایش نام کانفیگ',async()=>{const name=prompt('نام جدید کانفیگ:',info.name);if(!name)return;await request('/api/operations/config/'+cid,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});await window.SonarConsole.loadConfig();await configActions();}));
  p.append(button('اجرای تست اتصال از Agent مجاز',async()=>{await probe([cid]);await configActions();}),button(info.monitoring_enabled?'توقف پایش خودکار':'فعال‌سازی پایش خودکار',async()=>{await request('/api/config-probes/'+cid+'/enabled',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!info.monitoring_enabled})});await configActions();}));
  const jobs=await request('/api/config-probes/'+cid);for(const job of jobs.slice(0,5))p.append(el('p',`آزمون ${job.id} · ${job.state==='pending'?'در صف':job.state==='running'?'در حال اجرا':job.state==='failed'?'اندازه‌گیری در دسترس نیست':job.result.status==='OK'?'اتصال موفق':'تست اتصال ناموفق'}${job.result.ping?' · '+job.result.ping+' ms':''}${job.error_code?' · '+job.error_code:''}`));
  if(jobs.some(j=>['pending','running'].includes(j.state)))probeTimer=setTimeout(()=>{if(state.tab==='config-detail'&&window.SonarConsole?.currentConfigId?.()===cid)configActions().catch(e=>toast(e.message));},3000);
 }
 window.SonarControl={team,sessions,workspaces,notifications,regions,agents,policy,serverActions,configActions,probe};
})();
