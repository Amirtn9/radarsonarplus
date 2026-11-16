'use strict';
// Read-only presentation of recorded evidence. Loading a card never probes a host.
(()=>{
 const versions=new Map(),cards=new Set();
 const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);if(cls)el.className=cls;return el;};
 const stamp=value=>{if(!value)return NaN;return new Date(value).valueOf();};
 const fresh=value=>{const age=Date.now()-stamp(value);return Number.isFinite(age)&&age>=-30000&&age<300000;};
 const metric=(value,max,unit)=>{if(value==null||value==='')return '—';const number=Number(value);return Number.isFinite(number)&&number>=0&&number<=max?number.toLocaleString('fa-IR',{maximumFractionDigits:1})+unit:'—';};
 const labels={current:'مطابق نسخه مرکزی',different:'نسخه یا بیلد متفاوت',incompatible:'پروتکل ناسازگار',unknown:'نامشخص'};
 function evidence(item,host){
  const recent=fresh(item.last_probe_at),healthy=navigator.onLine!==false&&recent&&item.last_probe_ok===true;
  const observed=Number.isFinite(stamp(item.last_probe_at));
  const status=navigator.onLine===false?'مرورگر آفلاین':!observed?'بررسی‌نشده':!recent?'داده قدیمی':item.last_probe_ok===false?'ارتباط ناموفق':healthy?'متصل':'نامشخص';
  const data=versions.get(Number(item.id));
  const version=healthy&&data&&fresh(data.checked_at)?data:null;
  const versionText=version?`${labels[version.state]||labels.unknown} · ${version.agent_version||'—'} · بیلد ${version.agent_build?.slice(0,12)||'—'}`:'نامشخص؛ بررسی نسخه لازم است';
  const enrolled=item.tls_enrolled&&item.token_configured;
  host.dataset.state=healthy?'fresh':recent?'failed':'unknown';
  host.replaceChildren();
  const heartbeat=observed&&item.last_probe_ok===true?new Date(stamp(item.last_probe_at)).toLocaleString('fa-IR')+(recent?' · تازه':' · قدیمی'):'در آخرین بررسی ثبت نشده';
  const fields=[['ارتباط',status],['WSS',healthy?'موفق در پنج دقیقه اخیر':status],
   ['TLS',healthy&&enrolled?'تأییدشده در آخرین heartbeat':enrolled?'گواهی و توکن ثبت‌شده؛ ارتباط تأیید نشده':'گواهی یا توکن ثبت نشده'],
   ['آخرین Heartbeat موفق',heartbeat],['تأخیر',healthy?metric(item.latency_ms,60000,' ms'):'—'],
   ['CPU',healthy?metric(item.cpu_pct??item.cpu,100,'٪'):'—'],['نسخه Agent',versionText]];
  for(const [title,value]of fields){const cell=node('div');cell.append(node('dt',title),node('dd',value));host.append(cell);}
 }
 function attach(card,item){
  card.dataset.agentId=String(item.id);card.classList.add('agent-card');
  card.append(node('p',`شناسه Agent: ${item.id} · منطقه: ${item.location_type==='ir'?'ایران':item.location_type==='ext'?'خارج':'نامشخص'}`,'muted'));
  const host=node('dl',null,'agent-evidence');card.append(host);evidence(item,host);cards.add({card,item,host});
  const actions=node('div',null,'agent-card-actions');
  if(item.owned_by_admin||item.owned_by_workspace){const details=node('button','جزئیات و رفع مشکل');details.type='button';details.onclick=()=>window.SonarConsole?.openDetails('server',Number(item.id));actions.append(details);}
  else actions.append(node('p','برای رفع مشکل با مالک Agent هماهنگ کنید.','muted'));
  card.append(actions);return actions;
 }
 function refresh(){for(const entry of cards){if(!entry.card.isConnected){cards.delete(entry);continue;}evidence(entry.item,entry.host);}}
 function record(report){for(const item of report.nodes||[])if(item&&Number.isSafeInteger(Number(item.id)))versions.set(Number(item.id),item);refresh();}
 setInterval(refresh,15000);window.addEventListener('online',refresh);window.addEventListener('offline',refresh);
 window.SonarAgentCards={attach,record,fresh};
})();
