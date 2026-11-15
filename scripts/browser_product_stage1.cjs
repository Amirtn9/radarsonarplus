const {chromium}=require('playwright');
const crypto=require('node:crypto'),assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL,token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local fixture required');
function launch(){
 const data={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:901})};
 const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();
 data.hash=crypto.createHmac('sha256',secret).update(Object.keys(data).sort().map(k=>k+'='+data[k]).join('\n')).digest('hex');
 return 'tgWebAppData='+encodeURIComponent(new URLSearchParams(data).toString());
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:900}});
  await context.route('https://telegram.org/**',route=>route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const now=new Date().toISOString(),old=new Date(Date.now()-600000).toISOString();
  const rows=[
   {id:1001,name:'Online stage server',ip:'192.0.2.1',port:2222,is_active:1,last_status:'Online',last_agent_status:'Online',last_cpu:0,last_ram:34.5,last_disk:90.1,last_checked_at:now},
   {id:1002,name:'Offline stage server',ip:'192.0.2.2',port:22,is_active:1,last_status:'Offline',last_cpu:null,last_ram:null,last_disk:null,last_checked_at:now},
   {id:1003,name:'Stale stage server',ip:'192.0.2.3',port:22,is_active:1,last_status:'Online',last_cpu:45,last_ram:100,last_disk:0,last_checked_at:old}
  ];
  await context.route('**/api/resources/server?*',route=>route.fulfill({json:rows,headers:{'X-Total-Count':'3'}}));
  await page.goto(base+'/#'+launch());
  await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  await page.locator('#navigation .nav-section').first().waitFor();
  for(const target of ['servers','configs','services','incidents']){
   await page.evaluate(()=>tabClick('overview'));
   const metric=page.locator(`.metric-link[data-go="${target}"]`);await metric.focus();await metric.press('Enter');
   await page.waitForFunction(target=>state.tab===target,target);
  }
  await page.evaluate(()=>tabClick('overview'));
  const group=page.locator('#navigation [data-group=resources]');
  await group.locator('summary').click();
  await page.waitForFunction(()=>localStorage.getItem('sonar.nav.group.resources')==='closed');
  await page.reload();await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  await page.waitForFunction(()=>document.querySelector('#navigation [data-group=resources]')?.open===false);
  await group.locator('summary').focus();await group.locator('summary').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#navigation [data-group=resources]').open);
  await page.locator('#navigation [data-tab=servers]').click();
  await page.locator('.server-card').filter({hasText:'Online stage server'}).waitFor();
  const online=page.locator('.server-card').filter({hasText:'Online stage server'});
  const offline=page.locator('.server-card').filter({hasText:'Offline stage server'});
  const stale=page.locator('.server-card').filter({hasText:'Stale stage server'});
  assert.equal(await online.locator('.server-state').innerText(),'آنلاین');
  assert.equal(await online.getByRole('meter',{name:'CPU',exact:true}).getAttribute('value'),'0');
  assert.ok((await online.innerText()).includes('SSH: بررسی‌نشده'));
  assert.ok((await online.innerText()).includes('2222'));
  assert.equal(await offline.locator('.server-state').innerText(),'آفلاین');
  assert.equal(await offline.locator('meter').count(),0);
  assert.equal(await stale.locator('.server-state').innerText(),'نیازمند بررسی');
  assert.equal(await stale.locator('.server-card-metric[data-fresh=false]').count(),3);
  await context.setOffline(true);await page.waitForFunction(()=>document.querySelector('.server-card .server-state').dataset.state==='unknown');
  await context.setOffline(false);await page.waitForFunction(()=>document.querySelector('.server-card .server-state').dataset.state==='online');
  for(const theme of ['dark','light'])for(const width of [320,390,820,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(theme=>SonarTheme.set(theme),theme);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
   if(width===390||width===1440)await page.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/stage1-${theme}-${width}.png`,fullPage:true});
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: keyboard dashboard links, persistent collapsible navigation, zero/missing/stale server metrics, offline expiry and responsive cards.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
