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
 id('web-server-form').addEventListener('submit',async e=>{
   e.preventDefault();const form=e.currentTarget,btn=e.submitter;
   if(btn)btn.disabled=true;id('web-ops-result').textContent='در حال ثبت...';
   const data={name:id('new-server-name').value,ip:id('new-server-ip').value,location_type:id('new-server-location').value,
     port:Number(id('new-server-port').value),username:id('new-server-user').value,
     password:id('new-server-password').value,ws_port:Number(id('new-server-ws').value)};
   try{const v=await jsonPost('/api/operations/server',data);id('new-server-password').value='';
      id('web-ops-result').textContent=`✅ سرور #${v.id} ذخیره شد. برای نصب Agent وارد استخر Agentها یا منوی سرور در ربات شوید.`;
   }catch(err){id('web-ops-result').textContent='❌ '+err.message;}
   finally{if(btn)btn.disabled=false;data.password='';}
 });
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
