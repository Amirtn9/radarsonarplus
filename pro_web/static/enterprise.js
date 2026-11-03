 'use strict';
(()=>{
 const $=id=>document.getElementById(id);
 const get=(path)=>window.SonarWebRequest(path);
 const post=(path,data)=>window.SonarWebPost(path,data);
 const call=(path,method='POST',data={})=>window.SonarWebRequest(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;};
 const btn=(label,fn,cls='')=>{const b=el('button',label,cls);b.type='button';b.addEventListener('click',fn);return b;};
 const line=(host,text)=>host.append(el('p',text));
 async function health(){
  const host=$('enterprise-health');if(!host)return;host.textContent='در حال دریافت...';
  try{const d=await get('/api/enterprise/health');host.replaceChildren();
   (d.servers||[]).forEach(s=>line(host,`${s.name} · ${s.state==='unknown'?'نامشخص':s.state} · ${s.score===null?'—':s.score+'/100'} · ${s.stale?'داده قدیمی':''}`));
   if(!d.servers?.length)host.textContent='هنوز سروری ثبت نشده است.';
  }catch(e){host.textContent=e.message;}
 }
 async function showOperation(id){
  const host=$('enterprise-operation-detail');if(!host)return;host.textContent='در حال دریافت جزئیات...';
  try{const d=await get(`/api/enterprise/operations/${encodeURIComponent(id)}`);host.replaceChildren();
   const o=d.operation;line(host,`عملیات ${o.id} · ${o.kind} · ${o.status}`);
   (d.items||[]).forEach(x=>line(host,`سرور ${x.resource_id} ${x.name||''} · ${x.status} · تلاش ${x.attempts} · ${x.detail||x.last_error||''}`));
  }catch(e){host.textContent=e.message;}
 }
 async function operations(){
  const host=$('enterprise-operations');if(!host)return;
  try{const rows=await get('/api/enterprise/operations');host.replaceChildren();
   (rows||[]).forEach(o=>{
    const wrap=el('div');wrap.className='listitem';
    line(wrap,`${o.id} · ${o.kind} · ${o.status} · ${o.succeeded}/${o.total} موفق · ${o.failed} ناموفق`);
    wrap.append(btn('جزئیات',()=>showOperation(o.id)));
    if(['queued','running'].includes(o.status))wrap.append(btn('توقف موقت',async()=>{try{await post(`/api/enterprise/operations/${o.id}/control`,{action:'pause'});await operations();}catch(e){alert(e.message);}}));
    if(o.status==='paused')wrap.append(btn('ادامه',async()=>{try{await post(`/api/enterprise/operations/${o.id}/control`,{action:'resume'});await operations();}catch(e){alert(e.message);}}));
    if(['queued','running','paused'].includes(o.status))wrap.append(btn('لغو',async()=>{if(!confirm('عملیات لغو شود؟'))return;try{await post(`/api/enterprise/operations/${o.id}/control`,{action:'cancel'});await operations();}catch(e){alert(e.message);}}));
    if(['failed','cancelled'].includes(o.status))wrap.append(btn('تلاش مجدد',async()=>{try{await post(`/api/enterprise/operations/${o.id}/control`,{action:'retry'});await operations();}catch(e){alert(e.message);}}));
    host.append(wrap);
   });
   if(!rows?.length)host.textContent='عملیاتی ثبت نشده است.';
  }catch(e){host.textContent=e.message;}
 }
 async function analytics(){
  const host=$('enterprise-analytics');if(!host)return;
  try{const d=await get('/api/enterprise/analytics?days=30');host.replaceChildren();
   const rows=[['رخدادها',d.incidents?.incidents],['رخدادهای رفع‌شده',d.incidents?.resolved],['میانگین زمان رفع (ساعت)',Number(d.incidents?.mean_resolution_hours||0).toFixed(1)],['کل عملیات',d.operations?.total],['عملیات موفق',d.operations?.succeeded],['عملیات ناموفق',d.operations?.failed],['میانگین CPU',Number(d.resources?.avg_cpu||0).toFixed(1)],['میانگین RAM',Number(d.resources?.avg_ram||0).toFixed(1)],['میانگین Disk',Number(d.resources?.avg_disk||0).toFixed(1)]];
   rows.forEach(([k,v])=>line(host,`${k}: ${v??'—'}`));
  }catch(e){host.textContent=e.message;}
 }
 async function sourceInfo(){
  const host=$('enterprise-agent-source');if(!host)return;
  try{const d=await get('/api/enterprise/agent/source');host.textContent=`نسخه فعلی Backend: ${d.version}. ثبت Release فقط با Artifact امضاشده Ed25519 انجام می‌شود. ${d.release_mode||''}`;}
  catch(e){host.textContent=e.message;}
 }
 async function rollouts(){
  const host=$('enterprise-agent-rollouts');if(!host)return;
  try{const rows=await get('/api/enterprise/agent/rollouts');host.replaceChildren();
   (rows||[]).forEach(r=>{
    const wrap=el('div');wrap.className='listitem';
    line(wrap,`#${r.id} · ${r.version} · ${r.state} · ${r.batch_offset}/${r.server_ids.length} · نسخه قبلی: ${r.previous_release_id||'ندارد'}`);
    if(['queued','running'].includes(r.state))wrap.append(btn('توقف',async()=>{try{await post(`/api/enterprise/agent/rollouts/${r.id}/control`,{action:'pause'});await rollouts();}catch(e){alert(e.message);}}));
    if(r.state==='paused')wrap.append(btn('ادامه',async()=>{try{await post(`/api/enterprise/agent/rollouts/${r.id}/control`,{action:'resume'});await rollouts();}catch(e){alert(e.message);}}));
    if(['queued','running','paused'].includes(r.state))wrap.append(btn('لغو',async()=>{if(!confirm('انتشار لغو شود؟'))return;try{await post(`/api/enterprise/agent/rollouts/${r.id}/control`,{action:'cancel'});await rollouts();}catch(e){alert(e.message);}}));
    if(['completed','failed'].includes(r.state)&&r.previous_release_id)wrap.append(btn('Rollback گروهی',async()=>{if(!confirm('Agent سرورهای موفق به Release قبلی برگردند؟'))return;try{await post(`/api/enterprise/agent/rollouts/${r.id}/control`,{action:'rollback'});await rollouts();}catch(e){alert(e.message);}}));
    host.append(wrap);
   });
   if(!rows?.length)host.textContent='انتشاری ثبت نشده است.';
  }catch(e){host.textContent=e.message;}
 }
 async function incidents(){
  const host=$('enterprise-incidents');if(!host)return;
  try{const rows=await get(`/api/enterprise/incidents?status=${encodeURIComponent($('enterprise-incident-filter')?.value||'open')}`);host.replaceChildren();
   (rows||[]).forEach(r=>{const wrap=el('div');wrap.className='listitem';line(wrap,`#${r.id} · ${r.severity} · ${r.status} · ${r.title} · تکرار ${r.occurrences}`);
    wrap.append(btn('جزئیات',async()=>{try{const d=await get(`/api/enterprise/incidents/${r.id}`);const detail=$('enterprise-incidents');const panel=el('pre',JSON.stringify(d,null,2));panel.style.whiteSpace='pre-wrap';detail.append(panel);}catch(e){alert(e.message);}}));
    if(r.status==='open')wrap.append(btn('تأیید',async()=>{try{await post(`/api/enterprise/incidents/${r.id}/state`,{status:'acknowledged'});await incidents();}catch(e){alert(e.message);}}));
    if(['open','acknowledged'].includes(r.status))wrap.append(btn('بررسی',async()=>{try{await post(`/api/enterprise/incidents/${r.id}/state`,{status:'investigating'});await incidents();}catch(e){alert(e.message);}}));
    wrap.append(btn('یادداشت',async()=>{const note=prompt('یادداشت رخداد:');if(!note)return;try{await post(`/api/enterprise/incidents/${r.id}/notes`,{note});alert('یادداشت ثبت شد.');}catch(e){alert(e.message);}}));
    if(r.status!=='resolved')wrap.append(btn('رفع شد',async()=>{try{await post(`/api/enterprise/incidents/${r.id}/state`,{status:'resolved'});await incidents();}catch(e){alert(e.message);}}));
    host.append(wrap);
   });if(!rows?.length)host.textContent='رخدادی پیدا نشد.';
  }catch(e){host.textContent=e.message;}
 }
 async function healthPolicy(loadOnly=true){
  const host=$('enterprise-health-policy-form');if(!host)return;
  try{
   if(loadOnly){const d=await get('/api/enterprise/health/policy');for(const k of ['agent','cpu','ram','disk'])$(`health-weight-${k}`).value=d.weights[k];for(const k of ['cpu','ram','disk'])$(`health-threshold-${k}`).value=d.thresholds[k];$('health-stale-seconds').value=d.stale_seconds;}
   else{
    const weights=Object.fromEntries(['agent','cpu','ram','disk'].map(k=>[k,Number($(`health-weight-${k}`).value)]));
    const thresholds=Object.fromEntries(['cpu','ram','disk'].map(k=>[k,Number($(`health-threshold-${k}`).value)]));
    if(Object.values(weights).reduce((a,b)=>a+b,0)!==100)throw Error('مجموع وزن‌ها باید دقیقاً ۱۰۰ باشد.');
    await post('/api/enterprise/health/policy',{weights,thresholds,stale_seconds:Number($('health-stale-seconds').value)});
    alert('سیاست Health ذخیره شد.');await health();
   }
  }catch(e){if(loadOnly)console.warn(e);else alert(e.message);}
 }
 async function backups(){
  const host=$('enterprise-backups');if(!host)return;
  try{const rows=await get('/api/enterprise/backups/schedules');host.replaceChildren();
   (rows||[]).forEach(r=>{const wrap=el('div');wrap.className='listitem';line(wrap,`#${r.id} · ${r.name} · ${r.cadence} · ${r.enabled?'فعال':'متوقف'} · بعدی: ${r.next_run_at||'—'}`);
    wrap.append(btn('ویرایش',async()=>{const destination=prompt('مقصد جدید (مسیر محلی یا s3://bucket/prefix):',r.destination);if(destination===null)return;const cadence=prompt('تناوب: daily یا weekly',r.cadence);if(cadence===null)return;const retention=prompt('تعداد نسخه برای نگهداری (1 تا 90):',String(r.retention_count));if(retention===null)return;try{await call(`/api/enterprise/backups/schedules/${r.id}`,'PUT',{destination,cadence,retention:Number(retention)});await backups();}catch(e){alert(e.message);}}));
    wrap.append(btn('اجرای فوری',async()=>{try{await post(`/api/enterprise/backups/schedules/${r.id}/control`,{action:'run_now'});await backups();}catch(e){alert(e.message);}}));
    wrap.append(btn(r.enabled?'توقف':'فعال‌سازی',async()=>{try{await post(`/api/enterprise/backups/schedules/${r.id}/control`,{action:r.enabled?'disable':'enable'});await backups();}catch(e){alert(e.message);}}));
    wrap.append(btn('تاریخچه',async()=>{try{const d=await get(`/api/enterprise/backups/schedules/${r.id}/runs`);const panel=el('pre',JSON.stringify(d,null,2));panel.style.whiteSpace='pre-wrap';host.append(panel);}catch(e){alert(e.message);}}));
    wrap.append(btn('حذف',async()=>{if(!confirm('زمان‌بندی حذف شود؟'))return;try{await post(`/api/enterprise/backups/schedules/${r.id}/control`,{action:'delete'});await backups();}catch(e){alert(e.message);}}));
    host.append(wrap);
   });if(!rows?.length)host.textContent='زمان‌بندی ثبت نشده است.';
  }catch(e){host.textContent=e.message;}
 }
 async function webhooks(){
  const host=$('enterprise-webhooks');if(!host)return;
  try{const rows=await get('/api/enterprise/webhooks');host.replaceChildren();
   (rows||[]).forEach(r=>{const wrap=el('div');wrap.className='listitem';line(wrap,`#${r.id} · ${r.url} · ${r.enabled?'فعال':'متوقف'} · ${r.events.join(', ')}`);
    wrap.append(btn('ویرایش مقصد/رویداد',async()=>{const url=prompt('HTTPS URL جدید:',r.url);if(url===null)return;const events=prompt('رویدادها با کاما: incident.opened,incident.resolved,operation.completed,server.offline',r.events.join(','));if(events===null)return;try{await call(`/api/enterprise/webhooks/${r.id}`,'PUT',{url,events:events.split(',').map(x=>x.trim()).filter(Boolean)});await webhooks();}catch(e){alert(e.message);}}));
    wrap.append(btn('تحویل‌ها',async()=>{try{const d=await get(`/api/enterprise/webhooks/${r.id}/deliveries`);const out=$('enterprise-webhook-deliveries');out.replaceChildren();(d||[]).forEach(x=>{const row=el('div');line(row,`#${x.id} · ${x.event_key} · تلاش ${x.attempts} · ${x.delivered_at?'ارسال شد':x.dead_at?'Dead Letter':'در انتظار'} · ${x.last_error||''}`);if(x.dead_at)row.append(btn('تلاش مجدد',async()=>{try{await post(`/api/enterprise/webhooks/${r.id}/retry`,{delivery_id:x.id});await webhooks();}catch(e){alert(e.message);}}));out.append(row);});}catch(e){alert(e.message);}}));
    wrap.append(btn(r.enabled?'توقف':'فعال‌سازی',async()=>{try{await call(`/api/enterprise/webhooks/${r.id}`,'PUT',{enabled:!r.enabled});await webhooks();}catch(e){alert(e.message);}}));
    wrap.append(btn('چرخش Secret',async()=>{try{const d=await call(`/api/enterprise/webhooks/${r.id}`,'PUT',{rotate_secret:true});alert(`Secret جدید را ذخیره کنید:\n${d.secret}`);}catch(e){alert(e.message);}}));
    wrap.append(btn('حذف',async()=>{if(!confirm('Webhook حذف شود؟'))return;try{await call(`/api/enterprise/webhooks/${r.id}`,'DELETE');await webhooks();}catch(e){alert(e.message);}}));
    host.append(wrap);
   });if(!rows?.length)host.textContent='Webhook ثبت نشده است.';
  }catch(e){host.textContent=e.message;}
 }
 async function tokens(){
  const host=$('enterprise-tokens');if(!host)return;
  try{const rows=await get('/api/enterprise/tokens');host.replaceChildren();
   (rows||[]).forEach(r=>{const wrap=el('div');wrap.className='listitem';line(wrap,`#${r.id} · ${r.label} · ${r.scopes.join(', ')} · انقضا: ${r.expires_at||'بدون انقضا'} · ${r.revoked_at?'لغوشده':'فعال'}`);
    if(!r.revoked_at)wrap.append(btn('لغو Token',async()=>{if(!confirm('Token لغو شود؟'))return;try{await call(`/api/enterprise/tokens/${r.id}`,'DELETE');await tokens();}catch(e){alert(e.message);}}));
    host.append(wrap);
   });if(!rows?.length)host.textContent='Token ثبت نشده است.';
  }catch(e){host.textContent=e.message;}
 }
 async function load(){await Promise.all([health(),operations(),analytics(),rollouts(),sourceInfo(),incidents(),healthPolicy(true),backups(),webhooks(),tokens()]);}
 document.addEventListener('DOMContentLoaded',()=>{
  $('enterprise-health-refresh')?.addEventListener('click',health);
  $('enterprise-analytics-refresh')?.addEventListener('click',analytics);
  $('enterprise-incidents-refresh')?.addEventListener('click',incidents);
  $('enterprise-incident-filter')?.addEventListener('change',incidents);
  $('enterprise-backups-refresh')?.addEventListener('click',backups);
  $('enterprise-webhooks-refresh')?.addEventListener('click',webhooks);
  $('enterprise-tokens-refresh')?.addEventListener('click',tokens);
  $('enterprise-token-form')?.addEventListener('submit',async e=>{e.preventDefault();const scopes=$('enterprise-token-scopes').value.split(',').map(x=>x.trim()).filter(Boolean);const days=$('enterprise-token-expiry').value;try{const r=await post('/api/enterprise/tokens',{label:$('enterprise-token-label').value,scopes,expires_in_days:days?Number(days):null});$('enterprise-token-created').textContent=`Token جدید (فقط همین یک بار نمایش داده می‌شود): ${r.token}`;await tokens();}catch(err){$('enterprise-token-created').textContent=err.message;}});
  $('enterprise-metrics-form')?.addEventListener('submit',async e=>{e.preventDefault();const host=$('enterprise-metrics');host.textContent='در حال دریافت...';try{const id=Number($('enterprise-metrics-server-id').value),days=Number($('enterprise-metrics-days').value);const d=await get(`/api/enterprise/servers/${id}/metrics?days=${days}`);host.replaceChildren();(d.samples||[]).forEach(x=>line(host,`${x.created_at} · CPU ${x.cpu??'—'}% · RAM ${x.ram??'—'}% · Disk ${x.disk??'—'}% · Traffic ${x.traffic_gb??'—'} GB`));if(!d.samples?.length)host.textContent='نمونه‌ای در این بازه وجود ندارد.';}catch(err){host.textContent=err.message;}});

  $('enterprise-health-policy-form')?.addEventListener('submit',e=>{e.preventDefault();healthPolicy(false);});
  $('enterprise-operation-form')?.addEventListener('submit',async e=>{
   e.preventDefault();const ids=$('enterprise-server-ids').value.split(',').map(x=>Number(x.trim()));
   if(!ids.length||ids.some(x=>!Number.isSafeInteger(x)||x<1)){alert('شناسه سرورها نامعتبر است');return;}
   try{await post('/api/enterprise/operations',{kind:$('enterprise-operation-kind').value,service:$('enterprise-operation-service')?.value||'sonar-agent',server_ids:ids,idempotency_key:`web-${Date.now()}-${Math.random().toString(36).slice(2,10)}`});await operations();}catch(err){alert(err.message);}
  });
  $('enterprise-backup-form')?.addEventListener('submit',async e=>{e.preventDefault();try{await post('/api/enterprise/backups/schedules',{name:$('enterprise-backup-name').value,cadence:$('enterprise-backup-cadence').value,destination:$('enterprise-backup-destination').value,retention:Number($('enterprise-backup-retention').value)});await backups();alert('زمان‌بندی ثبت شد.');}catch(err){alert(err.message);}});
  $('enterprise-webhook-form')?.addEventListener('submit',async e=>{e.preventDefault();const host=$('enterprise-webhook-result');try{const r=await post('/api/enterprise/webhooks',{url:$('enterprise-webhook-url').value,events:[$('enterprise-webhook-event').value]});host.textContent=`Webhook #${r.id} ساخته شد. Secret را همین حالا ذخیره کنید: ${r.secret}`;await webhooks();}catch(err){host.textContent=err.message;}});
  $('enterprise-agent-release-form')?.addEventListener('submit',async e=>{
   e.preventDefault();const file=$('enterprise-agent-file')?.files?.[0];if(!file){alert('فایل Agent را انتخاب کنید.');return;}
   if(file.size>2_000_000){alert('حداکثر حجم Artifact دو مگابایت است.');return;}
   try{
    const buffer=await file.arrayBuffer();const digest=await crypto.subtle.digest('SHA-256',buffer);
    const hash=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
    const bytes=new Uint8Array(buffer);let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));
    const artifact_base64=btoa(binary);
    const r=await post('/api/enterprise/agent/releases',{version:$('enterprise-agent-version').value,sha256:hash,artifact_base64,signature:$('enterprise-agent-signature').value.trim(),channel:$('enterprise-agent-channel').value});
    $('enterprise-agent-release-id').value=r.id;$('enterprise-agent-release-result').textContent=`Release #${r.id} ثبت شد. SHA-256: ${r.sha256}`;alert('امضا و هش Artifact تأیید شد.');
   }catch(err){$('enterprise-agent-release-result').textContent=err.message;}
  });
  $('enterprise-agent-rollout-form')?.addEventListener('submit',async e=>{e.preventDefault();const ids=$('enterprise-agent-server-ids').value.split(',').map(x=>Number(x.trim()));if(!ids.length||ids.some(x=>!Number.isSafeInteger(x)||x<1)){alert('شناسه سرورها نامعتبر است');return;}try{await post('/api/enterprise/agent/rollouts',{release_id:Number($('enterprise-agent-release-id').value),server_ids:ids,batch_size:Number($('enterprise-agent-batch').value),canary_count:Number($('enterprise-agent-canary').value)});await rollouts();}catch(err){alert(err.message);}});
 });
 window.SonarEnterprise={load,health,operations,analytics,rollouts,incidents,backups,webhooks,tokens};
})();