'use strict';
const {chromium}=require('playwright'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL,token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local fixture required');
function launch(uid=901){const data={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:uid})};const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();data.hash=crypto.createHmac('sha256',secret).update(Object.keys(data).sort().map(k=>k+'='+data[k]).join('\n')).digest('hex');return 'tgWebAppData='+encodeURIComponent(new URLSearchParams(data).toString());}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000},timezoneId:'Asia/Tehran'});await context.route('https://telegram.org/**',r=>r.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let writes=0;
  page.on('request',req=>{if(req.method()==='POST'&&req.url().includes('/api/operations/'))writes++;});
  await page.goto(base+'/#'+launch());await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  const seed=await page.evaluate(()=>jsonPost('/__fixture/incidents',{}));
  await page.goto(base+'/incidents?q=Synthetic%20incident');
  await page.locator('#list-content .item').first().waitFor();assert.equal(await page.locator('#list-content .item').count(),12);
  await page.locator('#next').click();await page.waitForFunction(()=>state.index===2&&document.getElementById('list-content').getAttribute('aria-busy')!=='true');
  await page.locator('#next').click();await page.waitForFunction(()=>state.index===3&&document.getElementById('list-content').getAttribute('aria-busy')!=='true');assert.equal(await page.locator('#list-content .item').count(),2);
  await page.locator('#incident-severity').selectOption('critical');await page.waitForFunction(()=>state.index===1&&document.getElementById('list-content').getAttribute('aria-busy')!=='true');
  const total=await context.request.get(base+'/api/incidents?q=Synthetic%20incident&severity=critical&page=999');assert.equal(total.headers()['x-total-count'],'13');assert.deepEqual(await total.json(),[]);
  await page.locator('#incident-resource-type').selectOption('server');await page.locator('#incident-resource-id').fill(String(seed.server_id));await page.locator('#incident-resource-id').press('Tab');
  await page.locator('#incident-sort').selectOption('oldest');
  await page.waitForFunction(()=>document.getElementById('list-content').getAttribute('aria-busy')!=='true');
  await page.locator('#incident-after').fill('2099-01-01T00:00');await page.locator('#incident-after').press('Tab');
  await page.waitForFunction(()=>document.getElementById('list-content').getAttribute('aria-busy')!=='true'&&document.querySelectorAll('#list-content .item').length===0);
  await page.locator('#incident-after').fill('');await page.locator('#incident-after').press('Tab');await page.locator('#list-content .item').first().waitFor();
  const card=page.locator('#list-content .item').filter({hasText:'Synthetic incident 00'});await card.getByRole('button',{name:'تاریخچه و جزئیات'}).click();
  let dialog=page.getByRole('dialog');await dialog.getByText('شروع اختلال',{exact:true}).waitFor();await dialog.getByRole('button',{name:'بستن',exact:true}).click();
  await card.getByRole('button',{name:'تأیید مشاهده',exact:true}).click();await page.waitForFunction(()=>document.getElementById('list-content').getAttribute('aria-busy')!=='true');
  await card.getByRole('button',{name:'تاریخچه و جزئیات'}).click();dialog=page.getByRole('dialog');await dialog.getByText('تأیید مشاهده',{exact:true}).waitFor();assert.ok((await dialog.innerText()).includes('کاربر 901'));await page.keyboard.press('Escape');
  await page.reload();await page.locator('#list-content .item').first().waitFor();assert.equal(await page.locator('#incident-severity').inputValue(),'critical');assert.equal(await page.locator('#incident-resource-id').inputValue(),String(seed.server_id));
  await page.locator('#incident-severity').selectOption('warning');await page.waitForURL(/severity=warning/);await page.goBack();await page.waitForFunction(()=>document.getElementById('incident-severity').value==='critical');
  for(const theme of ['dark','light'])for(const width of [320,390,820,1440]){await page.setViewportSize({width,height:1000});await page.evaluate(theme=>SonarTheme.set(theme),theme);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));await page.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/incident-filters-${theme}-${width}.png`,fullPage:true});}
  await page.goto(base+'/incidents?q=Synthetic%20incident&severity=critical&page=999');
  await page.waitForFunction(()=>state.index===2&&document.getElementById('list-content').getAttribute('aria-busy')!=='true');
  assert.equal(await page.locator('#list-content .item').count(),1);assert.equal(new URL(page.url()).searchParams.get('page'),'2');
  await page.goto(base+`/servers/${seed.server_id}?section=connection`);
  await page.getByRole('tab',{name:'اتصال',exact:true}).waitFor();await page.locator('#server-connection-details').getByText('بررسی‌نشده',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>SonarConsole.currentServerId()),seed.server_id);assert.equal(writes,0);
  const getInfo=async()=>await(await context.request.get(base+`/api/console/servers/${seed.server_id}`)).json();
  assert.equal((await getInfo()).ssh,null);
  for(const state of ['success','auth_failed','host_key_unknown']){
   await page.getByRole('tab',{name:'عملیات',exact:true}).click();
   const response=page.waitForResponse(r=>r.url().endsWith('/check-ssh')&&r.request().method()==='POST');await page.locator('#web-ssh-check').click();assert.equal((await(await response).json()).state,state);
   await page.waitForFunction(()=>!document.getElementById('web-ssh-check').disabled);
   await page.getByRole('tab',{name:'اتصال',exact:true}).click();await page.waitForFunction(state=>document.getElementById('server-connection-details').textContent.includes(({success:'موفق',auth_failed:'خطای احراز هویت',host_key_unknown:'کلید میزبان تأیید نشده'})[state]),state);
   assert.ok((await getInfo()).ssh_last_success_at);
  }
  assert.equal(writes,3);assert.equal(await page.locator('#web-existing-ssh-confirm-form').isVisible(),false);
  await page.getByRole('tab',{name:'تاریخچه',exact:true}).click();await page.locator('#server-recorded-history').getByText(/AUTH_FAILED/).waitFor();
  assert.ok((await page.locator('#server-recorded-history').innerText()).includes('HOST_KEY_UNKNOWN'));
  await page.getByRole('tab',{name:'منابع',exact:true}).click();await page.waitForFunction(()=>Number(document.getElementById('detail-chart').dataset.sampleCount)>0);await page.locator('#detail-chart').focus();await page.keyboard.press('ArrowLeft');
  await page.reload();await page.waitForFunction(()=>document.getElementById('server-tab-resources').getAttribute('aria-selected')==='true');
  await page.getByRole('tab',{name:'اتصال',exact:true}).click();await page.goBack();await page.waitForFunction(()=>document.getElementById('server-tab-resources').getAttribute('aria-selected')==='true');
  for(const theme of ['dark','light'])for(const width of [320,390,820,1440]){await page.setViewportSize({width,height:1000});await page.evaluate(theme=>SonarTheme.set(theme),theme);for(const name of ['نمای کلی','منابع','اتصال','تاریخچه','عملیات']){await page.getByRole('tab',{name,exact:true}).click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));}await page.getByRole('tab',{name:'اتصال',exact:true}).click();await page.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/server-connection-${theme}-${width}.png`,fullPage:true});}
  assert.equal(writes,3);await page.goto(base+'/servers');await page.locator('.server-card').first().waitFor();assert.ok((await page.locator('.server-card').filter({hasText:'سرور آزمایشی گزارش'}).innerText()).includes('SSH: کلید میزبان تأیید نشده'));
  const other=await browser.newContext({ignoreHTTPSErrors:true});await other.route('https://telegram.org/**',r=>r.abort());const member=await other.newPage();await member.goto(base+'/#'+launch(902));await member.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  assert.equal((await other.request.get(base+`/api/incidents/${seed.incident_ids[0]}`)).status(),404);assert.equal((await other.request.get(base+`/api/console/servers/${seed.server_id}/history`)).status(),404);
  assert.deepEqual(errors,[]);console.log('PASS: incident filters/counts/pagination/real Timeline, scoped history, independent persisted SSH states, no implicit probes, five accessible tabs, charts, direct links/back/reload and responsive themes.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
