'use strict';
// Deliberately narrow operations; uses the existing authenticated API only.
(() => {
 const id=k=>document.getElementById(k);
 const pickServer=id('web-show-server'),pickConfig=id('web-show-config');
 if(!pickServer||!pickConfig)return;
 function show(which){
   id('web-server-form').hidden=which!=='server';id('web-config-form').hidden=which!=='config';
   id('web-ops-result').textContent='';tabClick('web-create');
 }
 pickServer.onclick=()=>show('server');pickConfig.onclick=()=>show('config');
 document.querySelectorAll('.quicklinks').forEach(x=>{
   if(x.querySelector('[data-go="servers"]')){
     const b=document.createElement('button');b.textContent='✚ افزودن سرور';b.type='button';b.onclick=()=>show('server');x.append(b);
   }
 });
 let scan=null;
 function resetScan(){scan=null;id('server-fingerprint-preview').value='';id('server-probe-status').textContent='آدرس یا پورت تغییر کرده؛ بررسی را دوباره اجرا کنید.';}
 ['new-server-ip','new-server-port'].forEach(k=>id(k).addEventListener('input',resetScan));
 id('server-probe').addEventListener('click',async e=>{
   const btn=e.currentTarget;btn.disabled=true;
   id('server-probe-status').textContent='در حال بررسی کلید SSH، ابتدا مستقیم، سپس Agent سالم…';
   try{
     const ip=id('new-server-ip').value.trim(),port=Number(id('new-server-port').value);
     const result=await jsonPost('/api/operations/server/probe',{ip,port});
     scan={ip,port,fingerprint:result.fingerprint};
     id('server-fingerprint-preview').value=result.fingerprint;
     id('server-probe-status').textContent=`کلید ${result.key_type} · مسیر ${result.route==='direct'?'مرکزی':'Agent #'+result.agent_id}. اثر انگشت را از کنسول مستقل تأیید کنید.`;
   }catch(err){resetScan();id('server-probe-status').textContent='خطا: '+err.message;}
   finally{btn.disabled=false;}
 });
 async function showInstallHistory(serverId){
   const host=id('web-server-install-history');if(!host)return;
   try{
     const jobs=await request(`/api/operations/server/${serverId}/install-jobs`);
     const map={pending:'در صف',running:'در حال نصب',success:'موفق و آزموده‌شده',failed:'ناموفق'};
     host.textContent=jobs.length?jobs.map(j=>`نصب #${j.id}: ${map[j.state]||j.state} · ${j.stage} ${j.error_code||''}`).join(' | '):'درخواست نصبی ثبت نشده است.';
   }catch(err){host.textContent=err.message;}
 }
 let installationTimer=null,installationServer=0;
 function watchInstall(serverId){installationServer=serverId;showInstallHistory(serverId);if(installationTimer)clearInterval(installationTimer);
   installationTimer=setInterval(()=>{if(installationServer===serverId)showInstallHistory(serverId);},10000);
 }
 id('web-server-form').addEventListener('submit',async e=>{
   e.preventDefault();const btn=e.submitter;
   const ip=id('new-server-ip').value.trim(),port=Number(id('new-server-port').value);
   const expected=id('new-server-fingerprint').value.trim();
   if(!scan||scan.ip!==ip||scan.port!==port){id('web-ops-result').textContent='ابتدا اتصال SSH را بررسی کنید.';return;}
   if(scan.fingerprint!==expected){id('web-ops-result').textContent='اثر انگشت با مقدار تأییدشده مطابقت ندارد. سرور ثبت نشد.';return;}
   if(btn)btn.disabled=true;id('web-ops-result').textContent='در حال احراز هویت SSH و ثبت ایمن…';
   const data={name:id('new-server-name').value,ip,location_type:id('new-server-location').value,
     port,username:id('new-server-user').value,password:id('new-server-password').value,
     ws_port:Number(id('new-server-ws').value),group_id:Number(id('new-server-group').value),expected_fingerprint:expected,
     install_agent:!!id('new-server-install-agent').checked};
   try{
     const v=await jsonPost('/api/operations/server',data);id('new-server-password').value='';
     id('web-ops-result').textContent=`سرور #${v.id} با SSH معتبر ثبت شد؛ مسیر: ${v.route==='direct'?'مرکزی':'Agent'}. `+(v.install_job_id?'نصب Agent به صف اضافه شد.':v.install_error?'سرور ثبت شد اما صف نصب در دسترس نبود؛ از جزئیات سرور دوباره اقدام کنید.':'نصب Agent درخواست نشده است.');
     if(v.install_job_id)watchInstall(v.id);
     resetScan(); id('new-server-fingerprint').value='';
   }catch(err){id('web-ops-result').textContent='ثبت ناموفق: '+err.message;}
   finally{if(btn)btn.disabled=false;data.password='';}
 });
 async function loadGroups(){
   const host=id('web-group-list');if(!host)return;
   try{const groups=await request('/api/operations/groups');host.replaceChildren();
     const select=id('new-server-group'),selected=select.value;
     select.replaceChildren();const no=document.createElement('option');no.value='0';no.textContent='بدون پوشه';select.append(no);
     groups.forEach(g=>{const o=document.createElement('option');o.value=String(g.id);o.textContent=g.name;select.append(o);});
     if([...select.options].some(o=>o.value===selected))select.value=selected;
     if(!groups.length)host.textContent='پوشه‌ای ایجاد نشده است.';
     groups.forEach(group=>{
       const row=document.createElement('div');row.className='item';
       const name=document.createElement('span');name.textContent=group.name;row.append(name);
       const del=document.createElement('button');del.type='button';del.textContent='حذف پوشه';
       del.onclick=async()=>{if(!confirm('پوشه حذف و سرورهای آن بدون پوشه شوند؟'))return;
         try{await request(`/api/operations/groups/${group.id}`,{method:'DELETE'});await loadGroups();}catch(err){toast(err.message);}};
       row.append(del);host.append(row);
     });
   }catch(err){host.textContent=err.message;}
 }
 id('web-group-form')?.addEventListener('submit',async e=>{
   e.preventDefault();const btn=e.submitter;if(btn)btn.disabled=true;
   try{await jsonPost('/api/operations/groups',{name:id('web-group-name').value.trim()});id('web-group-name').value='';await loadGroups();}
   catch(err){toast(err.message);}finally{if(btn)btn.disabled=false;}
 });
 id('web-show-server').addEventListener('click',loadGroups);
 id('web-show-config').addEventListener('click',loadGroups);
 function currentServer(){return Number(window.SonarConsole?.currentServerId?.()||0);}
 const serverStatus=id('web-server-actions-status');
 id('web-ssh-check')?.addEventListener('click',async e=>{
   const sid=currentServer();if(!sid)return;serverStatus.textContent='در حال بررسی SSH از سرور مرکزی یا Agent…';
   const btn=e.currentTarget;btn.disabled=true;
   try{const r=await jsonPost(`/api/operations/server/${sid}/check-ssh`,{});
     serverStatus.textContent=r.status==='Online'?'اتصال مدیریتی SSH برقرار است.':`SSH: ${r.error||'ناموفق'}`;
   }catch(err){serverStatus.textContent=err.message;}finally{btn.disabled=false;}
 });
 id('web-agent-install')?.addEventListener('click',async e=>{
   const sid=currentServer();if(!sid||!confirm('نصب/تعمیر Agent روی این سرور واقعاً اجرا شود؟'))return;
   e.currentTarget.disabled=true;
   try{const r=await jsonPost(`/api/operations/server/${sid}/install`,{confirmed:true});
     serverStatus.textContent=`درخواست نصب #${r.job_id} ثبت شد؛ جزئیات در زیر نمایش داده می‌شود.`;watchInstall(sid);
   }catch(err){serverStatus.textContent=err.message;}finally{e.currentTarget.disabled=false;}
 });
 id('web-agent-port-update')?.addEventListener('click',async()=>{
   const sid=currentServer();if(!sid)return;
   const text=prompt('پورت جدید Agent (۱۰۲۴ تا ۶۵۵۳۵):');if(text===null)return;
   const port=Number(text);if(!Number.isInteger(port)||port<1024||port>65535){toast('پورت نامعتبر');return;}
   if(!confirm(`تغییر پورت ذخیره‌شده به ${port}؟ پس از این مرحله باید Agent را تعمیر کنید.`))return;
   try{await jsonPost(`/api/operations/server/${sid}/agent-port`,{port});serverStatus.textContent='پورت ذخیره شد. برای اعمال روی سرور، Agent را مجدداً نصب کنید.';window.SonarConsole?.loadServer?.();}
   catch(err){serverStatus.textContent=err.message;}
 });
 id('web-server-rename')?.addEventListener('click',async()=>{
   const sid=currentServer();if(!sid)return;
   try{
     const existing=await request(`/api/console/servers/${sid}`);
     const name=prompt('نام جدید سرور:',existing.name);if(name===null)return;
     await jsonPost(`/api/operations/server/${sid}/rename`,{name:name.trim()});
     serverStatus.textContent='نام سرور تغییر کرد.';window.SonarConsole?.loadServer?.();
   }catch(err){serverStatus.textContent=err.message;}
 });
 id('web-server-group')?.addEventListener('click',async()=>{
   const sid=currentServer();if(!sid)return;
   try{
     const groups=await request('/api/operations/groups');
     const options=groups.map(g=>`${g.id}: ${g.name}`).join(' | ');
     const raw=prompt('شناسه پوشه (۰ = بدون پوشه): '+options,'0');if(raw===null)return;
     if(!/^\d+$/.test(raw.trim())){toast('شناسه پوشه نامعتبر است');return;}
     await jsonPost(`/api/operations/server/${sid}/group`,{group_id:Number(raw.trim())});
     serverStatus.textContent='پوشه سرور تغییر کرد.';window.SonarConsole?.loadServer?.();
   }catch(err){serverStatus.textContent=err.message;}
 });
 id('web-config-delete')?.addEventListener('click',async()=>{
   // Config detail API contains no raw subscription link or secrets.
   const cid=Number(window.SonarConsole?.currentConfigId?.()||0);if(!cid)return;
   const host=id('web-config-actions-status');
   try{
     const info=await request(`/api/console/configs/${cid}`);
     const typed=prompt(`برای حذف کانفیگ #${cid}، نام آن را وارد کنید: ${info.name}`);
     if(typed!==info.name){host.textContent='نام مطابقت ندارد.';return;}
     await jsonPost(`/api/operations/config/${cid}/delete`,{confirm_id:cid,confirm_name:typed});
     host.textContent='کانفیگ حذف شد.';tabClick('configs');
   }catch(err){host.textContent=err.message;}
 });
 id('web-server-delete')?.addEventListener('click',async()=>{
   const sid=currentServer();if(!sid)return;
   try{
     const info=await request(`/api/console/servers/${sid}`);
     const typed=prompt(`برای حذف سرور #${sid} نام آن را دقیقاً وارد کنید: ${info.name}`);
     if(typed!==info.name){toast('نام سرور مطابقت ندارد');return;}
     await jsonPost(`/api/operations/server/${sid}/delete`,{confirm_id:sid,confirm_name:typed});
     serverStatus.textContent='سرور حذف شد.';tabClick('servers');
   }catch(err){serverStatus.textContent=err.message;}
 });
 document.querySelector('#side-navigation [data-tab="web-create"]')?.addEventListener('click',loadGroups);
 window.SonarWebAgent={watchInstall,showInstallHistory};
 id('web-config-form').addEventListener('submit',async e=>{
   e.preventDefault();const btn=e.submitter;if(btn)btn.disabled=true;
   const data={name:id('new-config-name').value,link:id('new-config-link').value,kind:id('new-config-kind').value};
   try{const v=await jsonPost('/api/operations/config',data);id('new-config-link').value='';
     id('web-ops-result').textContent=`✅ کانفیگ #${v.id} ذخیره شد؛ هنوز آزمایش نشده است.`;
   }catch(err){id('web-ops-result').textContent='❌ '+err.message;}
   finally{if(btn)btn.disabled=false;data.link='';}
 });
 id('reachability-run')?.addEventListener('click',async()=>{
    const sid=Number(window.SonarConsole?.currentServerId?.());const host=id('reachability-results');
    if(!sid)return;host.textContent='در حال بررسی دسترسی TCP از مسیرهای مجاز…';
    try{const result=await request(`/api/fleet/reachability/${sid}`);
       const labels={possible_iran_only:'⚠️ احتمال محدودیت دسترسی از خارج (نیازمند بررسی بیشتر)',
         reachable_from_main:'✅ سرور از میزبان اصلی در دسترس است',
         unreachable_from_available_vantage_points:'🔴 از مسیرهای بررسی‌شده در دسترس نبود؛ قطع کامل هنوز اثبات نشده',
         insufficient_evidence:'⚪️ شواهد کافی برای نتیجه‌گیری وجود ندارد',
         unsupported_port:'⚪️ پورت این سرور برای این آزمون پشتیبانی نمی‌شود'};
       host.textContent=(labels[result.classification]||result.classification)+` | مسیر اصلی: ${result.main===null?'ناموجود':result.main?'متصل':'قطع'} | مسیر ایران: ${result.iran===null?'ناموجود':result.iran?'متصل':'قطع'}`;
    }catch(err){host.textContent='❌ '+err.message;}
 });
 const logout=id('web-logout');
 if(logout && !(window.Telegram?.WebApp?.initData)){
   logout.hidden=false;logout.onclick=async()=>{
     if(!confirm('از نسخه وب خارج شوید؟'))return;
     try{await jsonPost('/api/web/logout',{});location.reload();}catch(err){toast(err.message);}
   };
 }
})();
