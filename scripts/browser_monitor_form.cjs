'use strict';
// Disposable local HTTPS fixture only; no external monitoring requests.
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
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:900}});
  await context.route('https://telegram.org/**',route=>route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/?view=services#'+launch());
  await page.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل'&&document.getElementById('service-form').dataset.uxDenied==='false');
  const form=page.locator('#service-form');
  for(const theme of ['dark','light'])for(const width of [320,360,390,430,820,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(theme=>SonarTheme.set(theme),theme);
   await form.scrollIntoViewIfNeeded();
   await form.screenshot({path:process.env.SONAR_BROWSER_TEST_OUTPUT+`/monitor-form-${theme}-${width}.png`});
   const issues=await form.evaluate(form=>{
    const issues=[],outer=form.getBoundingClientRect(),controls=[...form.querySelectorAll('input,select,button:not([hidden])')];
    for(const input of controls){
     const box=input.getBoundingClientRect();
     if(box.width<120||box.height<40||box.height>80)issues.push(input.id+': unusable size '+box.width+'x'+box.height);
     if(box.left<outer.left-1||box.right>outer.right+1)issues.push(input.id+': outside form');
     if(input.matches('input,select')){
      const label=input.closest('label');
      if(!label||label.control!==input)issues.push(input.id+': missing associated label');
      const range=document.createRange();range.selectNodeContents(label.firstChild);
      const title=range.getBoundingClientRect();
      if(title.bottom>box.top+1)issues.push(input.id+': title overlaps input');
     }
    }
    for(let i=0;i<controls.length;i++)for(let j=i+1;j<controls.length;j++){
     const a=controls[i].getBoundingClientRect(),b=controls[j].getBoundingClientRect();
     if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)issues.push(controls[i].id+' overlaps '+controls[j].id);
    }
    if(document.documentElement.scrollWidth>document.documentElement.clientWidth+1)issues.push('horizontal page overflow');
    if(getComputedStyle(form).overflowX==='hidden')issues.push('form clips overflow');
    return issues;
   });
   assert.deepEqual(issues,[],`${theme} ${width}`);
  }
  const target='https://example.com/health?region='+('long-public-service-name-'.repeat(16));
  await page.setViewportSize({width:360,height:900});
  await page.getByLabel('نام سرویس',{exact:true}).fill('مانیتور فرم موبایل');
  await page.locator('#service-target').fill(target);
  for(const [id,value]of Object.entries({'service-interval':'120','service-timeout':'8','service-failures':'3','service-recoveries':'2','service-expected':'204'}))await page.locator('#'+id).fill(value);
  // Native HTML validation must prevent the request before a valid submission.
  let posts=0;page.on('request',req=>{if(req.method()==='POST'&&new URL(req.url()).pathname==='/api/services')posts++;});
  await page.locator('#service-interval').fill('10');
  await page.locator('#service-save').focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#service-interval').evaluate(input=>input.validity.rangeUnderflow),true);
  assert.equal(posts,0);
  await page.locator('#service-interval').fill('120');
  const response=page.waitForResponse(res=>res.request().method()==='POST'&&new URL(res.url()).pathname==='/api/services');
  await page.locator('#service-save').focus();await page.keyboard.press('Enter');
  const saved=await response;assert.equal(saved.status(),201);
  assert.deepEqual(saved.request().postDataJSON(),{name:'مانیتور فرم موبایل',kind:'http',target,interval:120,timeout:8,failure_threshold:3,recovery_threshold:2,expected_status:204});
  const rows=await(await context.request.get(base+'/api/services')).json();
  const created=rows.find(row=>row.target===target);assert.ok(created);
  assert.equal(created.interval_seconds,120);assert.equal(created.timeout_seconds,8);
  assert.equal(created.failure_threshold,3);assert.equal(created.recovery_threshold,2);assert.equal(created.expected_status,204);
  await page.getByText('مانیتور ایجاد شد',{exact:true}).waitFor();
  await page.locator('#service-kind').selectOption('tcp');
  assert.equal(await page.locator('#service-target').getAttribute('placeholder'),'example.com:443');
  assert.deepEqual(errors,[]);
  console.log('PASS: monitor form geometry in 12 viewport/theme combinations, associated labels, long URL, numeric validation, keyboard submission and persisted field mapping.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
