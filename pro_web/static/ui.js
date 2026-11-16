'use strict';
// Accessible, asynchronous dialogs shared by every panel. Text is never HTML.
(()=>{
 let pending=Promise.resolve();const assets=new Map();
 function modal(message,value,mode){
  const run=()=>new Promise(resolve=>{
   const previous=document.activeElement,dialog=document.createElement('dialog');dialog.className='sonar-dialog';dialog.setAttribute('aria-labelledby','sonar-dialog-title');
   const form=document.createElement('form');form.method='dialog';
   const title=document.createElement('h2');title.id='sonar-dialog-title';title.textContent=mode==='ask'?'ورود اطلاعات':'تأیید عملیات';
   const description=document.createElement('p');description.textContent=message;
   form.append(title,description);let input=null;
   if(mode==='ask'){const label=document.createElement('label');label.textContent='مقدار';input=document.createElement('textarea');input.name='value';input.rows=3;input.value=value||'';input.maxLength=3500;label.append(input);form.append(label);}
   const actions=document.createElement('div');actions.className='dialog-actions';
   const cancel=document.createElement('button');cancel.type='button';cancel.textContent='انصراف';
   const save=document.createElement('button');save.type='submit';save.className=/حذف|پاک‌سازی|بایگانی|بازپرداخت|ابطال/.test(message)?'destructive':'primary';save.textContent=mode==='ask'?'ثبت و ادامه':'تأیید و ادامه';actions.append(cancel,save);form.append(actions);dialog.append(form);document.body.append(dialog);
   const cancelOnNavigation=()=>finish(false);window.addEventListener('popstate',cancelOnNavigation);window.addEventListener('sonar:view-change',cancelOnNavigation);
   let done=false;function finish(ok){if(done)return;done=true;window.removeEventListener('popstate',cancelOnNavigation);window.removeEventListener('sonar:view-change',cancelOnNavigation);dialog.close?.();dialog.remove();if(previous?.disabled)requestAnimationFrame(()=>{if(previous.isConnected&&!previous.disabled)previous.focus();});else previous?.focus?.();resolve(ok?(input?input.value:true):(input?null:false));}
   cancel.onclick=()=>finish(false);dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});form.onsubmit=event=>{event.preventDefault();finish(true);};
   if(dialog.showModal)dialog.showModal();else{dialog.setAttribute('open','');dialog.setAttribute('aria-modal','true');dialog.setAttribute('role','dialog');}
   (input||cancel).focus();
  });const result=pending.then(run,run);pending=result.catch(()=>{});return result;
 }
 function script(src){
  if(assets.has(src))return assets.get(src);
  const task=new Promise((resolve,reject)=>{
   const tag=document.createElement('script');let settled=false;
   const timer=setTimeout(()=>finish(Error('بارگذاری ابزار طول کشید؛ دوباره تلاش کنید.')),15000);
   function finish(error){if(settled)return;settled=true;clearTimeout(timer);if(error){tag.remove();assets.delete(src);reject(error);}else resolve();}
   tag.src=src;tag.onload=()=>finish();tag.onerror=()=>finish(Error('بارگذاری ابزار ممکن نشد؛ دوباره تلاش کنید.'));document.head.append(tag);
  });assets.set(src,task);return task;
 }

 async function terminal(){if(typeof window.Terminal==='function')return;let css=document.querySelector('link[data-terminal-css]');if(!css){css=document.createElement('link');css.rel='stylesheet';css.href='/static/vendor/xterm.css';css.dataset.terminalCss='1';document.head.append(css);}await script('/static/vendor/xterm.js');await script('/static/vendor/xterm-fit.js');}
 function download(response,name){return response.blob().then(blob=>{const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);});}
 function actionState(button,value){
  if(!button)return;button.dataset.actionState=value;
  let status=document.getElementById('action-feedback');if(!status){status=document.createElement('p');status.id='action-feedback';status.className='sr-only';status.setAttribute('role','status');status.setAttribute('aria-live','polite');document.body.append(status);}
  const name=button.getAttribute('aria-label')||button.textContent.trim();status.textContent=value==='idle'?'':name+'؛ '+({busy:'در حال انجام',success:'انجام شد',error:'انجام نشد؛ پیام خطا را بخوانید'}[value]||'');
  if(value==='busy')button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
 }
 async function run(button,task){
  if(button.disabled||button.dataset.actionState==='busy')return;
  button.disabled=true;actionState(button,'busy');
  try{const result=await task();actionState(button,result===undefined||result===null||result===false?'idle':'success');return result;}
  catch(error){actionState(button,'error');throw error;}
  finally{button.disabled=false;}
 }
 window.SonarUI={run,state:actionState,ask:(message,value='')=>modal(message,value,'ask'),confirm:message=>modal(message,'','confirm'),script,terminal,download};
})();
