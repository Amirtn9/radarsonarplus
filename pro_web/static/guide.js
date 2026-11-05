'use strict';
(async()=>{
 const byId=id=>document.getElementById(id);
 const node=(tag,text,cls)=>{const element=document.createElement(tag);if(text!=null)element.textContent=text;if(cls)element.className=cls;return element;};
 const status=byId('guide-status');
 let signed='';try{signed=window.Telegram?.WebApp?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')||'';}catch{}
 const api=async path=>{
  const response=await fetch(path,{credentials:'same-origin',cache:'no-store',headers:signed?{Authorization:'tma '+signed}:{}});
  if(!response.ok)throw Error(String(response.status));return response.json();
 };
 try{
  if(signed){
   // Establish the same revocable cookie session as the main Mini App.
   try{const exchange=await fetch('/api/web/miniapp-session',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{Authorization:'tma '+signed}});if(exchange.ok)signed='';}catch{}
   // Reloading an old launch must not override a still-valid cookie session,
   // nor may a cookie belonging to another Telegram account be reused.
   if(signed){try{
    const uid=Number(JSON.parse(new URLSearchParams(signed).get('user')||'null')?.id);
    const response=await fetch('/api/me',{credentials:'same-origin',cache:'no-store'});
    if(Number.isSafeInteger(uid)&&uid>0&&response.ok&&Number((await response.json()).id)===uid)signed='';
   }catch{}}
  }
  let data;try{data=await api('/api/guide');}catch{data=await api('/api/guide/public');}
  byId('audience').textContent=data.admin?'راهنمای کاربر و مدیر':'راهنمای کاربران';
  const count=data.chapters.reduce((total,chapter)=>total+chapter.lessons.length,0);
  byId('lesson-count').textContent=`${count.toLocaleString('fa-IR')} آموزش · ${data.buttons.length.toLocaleString('fa-IR')} دکمه`;
  status.textContent=data.admin?'برای مطالعه، دسته موردنظر را انتخاب کنید.':'برای مطالعه، دسته موردنظر را انتخاب کنید.';
  for(const chapter of data.chapters){
   const section=node('section',null,'reveal');section.id=chapter.id;section.dataset.search=chapter.title+' '+chapter.summary;
   section.append(node('h2',chapter.title),node('p',chapter.summary,'chapter-summary'));
   const link=node('a',chapter.title);link.href='#'+chapter.id;byId('chapters').append(link);
   for(const lesson of chapter.lessons){
    const details=node('details');details.dataset.search=[chapter.title,lesson.button,lesson.action,lesson.result].join(' ');
    const body=node('div',null,'lesson-body');body.append(node('b','چه کار کنم؟'),node('p',lesson.action));
    const result=node('div',null,'result');result.append(node('b','چه نتیجه‌ای انتظار داشته باشم؟'),node('p',lesson.result));body.append(result);
    details.append(node('summary',lesson.button),body);section.append(details);
   }
   byId('chapter-content').append(section);
  }
  let category='';for(const item of data.buttons){
   if(item.category!==category){category=item.category;byId('button-content').append(node('h3',category,'button-group'));}
   const details=node('details',null,'dictionary-item');details.dataset.search=[item.label,item.category,item.description].join(' ');
   const body=node('div',item.description,'lesson-body');details.append(node('summary',item.label),body);byId('button-content').append(details);
  }
  const normalize=text=>String(text).replace(/ي/g,'ی').replace(/ك/g,'ک').replace(/\u200c/g,' ').replace(/wireguard/gi,'وایرگارد').toLocaleLowerCase('fa');
  byId('guide-search').addEventListener('input',event=>{
   const query=normalize(event.target.value.trim());let matches=0;
   document.querySelectorAll('details[data-search]').forEach(item=>{item.hidden=!!query&&!normalize(item.dataset.search).includes(query);if(!item.hidden)matches++;});
   byId('chapter-content').querySelectorAll('section').forEach(section=>{section.hidden=!!query&&![...section.querySelectorAll('details')].some(item=>!item.hidden);});
   status.textContent=query?`${matches.toLocaleString('fa-IR')} آموزش یا دکمه پیدا شد.`:'همه دسته‌ها نمایش داده می‌شوند؛ روی عنوان آموزش بزنید.';
  });
  const updateProgress=()=>{const length=document.documentElement.scrollHeight-innerHeight;byId('reading-progress').style.width=(length>0?Math.min(100,scrollY/length*100):0)+'%';};
  addEventListener('scroll',updateProgress,{passive:true});updateProgress();
 }catch{status.textContent='دریافت آموزش ممکن نشد. اتصال را بررسی و صفحه را دوباره باز کنید.';}
})();
