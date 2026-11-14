'use strict';
// Same API and components in standalone Web and Telegram Mini Apps.
(()=>{
 let capabilities=[];const can=capability=>capabilities.includes(capability);
 const resources={servers:'سرورها',configs:'کانفیگ‌ها',websites:'وب‌سایت‌ها',agents:'Agentها',team:'اعضای تیم',destinations:'مقصدهای اطلاع‌رسانی'};
 const labels={active:'فعال',expired:'منقضی',suspended:'تعلیق',cancelled:'لغو',pending:'در انتظار بررسی',paid:'پرداخت‌شده',rejected:'ردشده',refunded:'بازگشت به کیف پول'};
 const money=value=>Number(value||0).toLocaleString('fa-IR')+' ریال';
 const key=()=>crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-');
 const api=(path,options)=>window.SonarWebRequest('/api/billing'+path,options);
 const post=(path,value,method='POST')=>api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
 const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
 const button=(text,handler,cls='')=>{const node=el('button',text,cls);node.type='button';node.onclick=async()=>{node.disabled=true;try{await handler();}catch(error){toast(error.message);}finally{node.disabled=false;}};return node;};
 const row=(title,detail)=>{const n=el('article',null,'item');const d=el('div');d.append(el('strong',title),el('p',detail,'muted'));n.append(d);return n;};
 const section=(root,title)=>{const p=el('section',null,'panel');p.append(el('h2',title));root.append(p);return p;};
 function field(form,label,name,value='',type='text',options=null){
  const wrap=el('label',label),input=el(options?'select':'input');input.name=name;
  if(options){for(const [v,t] of options){const op=el('option',t);op.value=v;input.append(op);}}
  else{input.type=type;if(type==='number'){input.min='0';input.step='1';}input.maxLength=1000;}
  input.value=String(value??'');input.required=true;wrap.append(input);form.append(wrap);return input;
 }
 function submit(form,label,handler){const b=el('button',label,'primary');b.type='submit';form.append(b);const status=el('p','','muted');status.setAttribute('role','status');form.append(status);form.onsubmit=async e=>{e.preventDefault();b.disabled=true;status.textContent='در حال ذخیره…';try{await handler(new FormData(form));status.textContent='ذخیره شد';}catch(error){status.textContent=error.message;}finally{b.disabled=false;}};}
 function reason(){const value=prompt('دلیل این تغییر را وارد کنید:');if(!value||value.trim().length<3)throw Error('دلیل حداقل سه‌حرفی لازم است');return value.trim();}
 async function loadCustomer(host){
  host.replaceChildren();const account=await api('/account'),plans=await api('/plans'),destinations=await api('/destinations');
  const summary=section(host,'اشتراک خدمات پایش'),sub=account.subscription;
  summary.append(el('p',sub?`${sub.snapshot.name} · ${labels[sub.effective_status]||sub.effective_status} · اعتبار تا ${sub.expires_at?new Date(sub.expires_at).toLocaleString('fa-IR'):'بدون انقضا'}`:'حساب با تنظیمات قبلی؛ مدیر می‌تواند پلن جدید اختصاص دهد.'));
  if(sub?.snapshot?.policy)summary.append(el('p',`حداقل فاصله پایش ${sub.snapshot.policy.min_interval_seconds} ثانیه · نگهداری تاریخچه ${sub.snapshot.policy.retention_days} روز`));
  summary.append(el('strong','موجودی کیف پول: '+money(account.user.wallet_balance)));
  const usage=el('div',null,'billing-grid');for(const [name,title] of Object.entries(resources))usage.append(row(title,`${account.usage[name]||0} از ${sub?.limits?.[name]??'ظرفیت قبلی'}`));summary.append(usage);
  const catalog=section(host,'انتخاب و تمدید پلن');
  for(const plan of plans){const card=row(plan.name,`${money(plan.price)} · ${plan.days} روز · ${plan.description||''}${plan.legacy_terms?' · شرایط محفوظ اشتراک قبلی':''}`);const detail=el('p',Object.entries(resources).map(([k,v])=>`${v}: ${plan.limits[k]}`).join(' | '));card.firstChild.append(detail);
   card.append(button('انتخاب پلن',()=>checkoutForm(host,plan,destinations),'primary'));catalog.append(card);}
  const orders=section(host,'سفارش‌ها و رسیدهای پرداخت');
  if(!account.orders.length)orders.append(el('p','هنوز سفارشی ثبت نشده است.'));
  for(const order of account.orders){const item=row(`${money(order.amount)} · ${labels[order.status]||order.status}`,new Date(order.created_at).toLocaleString('fa-IR'));
   if(order.status==='pending')item.append(button('لغو پیش از پرداخت',async()=>{if(!confirm('فقط اگر پرداخت نکرده‌اید ادامه دهید. سفارش لغو شود؟'))return;await post('/orders/'+order.id+'/cancel',{});await loadCustomer(host);}),button('ثبت مشخصات رسید',async()=>{const text=prompt('شماره پیگیری بانکی، شناسه تراکنش ارز دیجیتال یا شناسه رسید ارسال‌شده به مدیر را وارد کنید. اطلاعات محرمانه یا کلید خصوصی نفرستید.');if(!text)return;await post('/orders/'+order.id+'/evidence',{text});toast('رسید برای بررسی مدیر ثبت شد');}));orders.append(item);}
  const ledger=section(host,'گردش کیف پول');for(const entry of account.wallet)ledger.append(row(money(entry.amount),`${entry.reason} · مانده ${money(entry.balance)} · ${new Date(entry.created_at).toLocaleString('fa-IR')}`));
 }
 function checkoutForm(host,plan,destinations){
  document.getElementById('billing-checkout')?.remove();const box=section(host,'تأیید خرید '+plan.name);box.id='billing-checkout';box.append(el('p','مبالغ پلن به ریال است. برای ارز دیجیتال، مقدار و شبکه را با دستور پرداخت مدیر تطبیق دهید. ارسال رسید به‌تنهایی اشتراک را فعال نمی‌کند. سفارش بدون رسید پس از ۲۴ ساعت منقضی می‌شود.'));
  const form=el('form',null,'billing-form');box.append(form);
  field(form,'روش پرداخت','method','wallet','text',[['wallet','کیف پول'],...destinations.map(d=>['destination-'+d.id,`${d.kind==='crypto'?'ارز دیجیتال':'کارت'} · ${d.name} · ${d.network}`])]);
  const instruction=el('p','','payment-instructions');form.append(instruction);
  const discount=field(form,'کد تخفیف (اختیاری)','discount','','text');discount.required=false;
  const choice=form.elements.method;choice.onchange=()=>{const dest=destinations.find(d=>'destination-'+d.id===choice.value);instruction.textContent=dest?`${dest.name} · شبکه ${dest.network||'بانکی'} · ${dest.address} · ${dest.holder} · ${dest.instructions}`:'مبلغ پس از اعمال تخفیف از کیف پول کسر می‌شود.';};choice.onchange();
  // The same key is retained if a network timeout makes the result uncertain.
  const idempotency=key();submit(form,'ثبت سفارش و پرداخت',async data=>{const method=data.get('method');const order=await post('/orders',{plan_id:plan.id,method:method==='wallet'?'wallet':'manual',idempotency_key:idempotency,destination_id:method==='wallet'?null:Number(method.replace('destination-','')),discount_code:String(data.get('discount')||'').trim()||null});toast(order.status==='paid'?'اشتراک فعال شد':'سفارش ثبت شد؛ پس از پرداخت مشخصات رسید را ثبت کنید.');await loadCustomer(host);});box.scrollIntoView({block:'start',behavior:'smooth'});
 }
 async function loadAdmin(host){
  host.replaceChildren();const me=await request('/api/me');capabilities=me.billing_permissions||[];if(!me.admin&&!capabilities.length){host.append(el('p','مجوز مدیریت ندارید.'));return;}
  if(can('users.read')){
  const toolbar=section(host,'مدیریت کاربران، اشتراک و پرداخت');const search=el('input');search.placeholder='نام یا شناسه کاربر';search.setAttribute('aria-label','جست‌وجوی کاربران');toolbar.append(search);
  const result=el('div');toolbar.append(button('جست‌وجو',()=>users(result,search.value)),...(can('users.write')?[button('ایجاد کاربر',()=>userEditor(host,{user_id:Number(prompt('شناسه عددی تلگرام کاربر:')),full_name:'کاربر جدید',is_banned:0}))]:[]),result);await users(result,'');
  }
  const plans=await api('/plans?all=true'),destinations=await api('/destinations?all=true');
  if(can('plans')){
  const catalog=section(host,'پلن‌ها و سهمیه‌های مستقل');catalog.append(button('ایجاد پلن',()=>planEditor(host,null)));
  for(const p of plans){const item=row(p.name,`${money(p.price)} · ${p.days} روز · ${p.enabled?'فعال':'غیرفعال'}`);item.append(button('ویرایش',()=>planEditor(host,p)),button('حذف بدون سابقه',async()=>{await post('/plans/'+p.id,{reason:reason()},'DELETE');await loadAdmin(host);}));catalog.append(item);}
  }
  if(can('destinations')){
  const dest=section(host,'مقصدهای پرداخت کارت و ارز دیجیتال');dest.append(button('افزودن مقصد',()=>destinationEditor(host,null)));for(const d of destinations){const item=row(d.name,`${d.kind==='crypto'?'ارز دیجیتال':'کارت بانکی'} · ${d.network} · ${d.address} · ${d.enabled?'فعال':'غیرفعال'}`);item.append(button('ویرایش / غیرفعال‌سازی',()=>destinationEditor(host,d)));dest.append(item);}
  }
  if(can('payments')){
  const payments=section(host,'بررسی پرداخت‌ها');const select=el('select');for(const [value,title] of Object.entries(labels)){if(!['pending','paid','rejected','refunded'].includes(value))continue;const op=el('option',title);op.value=value;select.append(op);}payments.append(select);const payrows=el('div');payments.append(payrows);
  const loadOrders=async()=>{payrows.replaceChildren();const orders=await api('/orders?status='+select.value);for(const o of orders){const item=row(`کاربر ${o.user_id} · ${money(o.amount)}`,`${o.snapshot.name} · ${labels[o.status]} · رسید: ${o.evidence||'ثبت نشده'}`);
   if(o.evidence?.startsWith('telegram-photo:'))item.append(button('دریافت تصویر رسید',async()=>{const response=await api('/orders/'+o.id+'/receipt',{raw:true}),url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='receipt.jpg';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}));
   if(o.status==='pending'){item.append(button('تأیید پرداخت واقعی',async()=>{if(!confirm('واریز واقعی و مبلغ و شبکه را مستقل بررسی کرده‌اید؟'))return;await post('/orders/'+o.id+'/decision',{approve:true,reason:reason()});await loadOrders();}),button('رد پرداخت',async()=>{await post('/orders/'+o.id+'/decision',{approve:false,reason:reason()});await loadOrders();}));}
   if(o.status==='paid')item.append(button('بازگشت به کیف پول و تعلیق اشتراک',async()=>{if(!confirm('مبلغ به کیف پول برمی‌گردد و اشتراک تعلیق می‌شود. ادامه؟'))return;await post('/orders/'+o.id+'/refund',{reason:reason()});await loadOrders();}));payrows.append(item);}if(!orders.length)payrows.append(el('p','پرداختی با این وضعیت یافت نشد.'));};select.onchange=loadOrders;await loadOrders();
  }
  if(can('discounts')){
  const discounts=section(host,'تخفیف‌ها');discounts.append(button('ساخت کد تخفیف',()=>discountEditor(host)));for(const d of await api('/discounts'))discounts.append(row(d.code,`${d.kind==='percent'?d.value+'٪':money(d.value)} · مصرف ${d.uses}/${d.max_uses} · رزرو ${d.reserved||0} · تا ${new Date(d.ends_at).toLocaleString('fa-IR')}`));
  }
  if(can('reports')){
  const finance=section(host,'گزارش مالی ۳۰ روز گذشته');for(const f of await api('/finances'))finance.append(row(`${new Date(f.bucket_day).toLocaleDateString('fa-IR')} · پلن ${f.plan_id}`,`${labels[f.status]} · ${f.orders} سفارش · ${money(f.amount)} · ${f.method}`));
  }
  if(can('audit')){
  const audit=section(host,'سوابق تغییرات مالی و مدیریتی');for(const a of await api('/audit'))audit.append(row(`${a.event} · مدیر ${a.actor_id} · کاربر ${a.user_id||'—'}`,`${a.reason} · ${new Date(a.created_at).toLocaleString('fa-IR')}`));  }
  if(me.admin)await staffPanel(host);
 }
 async function users(host,q){host.replaceChildren();for(const u of await api('/users?q='+encodeURIComponent(q))){const item=row(u.full_name||String(u.user_id),`شناسه ${u.user_id} · ${u.is_banned?'مسدود':'فعال'} · ${u.plan||'قدیمی'} · ${money(u.wallet_balance)} · آخرین فعالیت ${u.last_active_at?new Date(u.last_active_at).toLocaleString('fa-IR'):'نامشخص'}`);item.append(button('پروفایل و مدیریت',()=>userEditor(document.getElementById('users'),u)));host.append(item);}}
 async function userEditor(host,user){if(!Number.isSafeInteger(Number(user.user_id))||Number(user.user_id)<1)throw Error('شناسه معتبر نیست');const p=section(host,'مدیریت کاربر '+user.user_id);if(can('users.write')){const f=el('form',null,'billing-form');p.append(f);
  field(f,'نام','full_name',user.full_name||'کاربر');field(f,'وضعیت حساب','banned',user.is_banned?'true':'false','text',[['false','فعال'],['true','مسدود']]);submit(f,'ذخیره حساب',async d=>{await post('/users/'+user.user_id,{full_name:d.get('full_name'),banned:d.get('banned')==='true',reason:reason()},'PUT');});
  }
  if(can('subscriptions')){const plans=await api('/plans?all=true'),sub=el('form',null,'billing-form');p.append(el('h3','اشتراک و ظرفیت اختصاصی'),sub);field(sub,'پلن','plan_id',plans[0]?.id||'', 'text',plans.map(x=>[String(x.id),x.name]));field(sub,'روز اضافه یا کم‌شده','days',0,'number').min='-3660';field(sub,'وضعیت','status','active','text',[['active','فعال'],['suspended','تعلیق'],['cancelled','لغو']]);
  const overrides={};for(const [k,title] of Object.entries(resources)){const input=field(sub,'ظرفیت اختصاصی '+title+' (خالی: پلن)',k,'','number');input.required=false;overrides[k]=input;}
  submit(sub,'اعمال اشتراک',async d=>{const values={};for(const k of Object.keys(resources))if(d.get(k)!=='')values[k]=Number(d.get(k));await post('/users/'+user.user_id+'/subscription',{plan_id:Number(d.get('plan_id')),days:Number(d.get('days')),status:d.get('status'),overrides:values,reason:reason()},'PUT');});
  }
  if(can('wallet')){const wallet=el('form',null,'billing-form');p.append(el('h3','اصلاح موجودی کیف پول'),wallet);field(wallet,'مبلغ ریال (منفی برای کسر)','amount',0,'number').min='-100000000000000';let walletKey=key();submit(wallet,'ثبت تغییر موجودی',async d=>{await post('/users/'+user.user_id+'/wallet',{amount:Number(d.get('amount')),idempotency_key:walletKey,reason:reason()});walletKey=key();});
  }
  if(can('messages')){const f=el('form',null,'billing-form');p.append(el('h3','ارسال پیام خصوصی از ربات'),f);field(f,'متن پیام','message_text','');let messageKey=key();submit(f,'افزودن پیام به صف خصوصی',async data=>{await post('/users/'+user.user_id+'/message',{text:data.get('message_text'),request_key:messageKey,reason:reason()});messageKey=key();toast('پیام در صف ارسال خصوصی قرار گرفت');});for(const m of await api('/messages?user_id='+user.user_id)){const item=row('پیام '+m.id,m.state+' · تلاش '+m.attempts+' · '+(m.error_code||''));if(m.state==='failed')item.append(button('تلاش مجدد',()=>post('/messages/'+m.id+'/retry',{})));p.append(item);}}
  try{const account=await api('/users/'+user.user_id);p.append(el('p',`ثبت‌نام ${account.user.registered_at?new Date(account.user.registered_at).toLocaleString('fa-IR'):'نامشخص'} · موجودی ${money(account.user.wallet_balance)} · وضعیت ${labels[account.subscription?.effective_status]||'قدیمی'}`));for(const entry of account.wallet)p.append(row(money(entry.amount),entry.reason));}catch(error){p.append(el('p',error.message));}p.scrollIntoView({behavior:'smooth'});
 }
 function planEditor(host,plan){const p=section(host,plan?'ویرایش '+plan.name:'پلن جدید'),f=el('form',null,'billing-form');p.append(f);
  for(const [name,title,type,value] of [['name','نام','text',plan?.name||''],['description','توضیحات','text',plan?.description||''],['price','قیمت ریال','number',plan?.price||0],['days','مدت روز','number',plan?.days||30],['priority','ترتیب نمایش','number',plan?.priority||0]])field(f,title,name,value,type);f.elements.description.required=false;f.elements.priority.min='-10000';
  field(f,'حداقل فاصله پایش (ثانیه)','min_interval_seconds',plan?.policy?.min_interval_seconds??60,'number');field(f,'نگهداری تاریخچه (روز؛ ۱ تا ۳۰)','retention_days',plan?.policy?.retention_days??30,'number');
  for(const [k,title] of Object.entries(resources))field(f,'ظرفیت '+title,k,plan?.limits[k]??0,'number');
  for(const [key,title] of Object.entries({advanced_reports:'گزارش پیشرفته',api:'API سازمانی',automation:'خودکارسازی',backups:'پشتیبان‌گیری',agents:'Agent'}))field(f,title,'feature_'+key,String(plan?.features?.[key]??true),'text',[['true','فعال'],['false','غیرفعال']]);
  field(f,'وضعیت','enabled',String(plan?.enabled??true),'text',[['true','فعال'],['false','غیرفعال']]);field(f,'اعمال تغییرات ظرفیت و تمدید','apply',plan?.change_policy||'new','text',[['new','فقط خرید جدید'],['renewal','تمدید بعدی'],['current','اشتراک‌های فعلی هم تغییر کنند']]);
  submit(f,'ذخیره پلن',async d=>{const limits={};for(const k of Object.keys(resources))limits[k]=Number(d.get(k));await post('/plans'+(plan?'/'+plan.id:''),{name:d.get('name'),description:d.get('description'),price:Number(d.get('price')),days:Number(d.get('days')),priority:Number(d.get('priority')),limits,policy:{min_interval_seconds:Number(d.get('min_interval_seconds')),retention_days:Number(d.get('retention_days'))},features:Object.fromEntries(['advanced_reports','api','automation','backups','agents'].map(k=>[k,d.get('feature_'+k)==='true'])),enabled:d.get('enabled')==='true',apply:d.get('apply'),reason:reason()},plan?'PUT':'POST');await loadAdmin(host);});p.scrollIntoView({behavior:'smooth'});
 }
 function destinationEditor(host,dest){const p=section(host,'تنظیم مقصد پرداخت'),f=el('form',null,'billing-form');p.append(f);field(f,'نوع','kind',dest?.kind||'card','text',[['card','کارت بانکی'],['crypto','ارز دیجیتال']]);for(const [k,title] of [['name','نام مقصد'],['network','شبکه (مانند TRC20؛ برای رمزارز الزامی)'],['address','شماره کارت یا آدرس کیف پول'],['holder','نام دارنده'],['instructions','راهنمای مبلغ و پرداخت']])field(f,title,k,dest?.[k]||'');for(const k of ['network','holder','instructions'])f.elements[k].required=false;field(f,'وضعیت','enabled',String(dest?.enabled??true),'text',[['true','فعال'],['false','غیرفعال']]);
  submit(f,'ذخیره مقصد',async d=>{const body=Object.fromEntries(d);body.enabled=d.get('enabled')==='true';body.reason=reason();await post('/destinations'+(dest?'/'+dest.id:''),body,dest?'PUT':'POST');await loadAdmin(host);});p.scrollIntoView({behavior:'smooth'});
 }
 function discountEditor(host){const p=section(host,'کد تخفیف جدید'),f=el('form',null,'billing-form');p.append(f);field(f,'کد','code','');field(f,'نوع','kind','percent','text',[['percent','درصد'],['fixed','مبلغ ثابت ریال']]);field(f,'مقدار','value',10,'number');field(f,'حداکثر مصرف','max_uses',100,'number');field(f,'شروع','starts_at','','datetime-local');field(f,'پایان','ends_at','','datetime-local');
  for(const [k,title] of [['user_id','فقط کاربر (اختیاری)'],['plan_id','فقط پلن (اختیاری)']])field(f,title,k,'','number').required=false;field(f,'فقط خرید اول','first_only','false','text',[['false','خیر'],['true','بله']]);field(f,'فقط تمدید','renewal_only','false','text',[['false','خیر'],['true','بله']]);
  submit(f,'ثبت تخفیف',async d=>{await post('/discounts',{code:String(d.get('code')).toUpperCase(),kind:d.get('kind'),value:Number(d.get('value')),max_uses:Number(d.get('max_uses')),starts_at:new Date(d.get('starts_at')).toISOString(),ends_at:new Date(d.get('ends_at')).toISOString(),user_id:d.get('user_id')?Number(d.get('user_id')):null,plan_id:d.get('plan_id')?Number(d.get('plan_id')):null,first_only:d.get('first_only')==='true',renewal_only:d.get('renewal_only')==='true',enabled:true,reason:reason()});await loadAdmin(host);});p.scrollIntoView({behavior:'smooth'});
 }
 async function staffPanel(host){
  const sectionNode=section(host,'نقش‌های فروش، مالی و پشتیبانی');
  sectionNode.append(button('افزودن مجوز مدیریتی',()=>staffEditor(host,null)));
  for(const member of await api('/staff')){const item=row('کاربر '+member.user_id+' · '+member.label,member.capabilities.join('، ')+' · '+(member.enabled?'فعال':'غیرفعال'));item.append(button('ویرایش / ابطال',()=>staffEditor(host,member)));sectionNode.append(item);}
 }
 function staffEditor(host,member){
  const p=section(host,'مجوزهای مدیریتی'),f=el('form',null,'billing-form');p.append(f);
  field(f,'شناسه عددی کاربر','user_id',member?.user_id||'','number');field(f,'عنوان نقش','label',member?.label||'مدیر مالی');
  const presets=field(f,'الگوی نقش','preset','custom','text',[['custom','اختصاصی'],['finance','مدیر مالی'],['sales','مدیر فروش'],['support','پشتیبان']]);
  const names={'users.read':'مشاهده کاربران','users.write':'ویرایش و مسدودسازی کاربران',plans:'مدیریت پلن‌ها',subscriptions:'مدیریت اشتراک‌ها',wallet:'تغییر موجودی کیف پول',payments:'تأیید و بازگشت پرداخت',destinations:'مقصدهای پرداخت',discounts:'تخفیف‌ها',reports:'گزارش مالی',audit:'سوابق مدیریتی',messages:'ارسال پیام خصوصی به کاربران'};
  const checks={};for(const [key,title] of Object.entries(names)){const wrap=el('label',title),input=el('input');input.type='checkbox';input.checked=(member?.capabilities||[]).includes(key);wrap.append(input);f.append(wrap);checks[key]=input;}
  presets.onchange=()=>{const values={finance:['users.read','wallet','payments','destinations','reports','audit'],sales:['users.read','users.write','plans','subscriptions','discounts','reports'],support:['users.read','audit','messages']}[presets.value];if(values)for(const key of Object.keys(checks))checks[key].checked=values.includes(key);};
  field(f,'وضعیت نقش','enabled',String(member?.enabled??true),'text',[['true','فعال'],['false','باطل']]);
  submit(f,'ذخیره مجوزها',async data=>{await post('/staff/'+Number(data.get('user_id')),{label:data.get('label'),capabilities:Object.keys(checks).filter(k=>checks[k].checked),enabled:data.get('enabled')==='true',reason:reason()},'PUT');await loadAdmin(host);});p.scrollIntoView({behavior:'smooth'});
 }
 window.SonarBilling={async load(name){const host=document.getElementById(name);try{host.replaceChildren(el('p','در حال دریافت…'));if(name==='users')await loadAdmin(host);else await loadCustomer(host);}catch(error){host.replaceChildren(el('p',error.message),button('تلاش دوباره',()=>window.SonarBilling.load(name)));}}};
})();
