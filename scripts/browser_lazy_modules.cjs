// Cold navigation, script single-flight, failure recovery and stale-page protection.
const {chromium}=require('playwright'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const base=process.env.SONAR_BROWSER_TEST_URL,token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local fixture required');
function launch(){const d={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:901})},secret=crypto.createHmac('sha256','WebAppData').update(token).digest();d.hash=crypto.createHmac('sha256',secret).update(Object.keys(d).sort().map(k=>k+'='+d[k]).join('\n')).digest('hex');return 'tgWebAppData='+encodeURIComponent(new URLSearchParams(d).toString());}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});
  await context.route('https://telegram.org/**',route=>route.abort());
  const page=await context.newPage(),requests=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>requests.push(new URL(request.url()).pathname));
  await page.goto(base+'/#'+launch());await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  const files=['billing','product','reports','log-management'];
  assert.deepEqual(requests.filter(path=>files.some(name=>path==='/static/'+name+'.js')),[]);
  assert.equal(requests.filter(path=>path.includes('/vendor/xterm')).length,0);
  let fail=true;
  await page.route('**/static/reports.js',route=>fail?route.fulfill({status:503,body:'temporary failure'}):route.continue());
  await page.evaluate(()=>tabClick('reports'));
  await page.locator('#reports .module-loader-status').getByRole('button',{name:'تلاش دوباره',exact:true}).waitFor();
  assert.equal(await page.locator('#reports').getAttribute('aria-busy'),null);
  fail=false;
  await page.locator('#reports .module-loader-status').getByRole('button',{name:'تلاش دوباره',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('reports-status').textContent.includes('دریافت شد'));
  assert.equal(await page.locator('#reports .module-loader-status').count(),0);
  await page.evaluate(()=>Promise.all(Array.from({length:8},()=>window.SonarModules.ensure('commerce'))));
  for(const name of ['billing','product'])assert.equal(requests.filter(path=>path==='/static/'+name+'.js').length,1);
  await page.evaluate(()=>tabClick('billing'));await page.getByRole('heading',{name:'اشتراک خدمات پایش',exact:true}).waitFor();
  await page.evaluate(()=>tabClick('reports'));await page.waitForFunction(()=>state.tab==='reports');
  assert.equal(requests.filter(path=>path==='/static/reports.js').length,2);
  await page.evaluate(()=>tabClick('support'));await page.waitForFunction(()=>typeof window.SonarLogs?.load==='function'&&document.querySelector('#managed-log-list article,#managed-log-list p'));
  assert.equal(requests.filter(path=>path==='/static/log-management.js').length,1);
  const fresh=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});
  await fresh.route('https://telegram.org/**',route=>route.abort());
  const other=await fresh.newPage();other.on('pageerror',error=>errors.push(error.message));
  let release,requested;const gate=new Promise(resolve=>release=resolve),started=new Promise(resolve=>requested=resolve);
  await other.route('**/static/product.js',async route=>{requested();await gate;await route.continue();});
  await other.goto(base+'/#'+launch());await other.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');
  await other.evaluate(()=>tabClick('users'));await started;
  await other.evaluate(()=>tabClick('settings'));const title=await other.locator('#page-title').innerText();
  release();await other.evaluate(()=>window.SonarModules.ensure('commerce'));
  assert.equal(await other.evaluate(()=>state.tab),'settings');assert.equal(await other.locator('#page-title').innerText(),title);
  assert.deepEqual(errors,[]);
  console.log('PASS: four feature scripts absent at startup; shared dependencies load once; failed module retries; cold navigation cannot overwrite a newer page.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
