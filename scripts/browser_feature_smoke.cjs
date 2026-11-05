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
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
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
  await page.goto(base+'/guide#'+launch(901));
  await page.waitForFunction(()=>document.getElementById('audience').textContent.includes('مدیر تأییدشده'));
  assert.ok(await page.locator('#admin-fleet').count());
  await page.goto(base+'/guide#'+launch(901,3600));
  await page.waitForFunction(()=>document.getElementById('audience').textContent.includes('مدیر تأییدشده'));
  assert.ok(await page.locator('#admin-fleet').count());
  await page.locator('#guide-search').fill('وایرگارد');
  await page.waitForFunction(()=>document.getElementById('guide-status').textContent.includes('پیدا شد'));
  assert.ok(await page.locator('#configs details:not([hidden])').count());
  await page.locator('#guide-search').fill('');
  await page.screenshot({path:output+'/guide-mobile.png',fullPage:false});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:output+'/guide-desktop.png',fullPage:false});
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
