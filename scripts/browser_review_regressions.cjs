// Exercise forms through their visible controls against the disposable SQL fixture.
const {chromium}=require('playwright');
const crypto=require('node:crypto'),assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL,token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local synthetic fixture required');
function launch(){
 const fields={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:901})};
 const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();
 fields.hash=crypto.createHmac('sha256',secret).update(Object.keys(fields).sort().map(k=>k+'='+fields[k]).join('\n')).digest('hex');
 return 'tgWebAppData='+encodeURIComponent(new URLSearchParams(fields).toString());
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844},timezoneId:'Asia/Tehran'});
  await context.route('https://telegram.org/**',route=>route.abort());
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/admin/users?user=902#'+launch());
  await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  await page.evaluate(async()=>{
   const plans=await request('/api/billing/plans?all=true');
   await request('/api/billing/users/902/subscription',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:plans[0].id,status:'active',days:30,overrides:{servers:7,configs:4},reason:'Review form regression fixture'})});
  });
  // A fresh deep link must not depend on first visiting the plan editor.
  await page.reload();
  await page.getByRole('button',{name:'اشتراک',exact:true}).click();
  const quotaCards=page.locator('#customer-dossier .billing-grid').last();
  assert.equal(await quotaCards.locator('article').count(),6);
  assert.ok((await quotaCards.innerText()).includes('ظرفیت اختصاصی این حساب'));
  assert.ok(!(await quotaCards.innerText()).includes('"servers"'));
  await page.getByRole('button',{name:'اطلاعات',exact:true}).click();
  await page.getByRole('button',{name:'ویرایش حساب، اعتبار و پیام',exact:true}).click();
  const editor=page.getByRole('heading',{name:'مدیریت کاربر 902',exact:true}).locator('..');
  const subscription=editor.locator('form').filter({has:page.locator('[name=plan_id]')});
  await subscription.waitFor();
  assert.equal(await subscription.locator('[name=servers]').inputValue(),'7');
  assert.equal(await subscription.locator('[name=configs]').inputValue(),'4');
  await require('./browser_dialogs.cjs')(page,'Review regression reason');
  await subscription.locator('[name=days]').fill('1');
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/billing/users/902/subscription')&&r.request().method()==='PUT');
  await subscription.getByRole('button',{name:'اعمال اشتراک',exact:true}).click();
  assert.equal((await saved).status(),200);
  let account=await page.evaluate(()=>request('/api/billing/users/902'));
  assert.deepEqual(account.subscription.overrides,{servers:7,configs:4});
  await subscription.locator('[name=servers]').fill('0');
  const changed=page.waitForResponse(r=>r.url().endsWith('/api/billing/users/902/subscription')&&r.request().method()==='PUT');
  await subscription.getByRole('button',{name:'اعمال اشتراک',exact:true}).click();
  assert.equal((await changed).status(),200);
  account=await page.evaluate(()=>request('/api/billing/users/902'));
  assert.deepEqual(account.subscription.overrides,{servers:0,configs:4});
  await subscription.locator('[name=servers]').fill('7');
  const restored=page.waitForResponse(r=>r.url().endsWith('/api/billing/users/902/subscription')&&r.request().method()==='PUT');
  await subscription.getByRole('button',{name:'اعمال اشتراک',exact:true}).click();
  assert.equal((await restored).status(),200);
  account=await page.evaluate(()=>request('/api/billing/users/902'));
  assert.deepEqual(account.subscription.overrides,{servers:7,configs:4});
  const wallet=editor.locator('form').filter({has:page.locator('[name=amount]')});
  const before=Number(account.user.wallet_balance);
  await wallet.locator('[name=amount]').fill('0.1');
  const credited=page.waitForResponse(r=>r.url().endsWith('/api/billing/users/902/wallet')&&r.request().method()==='POST');
  await wallet.getByRole('button',{name:'ثبت تغییر موجودی',exact:true}).click();
  assert.equal((await credited).status(),200);
  assert.equal(Number((await page.evaluate(()=>request('/api/billing/users/902'))).user.wallet_balance),before+1);
  // Creating and editing a destination must await the actual audit reason.
  await page.evaluate(()=>window.SonarProduct.navigate('payments'));
  await page.getByRole('button',{name:'افزودن مقصد',exact:true}).click();
  const destination=page.getByRole('heading',{name:'تنظیم مقصد پرداخت',exact:true}).locator('..').locator('form');
  await destination.locator('[name=name]').fill('Review destination');
  await destination.locator('[name=address]').fill('synthetic-public-card-only');
  const created=page.waitForResponse(r=>r.url().endsWith('/api/billing/destinations')&&r.request().method()==='POST');
  await destination.getByRole('button',{name:'ذخیره مقصد',exact:true}).click();
  assert.equal((await created).status(),200);
  await page.locator('#users article').filter({hasText:'Review destination'}).getByRole('button',{name:'ویرایش / غیرفعال‌سازی',exact:true}).click();
  const edit=page.getByRole('heading',{name:'تنظیم مقصد پرداخت',exact:true}).locator('..').locator('form');
  await edit.locator('[name=enabled]').selectOption('false');
  const updated=page.waitForResponse(r=>/\/api\/billing\/destinations\/\d+$/.test(r.url())&&r.request().method()==='PUT');
  await edit.getByRole('button',{name:'ذخیره مقصد',exact:true}).click();
  assert.equal((await updated).status(),200);
  const row=await page.evaluate(async()=>(await request('/api/billing/destinations?all=true')).find(d=>d.name==='Review destination'));
  assert.equal(row.enabled,false);
  await page.locator('#users article').filter({hasText:'Review destination'}).getByText('کارت بانکی · غیرفعال',{exact:false}).waitFor();
  for(const width of [320,390,768,1440]){
   await page.setViewportSize({width,height:844});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
  }
  await page.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+'/review-payment-forms.png',fullPage:true});
  await page.evaluate(async()=>{
   const configs=await request('/api/resources/config');
   window.SonarConsole.openDetails('config',configs.find(config=>config.last_status==='OK').id);
  });
  const score=page.locator('#config-details .console-detail-cell').filter({has:page.getByText('امتیاز',{exact:true})}).locator('strong');
  await score.waitFor();
  assert.ok((await score.innerText()).endsWith('/10'));
  assert.ok(!(await score.innerText()).endsWith('/100'));
  await context.route('**/api/fleet/relays',route=>route.fulfill({json:{nodes:[
   {id:1,name:'Current agent',ip:'192.0.2.1',port:22,ws_port:8443,owned_by_admin:true},
   {id:2,name:'Offline agent',ip:'192.0.2.2',port:22,ws_port:8443,owned_by_admin:true}
  ],fallback_order:[],policy:'balanced'}}));
  let versionChecks=0;
  await context.route('**/api/fleet/versions',route=>{
   versionChecks++;assert.deepEqual(route.request().postDataJSON().server_ids,[1,2]);
   return route.fulfill({json:{expected_version:'6.9.10',nodes:[
    {id:1,name:'Current agent',state:'current',agent_version:'6.9.10',agent_protocol:2,
     agent_build:'a'.repeat(64),xray_version:'v26.10.31',xray_version_state:'current',
     xray_compat_version:'v26.6.27',xray_compat_version_state:'current',checked_at:new Date().toISOString()},
    {id:2,name:'Offline agent',state:'unknown',xray_version_state:'unknown',
     xray_compat_version_state:'unknown',checked_at:new Date().toISOString()}
   ]}});
  });
  await page.evaluate(()=>tabClick('balancer'));
  await page.locator('#relay-candidates').getByText('Current agent',{exact:true}).waitFor();
  await page.locator('#relay-version-check').click();
  await page.locator('#relay-versions').getByText('Current agent · ✅ مطابق نسخه مرکزی',{exact:true}).waitFor();
  await page.locator('#relay-versions').getByText('Offline agent · ⚪ نامشخص',{exact:true}).waitFor();
  assert.equal(versionChecks,1);
  await page.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+'/agent-versions.png',fullPage:true});
  const requestedPages=[];
  await context.route('**/api/resources/server?*',route=>{
   const index=Number(new URL(route.request().url()).searchParams.get('page')||1);
   requestedPages.push(index);
   const all=Array.from({length:13},(_,i)=>({id:1000+i,name:`Pagination server ${i+1}`,ip:'192.0.2.1',is_active:1,last_status:'Unknown',favorite:false}));
   return route.fulfill({json:all.slice((index-1)*12,index*12),headers:{'X-Total-Count':'13'}});
  });
  await page.evaluate(()=>tabClick('servers'));
  await page.locator('#list-content').getByText('Pagination server 1',{exact:true}).waitFor();
  await page.evaluate(async()=>{state.index=999;await loadList();});
  assert.ok(requestedPages.includes(999)&&requestedPages.includes(2));
  assert.equal(await page.evaluate(()=>state.index),2);
  await page.locator('#list-content').getByText('Pagination server 13',{exact:true}).waitFor();
  assert.equal(await page.locator('#list-content .empty-state').count(),0);
  assert.equal(await page.locator('#next').isDisabled(),true);
  assert.equal(await page.locator('#prev').isDisabled(),false);
  const invalidRequests=requestedPages.filter(index=>index===999).length;
  await page.goBack();
  await page.waitForFunction(()=>state.tab==='servers'&&state.index===1);
  assert.equal(requestedPages.filter(index=>index===999).length,invalidRequests);
  assert.deepEqual(errors,[]);
  console.log('PASS: direct-link permissions, preserved quotas, financial forms, Agent versions and recovery from an out-of-range resource page.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
