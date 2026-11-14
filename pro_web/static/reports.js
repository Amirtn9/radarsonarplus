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
   await analyticsPanel();
   get('reports-status').textContent=data.servers.length?'آخرین وضعیت ذخیره‌شده دریافت شد.':'هنوز سروری در فضای کاری ثبت نشده است.';
  }catch(error){get('reports-status').textContent=error.message;}
 }
 get('reports-export')?.addEventListener('click',async()=>{try{const response=await window.SonarWebRequest('/api/reports/servers.csv',{raw:true});const blob=await response.blob();const url=URL.createObjectURL(blob);const link=make('a','');link.href=url;link.download='sonar-servers.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){get('reports-status').textContent=error.message;}});
 async function analyticsPanel(){
  if(get('reports-analytics'))return;
  const root=document.createElement('section');root.className='panel';root.id='reports-analytics';root.append(make('h2','گزارش تحلیلی در بازه انتخابی'));
  const form=document.createElement('form');form.className='billing-form';const inputs={};
  for(const [key,label] of [['start','از تاریخ'],['end','تا تاریخ']]){const wrap=make('label',label),input=document.createElement('input');input.type='datetime-local';input.required=true;wrap.append(input);form.append(wrap);inputs[key]=input;}
  function localValue(date){return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
  function setWindow(hours){inputs.end.value=localValue(new Date());inputs.start.value=localValue(new Date(Date.now()-hours*3600000));}
  setWindow(24);const controls=document.createElement('div');
  for(const hours of [24,48,168,720]){const b=make('button',hours<72?hours+' ساعت':hours/24+' روز');b.type='button';b.onclick=()=>{setWindow(hours);void load();};controls.append(b);}root.append(controls);
  const submit=make('button','نمایش گزارش');submit.type='submit';form.append(submit);root.append(form);const results=document.createElement('div');results.setAttribute('role','status');root.append(results);
  async function load(){submit.disabled=true;try{const query=new URLSearchParams({start:new Date(inputs.start.value).toISOString(),end:new Date(inputs.end.value).toISOString()});const data=await window.SonarWebRequest('/api/reports/analytics?'+query);results.replaceChildren(make('p',`نمونه سرور: ${data.servers.samples} · تست کانفیگ: ${data.configs.samples} · تست موفق: ${data.configs.successful} · رخداد: ${data.incidents.incidents}`));
   for(const region of data.regions)results.append(make('p',`${region.region==='ir'?'ایران':region.region==='ext'?'خارج':'مرکزی'} · اندازه‌گیری ${region.measured} · نامشخص ${region.unknown} · دسترس‌پذیری نمونه‌ها ${region.sample_availability==null?'نامشخص':Number(region.sample_availability).toFixed(2)+'٪'} · میانگین پاسخ ${region.average_ms==null?'—':Number(region.average_ms).toFixed(1)+' ms'}`));
   results.append(make('p',`مجموع مدت رخدادهای هم‌پوشان: ${Math.round(Number(data.incidents.incident_seconds)/60)} دقیقه؛ این عدد مجموع رخدادهاست و زمان قطعی یک سرویس واحد نیست. نگهداری تاریخچه ${data.retention_days} روز است؛ نبود نمونه به معنی سلامت نیست.`));
  }catch(error){results.replaceChildren(make('p',error.message));}finally{submit.disabled=false;}}
  form.onsubmit=e=>{e.preventDefault();void load();};get('reports-status').after(root);await load();
 }
 const csvButton=get('reports-export');
 if(csvButton)for(const [format,title] of [['snapshot.json','دریافت JSON'],['report.pdf','دریافت PDF']]){
  const button=make('button',title);button.type='button';button.onclick=async()=>{button.disabled=true;try{const response=await window.SonarWebRequest('/api/reports/'+format,{raw:true});const url=URL.createObjectURL(await response.blob()),a=make('a','');a.href=url;a.download='sonar-'+format;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(error){get('reports-status').textContent=error.message;}finally{button.disabled=false;}};csvButton.after(button);
 }
 const help={ 'ca-failure_threshold':'تعداد تست ناموفق متوالی پیش از ایجاد هشدار قطعی.', 'ca-score_delta':'حداقل اختلاف امتیاز از ۱۰۰ برای ارسال هشدار کیفیت.', 'ca-cooldown_minutes':'کمترین فاصله میان دو پیام تغییر کیفیت؛ اعلان بازیابی را عقب نمی‌اندازد.', 'multi_probe':'تست یک کانفیگ از چند نقطه شبکه برای مقایسه مسیرها.', 'resource-hours':'فقط نمونه‌های ثبت‌شده در این بازه نمایش داده می‌شوند؛ دریافت خودکار به معنی نمونه‌سازی نیست.'};
 for(const [id,text] of Object.entries(help)){const input=get(id);if(!input)continue;const host=input.closest('label')||input.parentElement;const detail=make('details','');detail.className='field-help';const summary=make('summary','؟');summary.setAttribute('aria-label','راهنمای این گزینه');detail.append(summary,make('p',text));host.append(detail);}
 const glossary={TLS:'بررسی هویت و رمزنگاری اتصال با گواهی معتبر.',WSS:'ارتباط رمزنگاری‌شده با Agent برای دریافت آمار و انجام تست.',RTT:'زمان رفت‌وبرگشت درخواست؛ مقدار کمتر معمولاً بهتر است.',Webhook:'ارسال خودکار رویدادها به نشانی HTTPS سامانه شما.',webhook:'ارسال خودکار رویدادها به سامانه دیگر.',Canary:'انتشار آزمایشی روی تعداد کمی نود قبل از انتشار گسترده.',canary:'تعداد نودهای مرحله آزمایشی انتشار.',Scope:'محدوده مجوز توکن؛ فقط دسترسی لازم را انتخاب کنید.',Jitter:'نوسان تأخیر بین نمونه‌ها؛ مقدار کمتر نشانه ثبات بیشتر است.',CPU:'درصد استفاده از پردازنده سرور.',RAM:'درصد استفاده از حافظه سرور.',rollback:'بازگردانی نسخه قبلی پس از بررسی نتیجه انتشار.',heartbeat:'آخرین پیام سلامت دریافت‌شده از Agent.'};
 const seen=new WeakSet();
 function explain(){document.querySelectorAll('label,h3,dt').forEach(host=>{if(seen.has(host)||host.querySelector('.field-help'))return;seen.add(host);const term=Object.keys(glossary).find(term=>host.textContent.includes(term));if(!term)return;const detail=make('details','');detail.className='field-help';const summary=make('summary','؟');summary.setAttribute('aria-label','توضیح '+term);detail.append(summary,make('p',glossary[term]));host.append(detail);});}
 explain();let scheduled=false;new MutationObserver(()=>{if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;explain();});}).observe(document.getElementById('main'),{childList:true,subtree:true});
 window.SonarReports={load};
})();
