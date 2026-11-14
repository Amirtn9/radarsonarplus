'use strict';
// Navigation, safe areas and progressive loading; no retained API response cache.
(()=>{
 const byId=id=>document.getElementById(id);
 async function module(name,global){
  const root=byId(name);if(!root)return;
  let message=root.querySelector('.module-status');if(!message){message=document.createElement('div');message.className='module-status route-loading';message.setAttribute('role','status');root.prepend(message);}message.textContent='در حال آماده‌سازی این بخش…';root.setAttribute('aria-busy','true');for(const form of root.querySelectorAll('form'))form.inert=true;
  try{await window.SonarUI.script('/static/'+name+'.js');message.remove();root.removeAttribute('aria-busy');for(const form of root.querySelectorAll('form'))form.inert=false;if(state.tab===name)await window[global].load();}
  catch(error){root.removeAttribute('aria-busy');message.className='module-status error-state';message.textContent='بارگذاری این بخش انجام نشد. اتصال را بررسی و دوباره تلاش کنید.';const retry=document.createElement('button');retry.type='button';retry.textContent='تلاش دوباره';retry.onclick=()=>module(name,global);message.append(retry);}
 }
 function safeAreas(){
  const root=document.documentElement,tg=window.Telegram?.WebApp;
  const finite=value=>Number.isFinite(Number(value))?Math.max(0,Math.min(180,Number(value))):0;
  const safe=tg?.safeAreaInset||{},content=tg?.contentSafeAreaInset||{};
  root.style.setProperty('--app-safe-top',Math.max(finite(safe.top),finite(content.top))+'px');
  root.style.setProperty('--app-safe-bottom',Math.max(finite(safe.bottom),finite(content.bottom))+'px');
  const height=window.visualViewport?.height||window.innerHeight;root.style.setProperty('--visual-height',height+'px');
  const top=byId('app-shell')?.querySelector('.topbar')?.getBoundingClientRect().bottom;if(top)root.style.setProperty('--quick-menu-top',Math.min(top+10,height*.4)+'px');
  document.body.classList.toggle('keyboard-open',Boolean(document.activeElement?.matches('input,textarea,select'))&&height<window.innerHeight*.78);
 }
 const eventName=code=>({config_alert_settings:'تنظیم اعلان کانفیگ',favorite_changed:'تغییر علاقه‌مندی',tag_added:'افزودن برچسب',incident_ack:'تأیید مشاهده رخداد',preference:'تغییر تنظیمات',rule:'تغییر قانون اعلان','config.edit':'ویرایش کانفیگ',operation_created:'ایجاد عملیات',service_created:'افزودن مانیتور',service_updated:'ویرایش مانیتور',service_deleted:'حذف مانیتور',team_invite:'دعوت عضو تیم'})[code]||'رویداد ثبت‌شده';
 function decorate(){
  const paths={billing:'M3 6h18v13H3z M3 10h18 M6 15h4',users:'M12 3 21 7v5c0 5-9 9-9 9S3 17 3 12V7z M8 12l3 3 5-6',reports:'M5 3h10l4 4v14H5z M8 11h8 M8 15h8 M8 18h5',search:'M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'};
  for(const node of document.querySelectorAll('#navigation button')){
   const name=node.dataset.tab||(node.id==='global-search-trigger'?'search':null);if(!paths[name]||node.querySelector('svg'))continue;
   const text=node.textContent.replace(/^▤\s*/,''),icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('class','nav-icon');icon.setAttribute('fill','none');icon.setAttribute('stroke','currentColor');icon.setAttribute('stroke-width','1.8');icon.setAttribute('aria-hidden','true');
   const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d',paths[name]);icon.append(path);const label=document.createElement('span');label.className='nav-label';label.textContent=text;node.replaceChildren(icon,label);
  }
 }
 let capabilities=null,permissionRequest=0;
 const requirements={
  technical:'#web-ssh-check,#web-existing-ssh-probe,#web-agent-install,#web-ssh-terminal,#web-agent-port-update,#web-existing-ssh-confirm-form,#control-server>button:first-of-type',
  server:'#web-server-rename,#web-server-group,#web-server-delete,#web-server-form,#web-show-server,#web-group-form,#control-server>button:not(:first-of-type)',
  config:'#web-config-delete,#config-bulk-test,#control-config button,#web-config-form,#web-show-config,#control-destinations form,#hosting-agents form',
  monitor:'#service-form,#control-policy form',
  team:'#control-team form'
 };
 function applyPermissions(){
  for(const [capability,selector]of Object.entries(requirements))for(const node of document.querySelectorAll(selector)){const denied=String(!capabilities?.[capability]);node.dataset.uxDenied=denied;const wrapper=node.closest('.server-action');if(wrapper)wrapper.dataset.uxDenied=denied;}
  for(const node of document.querySelectorAll('[data-requires]'))node.dataset.uxDenied=String(!capabilities?.[node.dataset.requires]);
  const create=document.querySelector('#navigation [data-tab="web-create"]');if(create)create.dataset.uxDenied=String(!capabilities?.server&&!capabilities?.config);
  for(const id of ['server-detail','config-detail','web-create']){const root=byId(id);if(!root)continue;let note=root.querySelector('.workspace-access-note');if(!note){note=document.createElement('p');note.className='workspace-access-note info-strip';root.prepend(note);}const text=capabilities?.active?'این فضای کاری با دسترسی مشاهده باز شده است.':'برای عملیات پایش، اعتبار فضای کاری را بررسی کنید.';if(note.textContent!==text)note.textContent=text;note.hidden=!capabilities||Boolean(capabilities.technical||capabilities.server||capabilities.monitor);}
 }
 async function permissions(){const serial=++permissionRequest;capabilities=null;applyPermissions();try{const result=await window.SonarWebRequest('/api/control/capabilities');if(serial!==permissionRequest)return;capabilities=result;applyPermissions();}catch{if(serial===permissionRequest){capabilities=null;applyPermissions();}}}
 function boot(){
  decorate();safeAreas();if(document.body.dataset.authenticated==='true')permissions();
  if(window.ResizeObserver&&document.querySelector('.topbar'))new ResizeObserver(safeAreas).observe(document.querySelector('.topbar'));
  for(const id of ['new-server-ip','new-server-user','new-server-password','new-config-link','service-target','login-code']){const input=byId(id);if(input){input.autocapitalize='none';input.spellcheck=false;}}const toast=byId('toast');if(toast){toast.setAttribute('role','status');toast.setAttribute('aria-live','polite');toast.setAttribute('aria-atomic','true');}
  byId('main')?.setAttribute('tabindex','-1');byId('menu-trigger')?.setAttribute('aria-haspopup','dialog');byId('quick-menu')?.setAttribute('role','dialog');byId('quick-menu')?.setAttribute('aria-modal','true');
  const network=document.createElement('p');network.className='network-notice info-strip';network.setAttribute('role','status');network.hidden=navigator.onLine!==false;network.textContent='اتصال مرورگر قطع است. پس از اتصال، وضعیت سفارش‌ها و عملیات در حال انجام را بررسی کنید.';byId('main').prepend(network);
  window.addEventListener('offline',()=>{network.hidden=false;document.querySelector('.topbar')?.classList.add('is-offline');setStatus('ارتباط قطع است',false);});
  window.addEventListener('online',()=>{network.hidden=true;document.querySelector('.topbar')?.classList.remove('is-offline');if(document.body.dataset.authenticated==='true')window.SonarWebRequest('/api/me').then(()=>{setStatus('متصل');permissions();}).catch(()=>{});});
  window.addEventListener('resize',safeAreas,{passive:true});window.visualViewport?.addEventListener('resize',safeAreas,{passive:true});document.addEventListener('focusout',()=>requestAnimationFrame(safeAreas));
  let bound=null;function bindTelegram(){const tg=window.Telegram?.WebApp;if(!tg||tg===bound)return;bound=tg;for(const name of ['safeAreaChanged','contentSafeAreaChanged','viewportChanged'])tg.onEvent?.(name,safeAreas);
   tg.BackButton?.onClick?.(()=>{if(document.querySelector('.sonar-dialog[open],#ssh-terminal-dialog[open]'))return;if(state.tab==='server-detail')tabClick('servers');else if(state.tab==='config-detail')tabClick('configs');else if(location.pathname.startsWith('/admin/')&&location.pathname!=='/admin/overview')window.SonarProduct.navigate('overview');else tabClick('overview');});safeAreas();
  }
  function back(){bindTelegram();const button=window.Telegram?.WebApp?.BackButton;if(!button)return;if(state.tab==='overview')button.hide?.();else button.show?.();}
  const announcement=document.createElement('p');announcement.className='sr-only';announcement.setAttribute('role','status');announcement.setAttribute('aria-live','polite');document.body.append(announcement);
  window.addEventListener('sonar:view-change',event=>{announcement.textContent=titles[event.detail.name]||'صفحه';back();applyPermissions();});back();
  document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;queueMicrotask(()=>{if(button.disabled)button.setAttribute('aria-busy','true');});},{capture:true});
  new MutationObserver(()=>applyPermissions()).observe(byId('main'),{childList:true,subtree:true});
  new MutationObserver(changes=>{for(const item of changes)if(item.attributeName==='disabled'&&!item.target.disabled)item.target.removeAttribute('aria-busy');}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['disabled']});
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
 window.SonarExperience={module,safeAreas,permissions,eventName};
})();
