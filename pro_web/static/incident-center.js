'use strict';
(()=>{
 const fields={severity:'incident-severity',resource_type:'incident-resource-type',resource_id:'incident-resource-id',after:'incident-after',before:'incident-before',sort:'incident-sort'};
 const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);return el;};
 const local=value=>new Date(window.SonarTelemetry.time(value)).toLocaleString('fa-IR');
 const labels={opened:'شروع اختلال',repeated:'تکرار اختلال',acknowledged:'تأیید مشاهده',resolved:'رفع اختلال',updated:'تغییر رخداد',status_changed:'تغییر وضعیت',note:'یادداشت ثبت‌شده',incident_transition:'اقدام کاربر'};
 function inputTime(value){if(!value)return '';const d=new Date(value);if(!Number.isFinite(d.valueOf()))return '';return new Date(d.valueOf()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
 function sync(){for(const [key,id]of Object.entries(fields)){const input=document.getElementById(id);if(document.activeElement===input)continue;input.value=['after','before'].includes(key)?inputTime(state.incidentFilters[key]):state.incidentFilters[key];}}
 function apply(){for(const [key,id]of Object.entries(fields)){const value=document.getElementById(id).value;state.incidentFilters[key]=value&&['after','before'].includes(key)?new Date(value).toISOString():value;}state.index=1;if(state.tab==='incidents')loadList();}
 for(const id of Object.values(fields))document.getElementById(id)?.addEventListener('change',apply);
 document.getElementById('incident-filter')?.addEventListener('submit',event=>{event.preventDefault();apply();});
 let activeClose=null;
 function open(id,push=true){
  if(activeClose)activeClose(false);
  state.incidentId=id;if(push&&state.tab==='incidents')updateRoute();
  const previous=document.activeElement,dialog=node('dialog');dialog.className='sonar-dialog incident-dialog';
  const title=node('h2','تاریخچه رخداد');title.id='incident-dialog-title';dialog.setAttribute('aria-labelledby',title.id);
  const content=node('div'),close=node('button','بستن');close.type='button';
  dialog.append(title,content,close);document.body.append(dialog);let sequence=0;
  const navigationClose=()=>finish(false);
  function finish(update=true){sequence++;window.removeEventListener('sonar:view-change',navigationClose);dialog.close();dialog.remove();activeClose=null;state.incidentId=null;if(update&&state.tab==='incidents')updateRoute(true);if(previous?.isConnected)previous.focus();}
  activeClose=finish;close.onclick=()=>finish();dialog.addEventListener('cancel',event=>{event.preventDefault();finish();});window.addEventListener('sonar:view-change',navigationClose);
  async function load(page=1){const serial=++sequence;content.replaceChildren(node('p','در حال دریافت تاریخچه…'));
   try{
    const result=await request(`/api/incidents/${id}?page=${page}`);if(serial!==sequence||!dialog.isConnected)return;
    const last=Math.max(1,Math.ceil(result.total/20));if(page>last)return load(last);
    content.replaceChildren(node('h3',result.incident.title),node('p',`شروع ثبت‌شده: ${local(result.incident.opened_at)} · ${result.total} رویداد ثبت‌شده`));
    content.append(node('p','فقط رویدادهای ذخیره‌شده نمایش داده می‌شوند؛ تاریخچه رخدادهای قدیمی ممکن است ناقص باشد.'));
    const list=node('ol');list.className='incident-timeline';
    if(!result.events.length)content.append(node('p','تاریخچه‌ای برای این رخداد ثبت نشده است.'));
    for(const event of result.events){const item=node('li');item.append(node('strong',labels[event.event]||'تغییر ثبت‌شده'),node('p',local(event.created_at)+(event.actor_id?' · کاربر '+event.actor_id:'')));if(event.status)item.append(node('p',`وضعیت: ${({open:'باز',acknowledged:'دیده‌شده',investigating:'در حال بررسی',resolved:'رفع‌شده'})[event.status]||event.status} · دفعات: ${event.occurrences}`));if(event.note)item.append(node('p',event.note));list.append(item);}
    content.append(list);const pager=node('div');pager.className='dialog-actions';
    for(const [text,next,disabled]of [['قبلی',page-1,page===1],['بعدی',page+1,page===last]]){const button=node('button',text);button.type='button';button.disabled=disabled;button.onclick=()=>load(next);pager.append(button);}
    pager.append(node('span',`صفحه ${page} از ${last}`));content.append(pager);
   }catch(error){if(serial===sequence)content.replaceChildren(node('p',error.message));}
  }
  dialog.showModal();close.focus();load();
 }
 window.SonarIncidents={sync,open};
})();
