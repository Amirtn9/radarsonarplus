'use strict';
(()=>{
 const get=id=>document.getElementById(id),make=(tag,text)=>{const el=document.createElement(tag);el.textContent=text??'—';return el;};
 async function load(){
  get('reports-status').textContent='در حال دریافت گزارش…';
  try{const data=await window.SonarWebRequest('/api/reports/overview');
   get('reports-summary').replaceChildren();
   for(const [label,value] of [['سرورها',data.summary.servers.total],['کانفیگ‌ها',data.summary.configs.total],['سالم',data.summary.configs.healthy],['رخداد باز',data.summary.incidents]]){const card=make('article','');card.className='metric';card.append(make('small',label),make('h2',Number(value).toLocaleString('fa-IR')));get('reports-summary').append(card);}
   get('reports-servers').replaceChildren();
   data.servers.forEach(server=>{const row=make('tr','');[server.name,server.last_status,server.last_cpu,server.last_ram,server.last_disk,server.last_checked_at?new Date(server.last_checked_at).toLocaleString('fa-IR'):'هنوز سنجیده نشده'].forEach(value=>row.append(make('td',value)));get('reports-servers').append(row);});
   for(const [key,id] of [['configs','reports-configs'],['incidents','reports-incidents']]){get(id).replaceChildren();if(!data[key].length)get(id).append(make('p','موردی ثبت نشده است.'));data[key].forEach(item=>get(id).append(make('p',key==='configs'?`${item.name} · ${item.score}/100 · ${item.ping??'—'} ms`:`${item.title} · ${item.status}`)));}
   get('reports-status').textContent=data.servers.length?'آخرین وضعیت ذخیره‌شده دریافت شد.':'هنوز سروری در فضای کاری ثبت نشده است.';
  }catch(error){get('reports-status').textContent=error.message;}
 }
 get('reports-export')?.addEventListener('click',async()=>{try{const response=await window.SonarWebRequest('/api/reports/servers.csv',{raw:true});const blob=await response.blob();const url=URL.createObjectURL(blob);const link=make('a','');link.href=url;link.download='sonar-servers.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){get('reports-status').textContent=error.message;}});
 const help={ 'ca-failure_threshold':'تعداد تست ناموفق متوالی پیش از ایجاد هشدار قطعی.', 'ca-score_delta':'حداقل اختلاف امتیاز از ۱۰۰ برای ارسال هشدار کیفیت.', 'ca-cooldown_minutes':'کمترین فاصله میان دو پیام تغییر کیفیت؛ اعلان بازیابی را عقب نمی‌اندازد.', 'multi_probe':'تست یک کانفیگ از چند نقطه شبکه برای مقایسه مسیرها.', 'resource-hours':'فقط نمونه‌های ثبت‌شده در این بازه نمایش داده می‌شوند؛ دریافت خودکار به معنی نمونه‌سازی نیست.'};
 for(const [id,text] of Object.entries(help)){const input=get(id);if(!input)continue;const host=input.closest('label')||input.parentElement;const detail=make('details','');detail.className='field-help';const summary=make('summary','؟');summary.setAttribute('aria-label','راهنمای این گزینه');detail.append(summary,make('p',text));host.append(detail);}
 const glossary={TLS:'بررسی هویت و رمزنگاری اتصال با گواهی معتبر.',WSS:'ارتباط رمزنگاری‌شده با Agent برای دریافت آمار و انجام تست.',RTT:'زمان رفت‌وبرگشت درخواست؛ مقدار کمتر معمولاً بهتر است.',Webhook:'ارسال خودکار رویدادها به نشانی HTTPS سامانه شما.',webhook:'ارسال خودکار رویدادها به سامانه دیگر.',Canary:'انتشار آزمایشی روی تعداد کمی نود قبل از انتشار گسترده.',canary:'تعداد نودهای مرحله آزمایشی انتشار.',Scope:'محدوده مجوز توکن؛ فقط دسترسی لازم را انتخاب کنید.',Jitter:'نوسان تأخیر بین نمونه‌ها؛ مقدار کمتر نشانه ثبات بیشتر است.',CPU:'درصد استفاده از پردازنده سرور.',RAM:'درصد استفاده از حافظه سرور.',rollback:'بازگردانی نسخه قبلی پس از بررسی نتیجه انتشار.',heartbeat:'آخرین پیام سلامت دریافت‌شده از Agent.'};
 const seen=new WeakSet();
 function explain(){document.querySelectorAll('label,h3,dt').forEach(host=>{if(seen.has(host)||host.querySelector('.field-help'))return;seen.add(host);const term=Object.keys(glossary).find(term=>host.textContent.includes(term));if(!term)return;const detail=make('details','');detail.className='field-help';const summary=make('summary','؟');summary.setAttribute('aria-label','توضیح '+term);detail.append(summary,make('p',glossary[term]));host.append(detail);});}
 explain();let scheduled=false;new MutationObserver(()=>{if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;explain();});}).observe(document.getElementById('main'),{childList:true,subtree:true});
 window.SonarReports={load};
})();
