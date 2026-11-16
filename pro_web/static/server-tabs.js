'use strict';
(()=>{
 const byId=id=>document.getElementById(id),root=byId('server-detail');if(!root)return;
 const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);return el;};
 const names={overview:'نمای کلی',resources:'منابع',connection:'اتصال',history:'تاریخچه',operations:'عملیات'};
 const labels={checking:'در حال بررسی',success:'موفق',sudo_required:'SSH برقرار؛ دسترسی sudo لازم است',auth_failed:'خطای احراز هویت',host_key_unknown:'کلید میزبان تأیید نشده',host_key_changed:'کلید میزبان تغییر کرده',network_failed:'خطای شبکه',unknown:'نتیجه نامشخص'};
 function label(ssh){if(!ssh)return 'بررسی‌نشده';const text=labels[ssh.state]||labels.unknown;const age=Date.now()-window.SonarTelemetry.time(ssh.attempted_at);if(ssh.state==='checking'&&age>120000)return 'بررسی ناتمام؛ نتیجه نامشخص';return age>180000?text+'؛ نتیجه قدیمی':text;}
 const time=value=>value?new Date(window.SonarTelemetry.time(value)).toLocaleString('fa-IR'):'ثبت نشده';
 window.SonarSSH={label};
 const nav=node('div');nav.className='server-tabs';nav.setAttribute('role','tablist');nav.setAttribute('aria-label','بخش‌های جزئیات سرور');
 root.querySelector('.detail-header').after(nav);const panes={};let selected='overview',historySequence=0;
 const overview=byId('server-details'),sensors=root.querySelector('.sensor-panel'),chart=byId('detail-chart').closest('section.panel'),operations=byId('web-ssh-check').closest('section.panel'),reachability=byId('reachability-run').closest('section.panel');
 for(const [key,title]of Object.entries(names)){
  const button=node('button',title);button.type='button';button.id='server-tab-'+key;button.setAttribute('role','tab');button.setAttribute('aria-controls','server-pane-'+key);button.onclick=()=>select(key);
  button.onkeydown=event=>{const keys=Object.keys(names);let index=keys.indexOf(selected);if(event.key==='ArrowRight')index--;else if(event.key==='ArrowLeft')index++;else if(event.key==='Home')index=0;else if(event.key==='End')index=keys.length-1;else return;event.preventDefault();select(keys[(index+keys.length)%keys.length]);nav.querySelector('[aria-selected=true]').focus();};
  nav.append(button);const pane=node('div');pane.id='server-pane-'+key;pane.setAttribute('role','tabpanel');pane.setAttribute('aria-labelledby',button.id);pane.tabIndex=0;panes[key]=pane;root.append(pane);
 }
 panes.overview.append(overview);panes.resources.append(sensors,chart);panes.operations.append(operations);panes.connection.append(reachability);
 const connection=node('section');connection.className='panel';connection.id='server-connection-details';panes.connection.prepend(connection);
 const history=node('section');history.className='panel';history.id='server-recorded-history';panes.history.append(history);
 function select(key,push=true){
  selected=Object.hasOwn(names,key)?key:'overview';
  for(const name of Object.keys(names)){panes[name].hidden=name!==selected;const tab=byId('server-tab-'+name);tab.setAttribute('aria-selected',String(name===selected));tab.tabIndex=name===selected?0:-1;}
  if(push&&state.tab==='server-detail')updateRoute();
  if(selected==='history'&&state.tab==='server-detail')loadHistory();
  if(selected==='resources')window.dispatchEvent(new Event('resize'));
 }
 function setInfo(info){
  connection.replaceChildren(node('h3','اتصال و اعتماد'));
  const facts=[['SSH',label(info.ssh)],['آخرین تلاش SSH',time(info.ssh?.attempted_at)],['پایان آخرین تلاش',time(info.ssh?.completed_at)],['آخرین SSH موفق',time(info.ssh_last_success_at)],
   ['آخرین وضعیت ثبت‌شده WSS',({Online:'آنلاین',Offline:'آفلاین'})[info.last_agent_status]||'نامشخص'],['گواهی TLS',info.tls_enrolled?'ثبت شده؛ تأیید ارتباط زنده نیست':'ثبت نشده']];
  const list=node('dl');list.className='connection-facts';for(const [key,value]of facts){const cell=node('div');cell.append(node('dt',key),node('dd',value));list.append(cell);}connection.append(list);
  connection.append(node('p','موفقیت WSS تأیید SSH نیست. وضعیت SSH نتیجه یک تلاش ثبت‌شده است و سلامت لحظه‌ای را تضمین نمی‌کند.'));
  const action=node('button','بررسی SSH و اعتماد میزبان');action.type='button';action.dataset.requires='technical';action.onclick=()=>select('operations');connection.append(action);
 }
 async function loadHistory(){
  const sid=window.SonarConsole?.currentServerId(),serial=++historySequence;if(!sid)return;
  history.replaceChildren(node('p','در حال دریافت تاریخچه ثبت‌شده…'));
  try{
   const data=await request(`/api/console/servers/${sid}/history`);if(serial!==historySequence||sid!==window.SonarConsole.currentServerId())return;
   history.replaceChildren(node('h3','تاریخچه ثبت‌شده سرور'),node('p','آخرین ۲۰ مورد هر بخش؛ فقط داده ذخیره‌شده نمایش داده می‌شود.'));
   for(const [title,rows,render]of [
    ['بررسی‌های SSH',data.checks,row=>`${time(row.attempted_at)} · ${label(row)}${row.error_code?' · '+row.error_code:''}`],
    ['نمونه‌های منابع',data.observations,row=>`${time(row.created_at)} · CPU ${row.cpu??'—'}٪ · RAM ${row.ram??'—'}٪ · Disk ${row.disk??'—'}٪`]]){
    history.append(node('h4',title));const list=node('ol');for(const row of rows)list.append(node('li',render(row)));history.append(rows.length?list:node('p','داده‌ای ثبت نشده است.'));
   }
   history.append(node('h4','رخدادهای این سرور'));for(const item of data.incidents){const button=node('button',item.title+' · '+time(item.updated_at));button.type='button';button.onclick=()=>window.SonarIncidents.open(item.id);history.append(button);}
   if(!data.incidents.length)history.append(node('p','رخدادی ثبت نشده است.'));
  }catch(error){if(serial===historySequence)history.replaceChildren(node('p',error.message));}
 }
 window.addEventListener('sonar:view-change',()=>{historySequence++;if(state.tab==='server-detail')select(new URLSearchParams(location.search).get('section')||'overview',false);});
 window.SonarServerTabs={select,current:()=>selected,setInfo};select('overview',false);
})();
