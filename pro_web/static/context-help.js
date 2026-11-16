'use strict';
// Context comes from recorded page evidence and existing capabilities; no probes.
(()=>{
 const byId=id=>document.getElementById(id),node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);return el;};
 const entries={
  overview:['start','اعداد داشبورد از داده ثبت‌شده می‌آیند. کارت‌ها جزئیات منبع مربوط را باز می‌کنند.'],
  servers:['servers','وضعیت پایش و WSS با نتیجه مستقل SSH یکسان نیستند. زمان آخرین بررسی و علامت داده قدیمی را کنار هم بخوانید.'],
  configs:['configs','تست‌نشده، خطای محیط و کانفیگ ناموفق سه حالت متفاوت‌اند. امتیاز از ۱۰ است و مقدار ناموجود در انتهای مرتب‌سازی قرار می‌گیرد.'],
  rankings:['quality','کیفیت مسیر با روشن بودن سرور یکسان نیست. زمان، منطقه و تعداد نمونه‌های ثبت‌شده را مقایسه کنید.'],
  incidents:['quality','Timeline فقط رویدادهای ذخیره‌شده را نشان می‌دهد؛ تاریخچه قدیمی ممکن است ناقص باشد. فیلترها و شمارش، همان نتایج انتخاب‌شده را پوشش می‌دهند.'],
  services:['services','سلامت HTTP/TCP مربوط به آدرس سرویس است، نه سلامت کل سرور یا یک پروکسی. آدرس و فاصله تست را پیش از ثبت بررسی کنید.'],
  fleet:['agent','ثبت گواهی با تأیید TLS در heartbeat اخیر متفاوت است. نود قطع‌شده، صرفاً به علت قطع ارتباط نسخه قدیمی محسوب نمی‌شود.'],
  balancer:['agent','مسیر مرکزی همیشه اول است. عدد کمتر اولویت بالاتری دارد؛ مرتب‌سازی، نود ناسالم یا بدون رضایت را قابل استفاده نمی‌کند.'],
  'server-detail':['servers','این صفحه داده ذخیره‌شده را نمایش می‌دهد؛ بازکردن تب‌ها بررسی SSH جدید اجرا نمی‌کند.'],
  'config-detail':['configs','نتیجه آخرین اندازه‌گیری را از خطای محیط تلاش تازه جدا بخوانید. اطلاعات محرمانه لینک در جزئیات نمایش داده نمی‌شود.'],
  'web-create':['servers','فقط منبعی را که مجوز مدیریت آن دارید ثبت کنید. برای SSH، اثر انگشت را مستقل از اتصال شبکه تأیید کنید.'],
  channels:['channels','مقصد، دسته پیام و وضعیت فعال را جدا بررسی کنید؛ پیام آزمایشی تنها با انتخاب صریح شما ارسال می‌شود.'],
  settings:['alerts','تغییرات این صفحه برای فضای کاری فعلی است. سکوت زمانی، فاصله سنجش و فاصله اعلان مفهوم یکسانی ندارند.'],
  team:['workspace','مجوز مشاهده اجازه عملیات فنی یا مالی نمی‌دهد. اشتراک منابع فقط پس از رضایت و پذیرش معتبر انجام می‌شود.'],
  audit:['control','این فهرست فعالیت‌های واقعاً ثبت‌شده را نشان می‌دهد. نگهداری و پاک‌سازی لاگ می‌تواند سابقه قابل مشاهده را محدود کند.'],
  reports:['quality','گزارش از داده‌های موجود ساخته می‌شود؛ نبود نمونه، صفر مصرف یا سلامت تأییدشده نیست.'],
  billing:['billing','قیمت نمایشی تومان است. سفارش، رسید و موجودی را بررسی کنید؛ ارسال درخواست، به‌تنهایی تأیید پرداخت نیست.'],
  users:['workspace','فقط بخش‌های مدیریتی مجاز حساب شما در دسترس‌اند؛ دسترسی خواندن، اجازه اصلاح موجودی یا تأیید پرداخت نمی‌دهد.'],
  enterprise:['quality','عملیات صف‌شده را با عملیات موفق اشتباه نگیرید. وضعیت و نتیجه هر منبع را جدا بررسی کنید.'],
  support:['navigation','پیام خطا و زمان آخرین بررسی را نگه دارید. اطلاعات محرمانه را در گزارش عمومی قرار ندهید.']
 };
 const help=node('details');help.id='context-help';help.className='context-help';
 const summary=node('summary','راهنمای همین صفحه'),content=node('div');content.id='context-help-content';help.append(summary,content);byId('main').prepend(help);
 let signature='',timer=null;
 function model(){
  const tab=state.tab,entry=entries[tab]||entries.overview,caps=window.SonarExperience?.capabilities();
  let chapter=entry[0],text=entry[1],stateText='',next=null;
  if(tab==='balancer'&&state.admin)chapter='admin-fleet';if(tab==='enterprise'&&state.admin)chapter='admin-enterprise';if(tab==='support'&&state.admin)chapter='admin-safety';if(tab==='users'&&state.admin)chapter='management-product';
  if(navigator.onLine===false)stateText='مرورگر آفلاین است؛ داده تازه تأیید نمی‌شود. پس از اتصال، وضعیت را دوباره دریافت کنید.';
  else if(tab==='servers'&&byId('list-content').querySelector('.server-state[data-state=unknown]'))stateText='بعضی سرورها داده تازه ندارند. ابتدا زمان آخرین نمونه و سپس اتصال مربوط را بررسی کنید.';
  else if(tab==='configs'&&byId('list-content').textContent.includes('خطای محیط سنجش'))stateText='در این نتایج خطای محیط سنجش وجود دارد؛ آن را به معنی خرابی قطعی کانفیگ نگیرید. سلامت Agent و امکان اجرای تست را بررسی کنید.';
  else if(['fleet','balancer'].includes(tab)&&byId(tab).querySelector('.agent-evidence[data-state=unknown]'))stateText='برای بعضی Agentها شواهد تازه موجود نیست. وضعیت قدیمی را به‌عنوان ارتباط زنده یا نسخه قدیمی تفسیر نکنید.';
  if(tab==='server-detail'){
   const section=window.SonarServerTabs?.current()||'overview',evidence=byId('server-connection-details');
   const ssh=evidence?.dataset.sshState;
   if(section==='resources'&&evidence?.dataset.sampleFresh==='false')stateText='نمونه منابع قدیمی یا ناموجود است؛ مقدار صفر واقعی با نبود اندازه‌گیری متفاوت است. اتصال و زمان آخرین نمونه را بررسی کنید.';
   else if(['host_key_unknown','host_key_changed'].includes(ssh))stateText='کلید میزبان نیاز به بررسی دارد. اثر انگشت را از کنسول مستقل سرور تطبیق دهید؛ تأیید خودکار راه رفع این خطا نیست.';
   else if(ssh==='auth_failed')stateText='احراز هویت SSH پذیرفته نشده است؛ نام کاربری و روش ورود ذخیره‌شده را با دسترسی معتبر بررسی کنید.';
   else if(ssh==='network_failed')stateText='آخرین تلاش SSH خطای شبکه داشته است؛ نشانی، پورت و دسترسی مسیر را بررسی کنید. آنلاین بودن WSS، SSH را تأیید نمی‌کند.';
   else if(ssh==='sudo_required')stateText='اتصال SSH برقرار شده، ولی عملیات مدیریتی به sudo بدون درخواست رمز نیاز دارد. تنظیم مجوز باید توسط مسئول سرور بررسی شود.';
   else if(!ssh)stateText='SSH بررسی‌نشده است؛ نبود نتیجه، شکست اتصال محسوب نمی‌شود. تنها دکمه بررسی صریح، تلاش تازه اجرا می‌کند.';
   if(section==='history')text='هر بخش آخرین ۲۰ رکورد موجود را نشان می‌دهد. برای جزئیات چرخه رخداد، همان رخداد را باز کنید؛ بازه خالی با تاریخچه ساختگی پر نمی‌شود.';
   next=caps?.technical&&['host_key_unknown','host_key_changed','auth_failed','sudo_required'].includes(ssh)?{label:'مشاهده عملیات مجاز SSH',section:'operations'}:{label:'مشاهده اتصال و زمان بررسی',section:'connection'};
  }else if(tab==='configs'&&caps?.config)next={label:'رفتن به فیلتر سلامت',focus:'config-health'};
  else if(tab==='servers'&&caps?.server)next={label:'مشاهده فرم افزودن سرور',tab:'web-create',focus:'new-server-name'};
  else if(tab==='services'&&caps?.monitor)next={label:'رفتن به فرم مانیتور',focus:'service-name'};
  else if(tab==='incidents')next={label:'رفتن به فیلتر شدت',focus:'incident-severity'};
  if(navigator.onLine===false)stateText='مرورگر آفلاین است؛ داده تازه تأیید نمی‌شود. پس از اتصال، وضعیت را دوباره دریافت کنید.';
  const role=!caps?'مجوزهای فضای کاری هنوز تأیید نشده‌اند؛ راهنمای عمومی نمایش داده می‌شود.':!caps.active?'عملیات این فضای کاری فعال نیست؛ وضعیت حساب را بررسی کنید.':['viewer','auditor'].includes(caps.role)?'این نقش فقط اجازه مشاهده دارد؛ برای تغییرات از مالک یا عضو مجاز کمک بگیرید.':`راهنما بر اساس دسترسی فعلی فضای کاری است؛ فقط عملیات مجاز خود را انجام دهید.`;
  return {title:titles[tab]||'صفحه',chapter,text,stateText,role,next};
 }
 async function lesson(chapter){
  const dialog=node('dialog');dialog.className='sonar-dialog context-guide';const heading=node('h2','آموزش مرتبط'),body=node('div'),close=node('button','بستن آموزش');close.type='button';heading.id='context-guide-title';dialog.setAttribute('aria-labelledby',heading.id);dialog.append(heading,body,close);const previous=document.activeElement;let revision=0;
  function finish(){revision++;window.removeEventListener('sonar:view-change',finish);dialog.close();dialog.remove();previous?.isConnected&&previous.focus();}
  close.onclick=finish;dialog.addEventListener('cancel',event=>{event.preventDefault();finish();});window.addEventListener('sonar:view-change',finish);document.body.append(dialog);dialog.showModal();close.focus();
  async function load(){const serial=++revision;body.replaceChildren(node('p','در حال دریافت آموزش…'));try{const data=await request('/api/guide');if(serial!==revision||!dialog.isConnected)return;const match=data.chapters.find(item=>item.id===chapter)||data.chapters.find(item=>item.id==='navigation');heading.textContent=match.title;body.replaceChildren(node('p',match.summary));for(const item of match.lessons){const article=node('article');article.append(node('h3',item.button),node('p',item.action),node('p','نتیجه مورد انتظار: '+item.result));body.append(article);}const link=node('a','باز کردن راهنمای کامل در پنجره جدید');link.href='/guide#'+match.id;link.target='_blank';link.rel='noopener';body.append(link);}catch{if(serial===revision){const retry=node('button','تلاش دوباره');retry.type='button';retry.onclick=load;body.replaceChildren(node('p','دریافت آموزش انجام نشد؛ اتصال را بررسی کنید.'),retry);}}}
  load();
 }
 function update(){const data=model(),next=JSON.stringify(data);if(signature===next)return;signature=next;summary.textContent='راهنمای این صفحه: '+data.title;content.replaceChildren(node('p',data.text));if(data.stateText){const note=node('p',data.stateText);note.className='context-state-note';content.append(note);}content.append(node('p',data.role));const actions=node('div');actions.className='quicklinks';if(data.next){const button=node('button',data.next.label);button.type='button';button.onclick=()=>{if(data.next.tab)tabClick(data.next.tab);if(data.next.section)window.SonarServerTabs.select(data.next.section);if(data.next.focus){const target=byId(data.next.focus);target?.focus();target?.scrollIntoView({block:'center'});}};actions.append(button);}const training=node('button','خواندن آموزش مرتبط بدون خروج');training.type='button';training.onclick=()=>lesson(data.chapter);actions.append(training);content.append(actions);}
 function schedule(){clearTimeout(timer);timer=setTimeout(update,80);}
 help.addEventListener('toggle',()=>{if(help.open)update();});
 new MutationObserver(changes=>{if(changes.some(change=>!help.contains(change.target)))schedule();}).observe(byId('main'),{childList:true,subtree:true,characterData:true});
 for(const event of ['sonar:view-change','sonar:server-section','sonar:capabilities','online','offline'])window.addEventListener(event,schedule);
 window.SonarContextHelp={update};update();
})();
