'use strict';
// Read-only, owner-scoped subscription states. Do not render subscription URLs.
(() => {
 const host=document.getElementById('subscription-summary');
 const items=document.getElementById('subscription-items');
 if(!host||!items)return;
 const labels={saved:'✅ لینک ذخیره شده • دریافت هنوز آغاز نشده',fetching:'⏳ دریافت لینک اشتراک',
  testing:'🔎 کانفیگ‌ها دریافت شده‌اند • در حال تست',ready:'✅ آخرین همگام‌سازی انجام شده',
  waiting_agent:'⚪️ لینک ذخیره شده • Agent آماده وجود ندارد',failed:'⚠️ خطا در آخرین دریافت یا ذخیره؛ داده قبلی حفظ شده'};
 const node=(tag,txt,cls)=>{const n=document.createElement(tag);n.textContent=String(txt||'');if(cls)n.className=cls;return n;};
 const date=raw=>{if(!raw)return 'هنوز تکمیل نشده';const d=new Date(raw);return Number.isFinite(d.getTime())?d.toLocaleString('fa-IR'):'—';};
 let loading=false;
 async function refresh(){
  host.hidden=state.tab!=='configs';if(host.hidden||loading)return;
  loading=true;items.replaceChildren(node('p','در حال خواندن وضعیت اشتراک‌ها…','muted'));
  try{
   const data=await request('/api/subscriptions');
   if(state.tab!=='configs')return;
   items.replaceChildren();const sources=data.subscriptions||[];
   if(!sources.length){items.append(node('p','هیچ لینک اشتراکی ثبت نشده است.','muted'));return;}
   for(const sub of sources){
    const card=node('article',null,'subscription-state');
    card.append(node('strong',sub.name||`اشتراک ${sub.id}`));
    card.append(node('p',labels[sub.refresh_status]||'وضعیت نامشخص','muted'));
    card.append(node('p',`ذخیره‌شده: ${sub.stored_count} کانفیگ • آخرین نتیجه سالم: ${sub.tested_healthy_count} • دریافت‌شده: ${sub.extracted_count??'—'}`,'muted'));
    card.append(node('small',`آخرین همگام‌سازی موفق: ${date(sub.last_success_at)}${sub.last_error_code?' • علت: '+sub.last_error_code:''}`,'muted'));
    items.append(card);
   }
  }catch(e){items.replaceChildren(node('p','خطا در دریافت وضعیت اشتراک‌ها: '+e.message,'bad'));}
  finally{loading=false;}
 }
 document.querySelectorAll('[data-tab="configs"], [data-mobile-tab="configs"], [data-go="configs"]').forEach(b=>b.addEventListener('click',()=>queueMicrotask(refresh)));
 document.getElementById('refresh')?.addEventListener('click',()=>queueMicrotask(refresh));
 setInterval(()=>{if(state.tab==='configs')refresh();},30000);
})();
