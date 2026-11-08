'use strict';
// Server authorization remains authoritative; rendering always uses textContent.
window.SonarLogs=(()=>{
 const el=id=>document.getElementById(id);
 let page=1,revision=0;
 function text(tag,value){const node=document.createElement(tag);node.textContent=String(value);return node;}
 async function load(reset=false){
  const host=el('managed-log-list');if(!host)return;
  if(reset)page=1;
  const version=++revision;
  const params=new URLSearchParams({window:el('managed-log-window').value,level:el('managed-log-level').value,category:el('managed-log-category').value,page:String(page)});
  const custom=params.get('window')==='custom';el('managed-log-custom').hidden=!custom;
  host.replaceChildren(text('p','در حال دریافت لاگ‌ها…'));
  try{
   if(custom){
    for(const key of ['start','end']){
     const value=el('managed-log-'+key).value;
     if(!value)throw Error('ابتدا زمان شروع و پایان را انتخاب کنید.');
     params.set(key,new Date(value).toISOString());
    }
   }
   const data=await window.SonarWebRequest('/api/admin/logs?'+params);
   if(version!==revision)return;
   host.replaceChildren();
   for(const row of data.items){
    const card=text('article','');card.className='item';
    const body=text('div','');
    body.append(text('strong',row.level+' · '+row.category+' · '+row.source),
      text('p',new Date(row.created_at).toLocaleString('fa-IR')+' · '+row.status),text('pre',row.message));
    card.append(body);host.append(card);
   }
   if(!data.items.length)host.append(text('p','لاگی در این بازه وجود ندارد.'));
   el('managed-log-count').textContent=`${data.total} رویداد · صفحه ${page}`;
   el('managed-log-prev').disabled=page<=1;
   el('managed-log-next').disabled=page*data.per_page>=data.total;
  }catch(error){if(version===revision){host.replaceChildren(text('p',error.message));el('managed-log-count').textContent='';el('managed-log-next').disabled=true;}}
 }
 el('managed-log-form')?.addEventListener('submit',event=>{event.preventDefault();void load(true);});
 el('managed-log-window')?.addEventListener('change',()=>{el('managed-log-custom').hidden=el('managed-log-window').value!=='custom';});
 el('managed-log-prev')?.addEventListener('click',()=>{if(page>1){page--;void load();}});
 el('managed-log-next')?.addEventListener('click',()=>{page++;void load();});
 el('managed-log-delete')?.addEventListener('click',async()=>{
  if(!confirm('تمام لاگ‌های پایگاه داده و تاریخچه فعالیت همه کاربران برای همیشه پاک شوند؟ این عملیات بازگشت ندارد.'))return;
  const confirmation=prompt('برای تأیید نهایی عبارت DELETE ALL LOGS را دقیق وارد کنید.');
  if(confirmation!=='DELETE ALL LOGS')return;
  const button=el('managed-log-delete');button.disabled=true;
  try{
   await window.SonarWebRequest('/api/admin/logs',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation})});
   // Invalidate requests already in flight and cached activity DOM immediately.
   revision++;window.dispatchEvent(new Event('sonar:logs-cleared'));el('managed-log-list').replaceChildren();
   await load(true);
  }catch(error){el('managed-log-count').textContent=error.message;}
  finally{button.disabled=false;}
 });
 return {load};
})();
