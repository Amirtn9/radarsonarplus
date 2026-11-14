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
   const save=document.createElement('button');save.type='submit';save.className='primary';save.textContent=mode==='ask'?'ثبت و ادامه':'تأیید و ادامه';actions.append(cancel,save);form.append(actions);dialog.append(form);document.body.append(dialog);
   let done=false;function finish(ok){if(done)return;done=true;dialog.close?.();dialog.remove();previous?.focus?.();resolve(ok?(input?input.value:true):(input?null:false));}
   cancel.onclick=()=>finish(false);dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});form.onsubmit=event=>{event.preventDefault();finish(true);};
   if(dialog.showModal)dialog.showModal();else{dialog.setAttribute('open','');dialog.setAttribute('aria-modal','true');dialog.setAttribute('role','dialog');}
   (input||cancel).focus();
  });const result=pending.then(run,run);pending=result.catch(()=>{});return result;
 }
 function script(src){if(assets.has(src))return assets.get(src);const task=new Promise((resolve,reject)=>{const tag=document.createElement('script');tag.src=src;tag.onload=resolve;tag.onerror=()=>{tag.remove();assets.delete(src);reject(Error('بارگذاری ابزار ممکن نشد؛ دوباره تلاش کنید.'));};document.head.append(tag);});assets.set(src,task);return task;}
 async function terminal(){if(typeof window.Terminal==='function')return;let css=document.querySelector('link[data-terminal-css]');if(!css){css=document.createElement('link');css.rel='stylesheet';css.href='/static/vendor/xterm.css';css.dataset.terminalCss='1';document.head.append(css);}await script('/static/vendor/xterm.js');await script('/static/vendor/xterm-fit.js');}
 function download(response,name){return response.blob().then(blob=>{const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);});}
 window.SonarUI={ask:(message,value='')=>modal(message,value,'ask'),confirm:message=>modal(message,'','confirm'),script,terminal,download};
})();
