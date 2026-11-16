'use strict';
// Shared visual semantics; never grants permission or invokes an operation.
(()=>{
 const paths={add:'M12 5v14M5 12h14',save:'m5 12 4 4L19 6',edit:'m4 16 1 4 4-1L20 8l-4-4Z',
  delete:'M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7',
  refresh:'M20 7v5h-5M4 17v-5h5M5 9a7 7 0 0 1 12-2l3 5M4 12l3 5a7 7 0 0 0 12-2',
  search:'m16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  download:'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4',close:'m6 6 12 12M18 6 6 18',
  help:'M9 8a3 3 0 0 1 6 0c0 2-3 2-3 5m0 3v1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  favorite:'m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z',
  pause:'M7 4v16M17 4v16',arrow:'M4 12h16m-6-6 6 6-6 6'};
 const skip='.navbtn,.iconbtn,.metric-link,[role=tab],.bottom-nav button,.nav-section button';
 const prefix=/^[\s+↻↗↙←→▣◇♢⇄⚙✓✅❌⛔🔔🔍📈📊📦🏷🤝🖥🗑✕×★☆]+/u;
 function kind(button,text){
  if(button.dataset.actionKind)return button.dataset.actionKind;
  if(button.matches('.destructive,.danger,.btn-danger')||/حذف|پاک‌سازی|بایگانی/.test(text))return 'delete';
  if(/علاقه‌مندی/.test(button.getAttribute('aria-label')||'')||/^[★☆]$/.test(text))return 'favorite';
  if(/بستن|انصراف|لغو|^[×✕]$/.test(text))return 'close';if(/جست‌وجو/.test(text))return 'search';
  if(/تازه|بروزرسانی|به‌روز|بارگذاری|دوباره|↻/.test(text))return 'refresh';if(/دریافت|دانلود|CSV|ZIP/.test(text))return 'download';
  if(/ایجاد|افزودن|ساخت|دعوت/.test(text))return 'add';if(/ذخیره|ثبت|تأیید|اعمال/.test(text))return 'save';
  if(/ویرایش|تنظیم|تغییر/.test(text))return 'edit';if(/راهنما|کمک/.test(text))return 'help';if(/توقف|غیرفعال/.test(text))return 'pause';return 'arrow';
 }
 function decorate(button){
  if(button.matches(skip)||button.querySelector('svg:not(.action-icon)'))return;
  const raw=button.textContent.trim(),name=button.getAttribute('aria-label')||raw;
  if(!name)return;
  if(/^[×✕]$/.test(raw)&&!button.hasAttribute('aria-label'))button.setAttribute('aria-label',button.id==='trend-close'?'بستن نمودار':'بستن پنجره');
  if(!button.hasAttribute('aria-label')){button.setAttribute('aria-label',name);button.dataset.actionAutoLabel='true';}
  // Preserve existing accessible names while replacing leading glyphs visually.
  const first=[...button.childNodes].find(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim());
  if(first&&prefix.test(first.textContent)&&first.textContent.replace(prefix,'').trim()){
   if(!button.hasAttribute('aria-label')||button.dataset.actionAutoLabel==='true'){button.setAttribute('aria-label',raw);button.dataset.actionAutoLabel='true';}
   first.textContent=first.textContent.replace(prefix,'');
  }else if(button.dataset.actionAutoLabel==='true'&&button.dataset.actionText!==raw&&raw){button.setAttribute('aria-label',raw);}
  if(first&&/^[★☆×✕]$/.test(raw)){first.textContent='';button.dataset.actionIconOnly='true';}
  button.dataset.actionText=button.textContent.trim();
  const semantic=kind(button,name);button.classList.add('action-button');button.dataset.actionVariant=semantic==='delete'?'destructive':button.classList.contains('primary')?'primary':'neutral';
  if(button.dataset.actionVariant==='destructive')button.classList.add('destructive');
  let icon=button.querySelector(':scope > .action-icon');
  if(!icon){icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.classList.add('action-icon');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.setAttribute('focusable','false');icon.setAttribute('fill','none');icon.setAttribute('stroke','currentColor');icon.setAttribute('stroke-width','1.8');icon.setAttribute('stroke-linecap','round');icon.setAttribute('stroke-linejoin','round');const path=document.createElementNS(icon.namespaceURI,'path');icon.append(path);button.prepend(icon);}
  if(semantic==='favorite'&&raw)icon.setAttribute('fill',raw==='★'?'currentColor':'none');
  const d=paths[semantic]||paths.arrow;if(icon.firstChild.getAttribute('d')!==d)icon.firstChild.setAttribute('d',d);
 }
 let scheduled=false;
 function scan(){scheduled=false;document.querySelectorAll('#app-shell button,dialog button,#quick-menu button').forEach(decorate);}
 function schedule(){if(!scheduled){scheduled=true;queueMicrotask(scan);}}
 new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true,characterData:true});scan();
})();
