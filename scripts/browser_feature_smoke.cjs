'use strict';
// Local synthetic test server only. Never point this fixture at production.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const fs=require('node:fs');
const {chromium}=require('playwright');
const base=process.env.SONAR_BROWSER_TEST_URL||'https://127.0.0.1:8443';
if(!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local test server required');
const token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token)throw Error('SONAR_BROWSER_TEST_TOKEN must match the synthetic test server');
const output=process.env.SONAR_BROWSER_TEST_OUTPUT||'/tmp/sonar-browser-artifacts';
fs.mkdirSync(output,{recursive:true});
function launch(uid,ageSeconds=0){
 const fields={auth_date:String(Math.floor(Date.now()/1000)-ageSeconds),user:JSON.stringify({id:uid})};
 const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();
 const check=Object.keys(fields).sort().map(key=>`${key}=${fields[key]}`).join('\n');
 fields.hash=crypto.createHmac('sha256',secret).update(check).digest('hex');
 return new URLSearchParams({tgWebAppData:new URLSearchParams(fields).toString(),tgWebAppVersion:'8.0',tgWebAppPlatform:'android'}).toString();
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  // Local fixture uses a self-signed certificate; public TLS checks remain enabled.
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});
  await context.route('https://telegram.org/**',route=>route.abort());
  const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/?view=settings#'+launch(901));
  await page.waitForFunction(()=>document.getElementById('web-auth').hidden&&document.getElementById('topbar-status').textContent==='متصل');
  assert.equal(await page.locator('#session-warning').isVisible(),false);
  await page.locator('#ca-save').waitFor({state:'visible'});
  await page.waitForFunction(()=>!document.getElementById('ca-save').disabled);
  const explanation=page.locator('#ca-failure_threshold').locator('..').locator('.field-help');
  await explanation.locator('summary').click();assert.ok(await explanation.locator('p').isVisible());await explanation.locator('summary').click();
  await page.locator('#ca-notify_score').check();
  await page.locator('#ca-probe_interval_minutes').fill('5');
  await page.locator('#ca-score_delta').fill('15');
  await page.locator('#ca-save').click();
  await page.getByText('✓ تنظیمات اعلان و زمان‌بندی با موفقیت ذخیره شد.').waitFor();
  assert.equal((await (await context.request.get(base+'/api/config-alerts')).json()).score_delta,15);
  await page.evaluate(()=>window.SonarTheme.set('dark'));
  const background=await page.locator('#ca-enabled').evaluate(element=>getComputedStyle(element.parentElement).backgroundColor);
  assert.ok(background.match(/[0-9.]+/g).slice(0,3).every(value=>Number(value)<150));
  await page.screenshot({path:output+'/settings-dark-mobile.png',fullPage:true});
  await page.evaluate(()=>window.SonarTheme.set('light'));
  await page.screenshot({path:output+'/settings-mobile.png',fullPage:true});
  // Regressions: history must survive region errors and rapid monitor changes.
  const service=(id,name)=>({id,name,kind:'http',target:'https://example.com',enabled:true,status:'up',interval:60});
  await page.route('**/api/services',route=>route.fulfill({json:[service(7001,'History Alpha'),service(7002,'History Beta')]}));
  await page.route('**/api/services/*/history?days=7',async route=>{
   const first=route.request().url().includes('/7001/');
   if(first)await new Promise(resolve=>setTimeout(resolve,400));
   await route.fulfill({json:[{ok:true,detail:first?'HTTP Alpha 200':'HTTP Beta 200',latency_ms:12,checked_at:new Date().toISOString()}]});
  });
  await page.route('**/api/services/*/regions?days=7',route=>route.fulfill({status:503,json:{detail:'Synthetic regional outage'}}));
  await page.goto(base+'/?view=services');
  const alpha=page.locator('#service-grid').locator('article').filter({hasText:'History Alpha'});
  const beta=page.locator('#service-grid').locator('article').filter({hasText:'History Beta'});
  await alpha.getByRole('button',{name:'تاریخچه',exact:true}).click();
  await page.locator('#service-history').getByText('HTTP Alpha 200',{exact:false}).waitFor();
  await page.locator('#service-regions .bad').waitFor();
  assert.ok((await page.locator('#service-history').innerText()).includes('HTTP Alpha 200'));
  const pending=page.waitForRequest(request=>request.url().includes('/7001/history'));
  await alpha.getByRole('button',{name:'تاریخچه',exact:true}).click();await pending;
  await beta.getByRole('button',{name:'تاریخچه',exact:true}).click();
  await page.locator('#service-history').getByText('HTTP Beta 200',{exact:false}).waitFor();
  await page.waitForTimeout(600);
  assert.ok((await page.locator('#service-history-title').innerText()).includes('History Beta'));
  assert.ok(!(await page.locator('#service-history').innerText()).includes('HTTP Alpha 200'));
  await page.unroute('**/api/services');
  await page.unroute('**/api/services/*/history?days=7');
  await page.unroute('**/api/services/*/regions?days=7');
  await page.goto(base+'/?view=support');
  await page.waitForFunction(()=>document.getElementById('web-auth').hidden);
  assert.deepEqual(await page.locator('#diagnostic-days option').evaluateAll(items=>items.map(item=>Number(item.value)*24)),[3,6,12,24,48,72,168]);
  await page.route('**/api/diagnostics/bundle?**',route=>route.fulfill({status:200,contentType:'application/zip',body:Buffer.from('PK synthetic download fixture')}));
  for(const hours of [3,6,12,24,48,72,168]){
   await page.locator('#diagnostic-days').selectOption(String(hours/24));
   const request=page.waitForRequest(req=>req.url().includes('/api/diagnostics/bundle?'));
   const download=page.waitForEvent('download');
   await page.locator('#download-btn').click();
   const sent=new URL((await request).url());
   assert.equal(Number(sent.searchParams.get('days'))*24,hours);
   assert.equal(sent.searchParams.get('include_remote'),'true');
   assert.ok((await download).suggestedFilename().endsWith(`-${hours}h.zip`));
  }
  await page.unroute('**/api/diagnostics/bundle?**');
  await page.goto(base+'/guide#'+launch(901));
  await page.waitForFunction(()=>document.getElementById('audience').textContent.includes('کاربر و مدیر'));
  assert.ok(await page.locator('#admin-fleet').count());
  await page.goto(base+'/guide#'+launch(901,3600));
  await page.waitForFunction(()=>document.getElementById('audience').textContent.includes('کاربر و مدیر'));
  assert.ok(await page.locator('#admin-fleet').count());
  await page.locator('#guide-search').fill('وایرگارد');
  await page.waitForFunction(()=>document.getElementById('guide-status').textContent.includes('پیدا شد'));
  assert.ok(await page.locator('#configs details:not([hidden])').count());
  await page.locator('#guide-search').fill('');
  await page.screenshot({path:output+'/guide-mobile.png',fullPage:false});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:output+'/guide-desktop.png',fullPage:false});
  await page.goto(base+'/?view=reports#'+launch(901));
  await page.waitForSelector('#reports-servers tr');
  assert.ok((await page.locator('#reports-servers').innerText()).includes('سرور آزمایشی گزارش'));
  const download=page.waitForEvent('download');await page.locator('#reports-export').click();
  assert.equal((await download).suggestedFilename(),'sonar-servers.csv');
  await page.screenshot({path:output+'/reports-desktop.png',fullPage:false});
  await page.goto(base+'/#'+launch(901));
  await page.waitForFunction(()=>document.getElementById('resource-server').value!=='');
  for(const hours of ['1','3','12','24','48']){
   await page.locator('#resource-hours').selectOption(hours);
   await page.waitForFunction(()=>document.getElementById('resource-message').textContent.includes('نمونه ثبت'));
  }
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>window.SonarTheme.set('dark'));
  await page.screenshot({path:output+'/dashboard-dark-mobile.png',fullPage:true});
  const dimensions=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
  assert.ok(dimensions.scroll<=dimensions.width+1,JSON.stringify(dimensions));
  await context.close();
  const user=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844},reducedMotion:'reduce'});
  await user.route('https://telegram.org/**',route=>route.abort());
  const ordinary=await user.newPage();ordinary.on('pageerror',error=>errors.push(error.message));
  await ordinary.goto(base+'/guide#'+launch(902));
  await ordinary.waitForFunction(()=>document.getElementById('chapter-content').children.length>=10);
  assert.equal(await ordinary.locator('#admin-fleet').count(),0);
  const visible=await (await user.request.get(base+'/api/guide?admin=true')).json();
  assert.ok(visible.chapters.every(chapter=>!chapter.admin));
  await user.close();
  const web=await browser.newContext({ignoreHTTPSErrors:true});
  await web.route('https://telegram.org/**',route=>route.abort());
  const website=await web.newPage();website.on('pageerror',error=>errors.push(error.message));
  await website.goto(base+'/');await website.locator('#web-auth').waitFor({state:'visible'});
  assert.equal(await website.locator('#session-warning').isVisible(),false);
  await web.close();assert.deepEqual(errors,[]);
  console.log('PASS Mini App automatic login with Telegram CDN blocked; cookie+CSRF settings save; administrator and ordinary guide; Persian search; desktop/mobile rendering; standalone web manual-login form; no JavaScript exceptions.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
