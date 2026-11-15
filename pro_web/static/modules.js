'use strict';
// Small navigation bridges: feature scripts are requested only when used.
(()=>{
 const groups={commerce:['billing','product'],reports:['reports'],logs:['log-management']};
 const pending=new Map();
 async function ensure(group){
  if(!groups[group])throw Error('ماژول ناشناخته است');
  if(!pending.has(group)){
   const task=(async()=>{for(const file of groups[group])await window.SonarUI.script('/static/'+file+'.js');})();
   pending.set(group,task);task.catch(()=>{if(pending.get(group)===task)pending.delete(group);});
  }
  return pending.get(group);
 }
 const definitions={
  SonarBilling:['commerce',['load']],
  SonarBillingComponents:['commerce',['render','userEditor','destinationEditor','discountEditor','staffPanel']],
  SonarProduct:['commerce',['load','navigate','planEditor','preview','usage','archive','search']],
  SonarReports:['reports',['load']],SonarLogs:['logs',['load']]
 };
 function screen(name,args){return document.getElementById(name==='SonarBilling'?(args[0]||'billing'):name==='SonarReports'?'reports':name==='SonarLogs'?'support':'users');}
 for(const [name,[group,methods]]of Object.entries(definitions)){
  const bridge={};
  for(const method of methods)bridge[method]=async(...args)=>{
   const root=screen(name,args);let status=root?.querySelector('.module-loader-status');
   if(root){
    if(!status){status=document.createElement('div');status.className='module-loader-status route-loading';status.setAttribute('role','status');root.prepend(status);}
    status.textContent='در حال آماده‌سازی این بخش…';root.setAttribute('aria-busy','true');
    for(const form of root.querySelectorAll('form'))form.inert=true;
   }
   try{
    await ensure(group);
    if(root){root.removeAttribute('aria-busy');for(const form of root.querySelectorAll('form'))form.inert=false;status?.remove();}
    const actual=window[name];if(actual===bridge||typeof actual?.[method]!=='function')throw Error('بارگذاری بخش کامل نشد');
    // A slow first load must not rename or render a page the user has left.
    if(method==='load'&&root&&typeof state!=='undefined'&&state.tab!==root.id)return;
    return await actual[method](...args);
   }catch(error){
    if(root){
     root.removeAttribute('aria-busy');
     if(!status?.isConnected){status=document.createElement('div');status.className='module-loader-status error-state';status.setAttribute('role','status');root.prepend(status);}
     status.replaceChildren(document.createTextNode('بارگذاری این بخش انجام نشد. اتصال را بررسی کنید.'));
     const retry=document.createElement('button');retry.type='button';retry.textContent='تلاش دوباره';retry.onclick=()=>bridge[method](...args);status.append(retry);
    }else if(typeof toast==='function')toast(error.message);
   }
  };
  window[name]=bridge;
 }
 function boot(){
  const header=document.getElementById('navigation');if(header&&!document.getElementById('global-search-trigger')){
   const trigger=document.createElement('button');trigger.type='button';trigger.textContent='جست‌وجوی سراسری';trigger.id='global-search-trigger';trigger.className='navbtn';trigger.setAttribute('aria-keyshortcuts','Control+k Meta+k');trigger.onclick=search;header.append(trigger);
  }
 }
 function search(){Promise.resolve(window.SonarProduct.search()).catch(error=>{if(typeof toast==='function')toast(error.message);});}
 document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&document.body.dataset.authenticated==='true'){event.preventDefault();search();}});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
 window.SonarModules={ensure};
})();
