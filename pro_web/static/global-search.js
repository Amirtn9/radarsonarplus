'use strict';
(()=>{
 const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);return el;};
 const kinds={server:'سرورها',config:'کانفیگ‌ها',agent:'Agentها',incident:'رخدادها',service:'مانیتورها',user:'کاربران',order:'سفارش‌ها'};
 let active=null;
 function open(){
  if(active?.isConnected){active.querySelector('input').focus();return;}
  const previous=document.activeElement,dialog=node('dialog');active=dialog;dialog.className='sonar-dialog global-search-dialog';
  const title=node('h2','جست‌وجوی سراسری');title.id='global-search-title';dialog.setAttribute('aria-labelledby',title.id);
  const label=node('label','نام، شناسه، IP یا دامنه'),input=node('input');input.type='search';input.id='global-search-query';input.maxLength=100;input.autocomplete='off';label.htmlFor=input.id;label.append(input);
  const status=node('p','حداقل دو نویسه وارد کنید.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const results=node('div');results.id='global-search-results';const close=node('button','بستن');close.type='button';dialog.append(title,label,status,results,close);
  let sequence=0,timer=null;
  function finish(){sequence++;clearTimeout(timer);dialog.close();dialog.remove();active=null;window.removeEventListener('sonar:view-change',finish);if(previous?.isConnected)previous.focus();}
  close.onclick=finish;dialog.addEventListener('cancel',event=>{event.preventDefault();finish();});window.addEventListener('sonar:view-change',finish);
  function navigate(item){
   const url=new URL(item.href,location.origin);if(url.origin!==location.origin)return;
   finish();history.pushState({},'',url.pathname+url.search);restoreRoute();
  }
  async function search(q,serial){
   results.replaceChildren();status.textContent='در حال جست‌وجو…';results.setAttribute('aria-busy','true');
   try{
    const rows=await request('/api/product/search?q='+encodeURIComponent(q));if(serial!==sequence||!dialog.isConnected)return;
    results.replaceChildren();status.textContent=rows.length?`${rows.length} نتیجه؛ حداکثر ۱۰ نتیجه از هر دسته.`:'نتیجه‌ای پیدا نشد.';
    for(const [kind,title]of Object.entries(kinds)){
     const matching=rows.filter(item=>item.kind===kind);if(!matching.length)continue;
     const section=node('section');section.append(node('h3',title));for(const item of matching){const link=node('a');link.href=item.href;link.className='global-search-result';link.append(node('strong',item.name),node('small',item.summary+' · #'+item.id));link.onclick=event=>{if(event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;event.preventDefault();navigate(item);};section.append(link);}results.append(section);
    }
   }catch(error){if(serial!==sequence||!dialog.isConnected)return;status.textContent='جست‌وجو انجام نشد؛ اتصال را بررسی کنید.';const retry=node('button','تلاش دوباره');retry.type='button';retry.onclick=()=>search(input.value.trim(),++sequence);results.replaceChildren(retry);}
   finally{if(serial===sequence)results.removeAttribute('aria-busy');}
  }
  input.oninput=()=>{const serial=++sequence;clearTimeout(timer);results.replaceChildren();results.removeAttribute('aria-busy');const q=input.value.trim();if(q.length<2){status.textContent='حداقل دو نویسه وارد کنید.';return;}status.textContent='در انتظار پایان تایپ…';timer=setTimeout(()=>search(q,serial),300);};
  input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();clearTimeout(timer);const q=input.value.trim();if(q.length>=2)search(q,++sequence);}};
  dialog.addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;const links=[...results.querySelectorAll('a')];if(!links.length)return;event.preventDefault();const index=links.indexOf(document.activeElement),delta=event.key==='ArrowDown'?1:-1;links[index<0?(delta===1?0:links.length-1):(index+delta+links.length)%links.length].focus();});
  document.body.append(dialog);dialog.showModal();input.focus();
 }
 window.SonarGlobalSearch={open};
})();
