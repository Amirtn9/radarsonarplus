'use strict';
const {chromium}=require('playwright'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL,token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local fixture required');
function launch(uid=901){const data={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:uid})};const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();data.hash=crypto.createHmac('sha256',secret).update(Object.keys(data).sort().map(k=>k+'='+data[k]).join('\n')).digest('hex');return 'tgWebAppData='+encodeURIComponent(new URLSearchParams(data).toString());}
(async()=>{
 const monitorName='Final mobile monitor '+Date.now();
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844},timezoneId:'Asia/Tehran'});await context.route('https://telegram.org/**',r=>r.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#'+launch());await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  await page.waitForFunction(()=>!!SonarExperience.capabilities());
  await page.evaluate(()=>{
   window.__tgEvents={};window.__tgBackVisible=false;window.Telegram={WebApp:{safeAreaInset:{top:24,bottom:24},contentSafeAreaInset:{top:36,bottom:16},onEvent:(name,fn)=>window.__tgEvents[name]=fn,BackButton:{onClick:fn=>window.__tgBack=fn,show:()=>window.__tgBackVisible=true,hide:()=>window.__tgBackVisible=false}}};tabClick('services');
  });
  await page.waitForFunction(()=>typeof window.__tgBack==='function');
  assert.equal(await page.evaluate(()=>document.documentElement.style.getPropertyValue('--app-safe-top')),'36px');
  await page.locator('[data-mobile-tab=more]').click();assert.equal(await page.locator('#app-shell').evaluate(el=>el.inert),true);
  const last=page.locator('#quick-menu a').last();await last.focus();await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('#quick-menu button')),true);
  await page.evaluate(()=>window.__tgBack());assert.equal(await page.locator('#quick-menu').isVisible(),false);assert.equal(await page.locator('#app-shell').evaluate(el=>el.inert),false);assert.equal(await page.evaluate(()=>document.activeElement.dataset.mobileTab),'more');
  await page.locator('#context-help summary').click();await page.locator('#context-help').getByRole('button',{name:'خواندن آموزش مرتبط بدون خروج',exact:true}).click();
  let modal=page.getByRole('dialog');await modal.getByRole('heading',{name:/مانیتورینگ سرویس/}).waitFor();assert.equal(await page.evaluate(()=>state.tab),'services');await page.evaluate(()=>window.__tgBack());await modal.waitFor({state:'detached'});
  // Static disabled controls must never be labelled as pending work.
  await page.evaluate(()=>tabClick('servers'));await page.waitForFunction(()=>document.getElementById('list-content').getAttribute('aria-busy')!=='true');
  assert.equal(await page.locator('#prev').isDisabled(),true);assert.equal(await page.locator('#prev').getAttribute('aria-busy'),null);
  const server=(await(await context.request.get(base+'/api/resources/server?q='+encodeURIComponent('سرور آزمایشی گزارش'))).json())[0];assert.ok(server);
  await page.route('**/api/console/servers/'+server.id,async route=>{const response=await route.fetch(),data=await response.json();data.last_checked_at=new Date(Date.now()-600000).toISOString();data.ssh={state:'host_key_changed',attempted_at:new Date().toISOString()};await route.fulfill({response,json:data});});
  await page.evaluate(id=>SonarConsole.openDetails('server',id),server.id);await page.getByRole('tab',{name:'اتصال',exact:true}).click();await page.locator('#context-help-content').getByText(/اثر انگشت را از کنسول مستقل/).waitFor();
  await page.getByRole('tab',{name:'منابع',exact:true}).click();await page.locator('#context-help-content').getByText(/نمونه منابع قدیمی یا ناموجود/).waitFor();
  await context.setOffline(true);await page.locator('#context-help-content').getByText(/مرورگر آفلاین است/).waitFor();await context.setOffline(false);
  await page.evaluate(()=>tabClick('overview'));await page.keyboard.press('Control+k');modal=page.getByRole('dialog');await page.waitForFunction(()=>window.__tgBackVisible);
  await modal.locator('input').fill('Synthetic');
  await page.evaluate(()=>{Object.defineProperty(window.visualViewport,'height',{configurable:true,get:()=>420});Object.defineProperty(window.visualViewport,'offsetTop',{configurable:true,get:()=>30});SonarExperience.safeAreas();});
  await page.waitForFunction(()=>document.body.classList.contains('keyboard-open'));assert.equal(await page.locator('#bottom-nav').isVisible(),false);
  const box=await modal.boundingBox();assert.ok(box.y>=30&&box.y+box.height<=450,JSON.stringify(box));
  await modal.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+'/keyboard-search.png'});
  await page.evaluate(()=>window.__tgBack());await modal.waitFor({state:'detached'});
  await page.evaluate(()=>{delete window.visualViewport.height;delete window.visualViewport.offsetTop;document.activeElement?.blur();SonarExperience.safeAreas();});
  await page.waitForFunction(()=>!document.body.classList.contains('keyboard-open'));await page.waitForFunction(()=>window.__tgBackVisible===false);
  // Real mutations prove busy/success/error states and duplicate prevention.
  await page.evaluate(()=>tabClick('services'));await page.waitForFunction(()=>document.getElementById('service-form').dataset.uxDenied==='false');
  await page.locator('#service-name').fill(monitorName);await page.locator('#service-target').fill('https://example.com/mobile');
  let mode='delayed',release,started;const begin=new Promise(resolve=>started=resolve);let posts=0,deletes=0;
  await page.route('**/api/services',async route=>{if(route.request().method()!=='POST')return route.continue();posts++;if(mode==='delayed'){started();await new Promise(resolve=>release=resolve);}if(mode==='failed')return route.fulfill({status:503,json:{detail:'Synthetic unavailable'}});return route.continue();});
  page.on('request',request=>{if(request.method()==='DELETE'&&request.url().includes('/api/services/'))deletes++;});
  await page.locator('#service-save').click();await begin;
  assert.equal(await page.locator('#service-save').getAttribute('data-action-state'),'busy');assert.equal(await page.locator('#service-save').getAttribute('aria-busy'),'true');assert.equal(await page.locator('#service-form').evaluate(el=>el.inert),true);
  await page.evaluate(()=>document.getElementById('service-form').requestSubmit(document.getElementById('service-save')));await page.waitForTimeout(100);assert.equal(posts,1);release();
  await page.waitForFunction(()=>document.getElementById('service-save').dataset.actionState==='success'&&!document.getElementById('service-save').disabled);assert.equal(await page.locator('#service-save').getAttribute('aria-busy'),null);
  mode='failed';await page.locator('#service-name').fill('Final failed monitor');await page.locator('#service-target').fill('https://example.com/failed');await page.locator('#service-save').click();await page.waitForFunction(()=>document.getElementById('service-save').dataset.actionState==='error');assert.equal(await page.locator('#service-save').isEnabled(),true);assert.equal(await page.locator('#service-form').evaluate(el=>el.inert),false);
  const created=page.locator('.service-card').filter({hasText:monitorName}),remove=created.getByRole('button',{name:'حذف',exact:true});
  assert.equal(await remove.getAttribute('data-action-variant'),'destructive');await remove.click();modal=page.getByRole('dialog');await modal.getByRole('button',{name:'انصراف',exact:true}).click();await modal.waitFor({state:'detached'});await page.waitForFunction(()=>document.activeElement.getAttribute('aria-label')==='حذف');assert.equal(deletes,0);
  // Representative forms, cards, filters, details and menus under increased text size.
  const views=['overview','servers','configs','services','incidents','fleet','settings','team','billing','users','reports','enterprise','support'];let checks=0;
  for(const width of [320,360,390,430,820,1440])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:1000});await page.evaluate(theme=>SonarTheme.set(theme),theme);
   for(const view of views){await page.evaluate(view=>tabClick(view),view);await page.waitForFunction(()=>pendingReads.size===0&&!document.querySelector('.module-status.route-loading'));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`${view}/${width}/${theme}`);checks++;}
   await page.evaluate(()=>tabClick('services'));await page.locator('#service-form').waitFor();
   const bad=await page.locator('#service-form').evaluate(form=>[...form.querySelectorAll('input,select')].some(input=>input.getBoundingClientRect().width<120));assert.equal(bad,false);
   if(width===390||width===1440)await page.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/context-mobile-${theme}-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:320,height:900});
  await page.evaluate(()=>{const sheet=[...document.styleSheets].find(sheet=>sheet.href?.includes('/static/liquid.css'));window.__largeTextSheet=sheet;window.__largeTextRule=sheet.insertRule('body,body button,body input,body select,body textarea{font-size:20px!important;line-height:1.8!important}',sheet.cssRules.length);});
  for(const view of ['services','configs','incidents','settings','fleet']){await page.evaluate(view=>tabClick(view),view);await page.waitForFunction(()=>pendingReads.size===0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`large text ${view}`);}
  await page.locator('[data-mobile-tab=more]').click();assert.ok(await page.locator('#quick-menu').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.keyboard.press('Escape');await page.evaluate(()=>window.__largeTextSheet.deleteRule(window.__largeTextRule));
  const viewerContext=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});await viewerContext.route('https://telegram.org/**',r=>r.abort());const viewer=await viewerContext.newPage();viewer.on('pageerror',e=>errors.push(e.message));await viewer.goto(base+'/#'+launch(903));await viewer.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  // The full suite creates a consent-based Viewer membership in workspace 902.
  const option=viewer.locator('#workspace-selector option[value="902"]');assert.equal(await option.count(),1,'Viewer workspace fixture must exist');await viewer.locator('#workspace-selector').selectOption('902');await viewer.waitForFunction(()=>SonarExperience.capabilities()?.role==='viewer');await viewer.evaluate(()=>tabClick('services'));await viewer.locator('#context-help summary').click();await viewer.locator('#context-help-content').getByText(/این نقش فقط اجازه مشاهده دارد/).waitFor();assert.equal(await viewer.locator('#context-help').getByRole('button',{name:'رفتن به فرم مانیتور',exact:true}).count(),0);
  assert.deepEqual(errors,[]);console.log(`PASS: shared action states and cancellation, protected pending form, ${checks} responsive view/theme checks, large text, keyboard focus, inline role/state help, Telegram safe areas/back and simulated visual keyboard.`);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
