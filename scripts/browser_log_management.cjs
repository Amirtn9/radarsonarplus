// Requires a dedicated local fixture with admin 901, user 902, and four fixture log rows aged 1/30/100/200 hours.
const {chromium}=require('playwright');const crypto=require('node:crypto');const assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL||'https://127.0.0.1:8443',token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['localhost','127.0.0.1'].includes(new URL(base).hostname))throw Error('A local synthetic fixture and SONAR_BROWSER_TEST_TOKEN are required');
function launch(id){const d={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id})};const k=crypto.createHmac('sha256','WebAppData').update(token).digest();d.hash=crypto.createHmac('sha256',k).update(Object.keys(d).sort().map(k=>k+'='+d[k]).join('\n')).digest('hex');return new URLSearchParams({tgWebAppData:new URLSearchParams(d).toString()}).toString();}
(async()=>{const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});try{
 const ctx=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844},timezoneId:'Asia/Tehran'});await ctx.route('https://telegram.org/**',r=>r.abort());
 const page=await ctx.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/#'+launch(901));await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
 await page.evaluate(()=>tabClick('support'));await page.waitForFunction(()=>document.getElementById('managed-log-count').textContent.includes('رویداد'));
 for(const [window,expected] of [['24',1],['48',2],['168',3],['all',4]]){
  await page.locator('#managed-log-window').selectOption(window);const response=page.waitForResponse(r=>r.url().includes('/api/admin/logs?')&&r.status()===200);await page.locator('#managed-log-form button[type=submit]').click();const data=await (await response).json();assert.equal(data.items.filter(r=>r.source==='fixture').length,expected);
 }
 for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:1000});const size=await page.evaluate(()=>({a:document.documentElement.scrollWidth,b:document.documentElement.clientWidth}));assert.ok(size.a<=size.b+1,JSON.stringify({width,...size}));}
 await page.screenshot({path:'/tmp/radar-log-desktop.png',fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/radar-log-mobile.png',fullPage:true});
 page.on('dialog',dialog=>dialog.accept(dialog.type()==='prompt'?'DELETE ALL LOGS':undefined));
 const deleted=page.waitForResponse(r=>r.url().endsWith('/api/admin/logs')&&r.request().method()==='DELETE');await page.locator('#managed-log-delete').click();assert.equal((await deleted).status(),200);
 await page.waitForFunction(()=>!document.getElementById('managed-log-delete').disabled);
 const remaining=await page.evaluate(async()=>await window.SonarWebRequest('/api/admin/logs?window=all'));
 assert.ok(remaining.items.every(r=>r.source!=='fixture'));
 if(process.env.SONAR_BROWSER_TEST_PURGE_JOB==='1'){
  const queued=page.waitForResponse(r=>r.url().endsWith('/api/admin/logs/purge')&&r.request().method()==='POST');
  await page.locator('#managed-log-purge-all').click();assert.equal((await queued).status(),202);
  await page.waitForFunction(()=>!document.getElementById('managed-log-purge-all').disabled);
  const report=await page.locator('#managed-log-purge-result').innerText();
  assert.ok(report.includes('partial')&&report.includes('journal قدیمی مشترک')&&report.includes('نود #'),report);
  assert.ok(report.includes('SYNTHETIC_OFFLINE_NODE'),report);
  for(const width of [320,390,1440]){await page.setViewportSize({width,height:1000});const size=await page.evaluate(()=>({a:document.documentElement.scrollWidth,b:document.documentElement.clientWidth}));assert.ok(size.a<=size.b+1,JSON.stringify({width,...size}));}
  await page.screenshot({path:'/tmp/radar-log-purge-partial.png',fullPage:true});
 }
 const ordinary=await browser.newContext({ignoreHTTPSErrors:true});await ordinary.route('https://telegram.org/**',r=>r.abort());const user=await ordinary.newPage();await user.goto(base+'/#'+launch(902));await user.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
 const status=await user.evaluate(async()=>{try{await window.SonarWebRequest('/api/admin/logs');return 200;}catch(e){return String(e.message)}});assert.notEqual(status,200);assert.equal(await user.locator('#admin-tab').isVisible(),false);
 const purgeDenied=await user.evaluate(async()=>{const response=await fetch('/api/admin/logs/purge',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':window.SonarWebAuth.getCsrf()},body:JSON.stringify({confirmation:'DELETE ALL LOGS',include_remote:true})});return response.status;});assert.equal(purgeDenied,403);
 assert.deepEqual(errors,[]);console.log('PASS: real browser auth, 24/48/168/all windows, 320/390/768/1440 widths, confirmed purge, ordinary-user denial; no page errors');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
