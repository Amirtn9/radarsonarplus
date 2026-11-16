'use strict';
const {chromium}=require('playwright');
const crypto=require('node:crypto'),assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL,token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local fixture required');
function launch(uid=901){
 const data={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:uid})};
 const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();
 data.hash=crypto.createHmac('sha256',secret).update(Object.keys(data).sort().map(k=>k+'='+data[k]).join('\n')).digest('hex');
 return 'tgWebAppData='+encodeURIComponent(new URLSearchParams(data).toString());
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}});
  await context.route('https://telegram.org/**',route=>route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  let versions=0;
  await context.route('**/api/fleet/versions',route=>{
   versions++;return route.fulfill({json:{expected_version:'6.9.10',nodes:route.request().postDataJSON().server_ids.map(id=>({id,name:'Synthetic identity',state:'current',agent_version:'6.9.10',agent_build:'a'.repeat(64),checked_at:new Date().toISOString()}))}});
  });
  await page.clock.install();
  await page.goto(base+'/?view=balancer#'+launch());
  await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  await page.evaluate(()=>jsonPost('/__fixture/fleet',{}));
  await page.locator('#relay-pool-refresh').click();
  const alpha=page.locator('#relay-candidates .agent-card').filter({hasText:'Relay Alpha'}),beta=page.locator('#relay-candidates .agent-card').filter({hasText:'Relay Beta'}),stale=page.locator('#relay-candidates .agent-card').filter({hasText:'Relay Stale'});
  await alpha.waitFor();
  assert.equal(versions,0); // No automatic per-card identity probes.
  assert.ok((await alpha.innerText()).includes('تأییدشده در آخرین heartbeat'));
  assert.ok((await alpha.innerText()).includes('۰٪'));
  assert.ok((await stale.innerText()).includes('داده قدیمی'));
  assert.ok(!(await stale.innerText()).includes('تأییدشده در آخرین heartbeat'));
  const consent=page.locator('#relay-candidates .agent-card').filter({hasText:'Relay Consent'});
  assert.equal(await consent.locator('.relay-check').isDisabled(),true);
  assert.equal(await consent.getByRole('button',{name:'جزئیات و رفع مشکل'}).count(),0);
  await page.locator('#relay-version-check').click();
  await alpha.getByText(/مطابق نسخه مرکزی/).waitFor();
  assert.ok(!(await stale.locator('.agent-evidence').innerText()).includes('مطابق نسخه مرکزی'));
  assert.equal(versions,1);
  const id=Number(await alpha.getAttribute('data-agent-id'));
  // Dedicated priority dialog synchronizes numeric input and slider.
  await alpha.getByRole('button',{name:'تنظیم اولویت',exact:true}).click();
  let dialog=page.getByRole('dialog');
  await dialog.locator('#relay-priority-range').evaluate(input=>{input.value='17';input.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await dialog.locator('#relay-priority-number').inputValue(),'17');
  await dialog.locator('#relay-priority-number').fill('1001');
  await dialog.getByRole('button',{name:'ذخیره',exact:true}).click();
  assert.equal(await dialog.locator('#relay-priority-number').evaluate(input=>input.validity.rangeOverflow),true);
  await dialog.locator('#relay-priority-number').fill('0');
  assert.equal(await dialog.locator('#relay-priority-range').inputValue(),'0');
  await dialog.getByRole('button',{name:'ذخیره',exact:true}).focus();await page.keyboard.press('Enter');
  await dialog.waitFor({state:'detached'});await alpha.waitFor();
  let data=await(await context.request.get(base+'/api/fleet/relays')).json();
  assert.equal(data.nodes.find(item=>item.id===id).route_priority,0);
  // Drag/drop changes a draft; cancellation leaves the backend intact.
  await page.locator('#relay-reorder').click();dialog=page.getByRole('dialog');
  const rows=dialog.locator('.relay-sort-row');
  const original=await rows.evaluateAll(rows=>rows.map(row=>Number(row.dataset.serverId)));
  await rows.first().dragTo(rows.last());
  assert.notDeepEqual(await rows.evaluateAll(rows=>rows.map(row=>Number(row.dataset.serverId))),original);
  assert.equal(await dialog.locator('.relay-pinned').getAttribute('draggable'),null);
  await dialog.getByRole('button',{name:'انصراف',exact:true}).click();
  assert.deepEqual((await(await context.request.get(base+'/api/fleet/relays')).json()).nodes.map(x=>[x.id,x.route_priority]),data.nodes.map(x=>[x.id,x.route_priority]));
  // A failed save remains explicitly unsaved and leaves the main list unchanged.
  await page.locator('#relay-reorder').click();dialog=page.getByRole('dialog');
  await dialog.locator('.relay-sort-row').first().getByRole('button',{name:/پایین:/}).click();
  await context.route('**/api/fleet/relays/order',route=>route.fulfill({status:503,json:{detail:'Synthetic unavailable'}}));
  await dialog.getByRole('button',{name:'ذخیره',exact:true}).click();
  await dialog.getByRole('status').filter({hasText:'ذخیره نشد'}).waitFor();
  await dialog.getByRole('button',{name:'انصراف',exact:true}).click();
  await context.unroute('**/api/fleet/relays/order');
  assert.deepEqual((await(await context.request.get(base+'/api/fleet/relays')).json()).nodes.map(x=>[x.id,x.route_priority]),data.nodes.map(x=>[x.id,x.route_priority]));
  // Keyboard-accessible move buttons and one atomic request persist after reload.
  await page.locator('#relay-reorder').click();dialog=page.getByRole('dialog');
  const down=dialog.locator('.relay-sort-row').first().getByRole('button',{name:/پایین:/});await down.focus();await page.keyboard.press('Enter');
  const expected=await dialog.locator('.relay-sort-row').evaluateAll(rows=>rows.map(row=>Number(row.dataset.serverId)));
  let saves=0;
  await context.route('**/api/fleet/relays/order',async route=>{saves++;await new Promise(resolve=>setTimeout(resolve,250));await route.continue();});
  await dialog.getByRole('button',{name:'ذخیره',exact:true}).click();
  await dialog.locator('form').evaluate(form=>form.requestSubmit());
  await dialog.waitFor({state:'detached'});assert.equal(saves,1);
  data=await(await context.request.get(base+'/api/fleet/relays')).json();
  assert.deepEqual(data.nodes.filter(x=>x.enabled&&x.approved).map(x=>x.id),expected);
  assert.ok(!data.fallback_order.some(x=>x.name==='Relay Stale'));
  await page.reload();await alpha.waitFor();
  assert.deepEqual((await(await context.request.get(base+'/api/fleet/relays')).json()).nodes.filter(x=>x.enabled&&x.approved).map(x=>x.id),expected);
  assert.ok((await page.locator('.route-primary').innerText()).includes('اولویت اول'));
  for(const theme of ['dark','light'])for(const width of [320,390,820,1440]){
   await page.setViewportSize({width,height:1000});await page.evaluate(theme=>SonarTheme.set(theme),theme);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
   assert.ok(await page.locator('.route-primary>div:nth-child(2)').evaluate(el=>el.getBoundingClientRect().width>=110));
   assert.ok(await alpha.evaluate(card=>{
    const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
    const luminance=color=>{ctx.fillStyle='#fff';ctx.fillRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);const rgb=[...ctx.getImageData(0,0,1,1).data].slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];};
    const ink=luminance(getComputedStyle(card.querySelector('.relay-candidate-head strong')).color),bg=luminance(getComputedStyle(card).backgroundColor);
    return (Math.max(ink,bg)+.05)/(Math.min(ink,bg)+.05)>=4.5;
   }),`Agent name contrast: ${theme} ${width}`);
   await alpha.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/agent-card-${theme}-${width}.png`});
   if(width===320||width===1440)await page.locator('.route-primary').screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/central-route-${theme}-${width}.png`});
   await page.locator('#relay-reorder').click();dialog=page.getByRole('dialog');
   assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   await dialog.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/relay-order-${theme}-${width}.png`});
   await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
   await alpha.getByRole('button',{name:'تنظیم اولویت',exact:true}).click();dialog=page.getByRole('dialog');
   assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   await dialog.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/relay-priority-${theme}-${width}.png`});
   await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
  }
  await page.evaluate(()=>tabClick('fleet'));
  await page.locator('#fleet-list .agent-card').filter({hasText:'Relay Stale'}).getByText('داده قدیمی',{exact:true}).first().waitFor();
  await context.setOffline(true);
  await page.locator('#fleet-list .agent-card').first().getByText('مرورگر آفلاین',{exact:true}).first().waitFor();
  await context.setOffline(false);
  await page.clock.fastForward(301000);
  assert.ok(!(await page.locator('#fleet-list .agent-card').filter({hasText:'Relay Alpha'}).innerText()).includes('موفق در پنج دقیقه اخیر'));
  assert.deepEqual(errors,[]);
  console.log('PASS: evidence freshness/TLS distinctions, zero CPU/priority, shared consent, batched identity, slider, keyboard/drag order, cancellation, failed and duplicate saves, persistence, pinned central route and responsive themes.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
