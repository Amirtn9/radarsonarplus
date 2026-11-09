'use strict';
// Synthetic local HTTPS fixture only; API overrides below are browser test data.
const assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs');
const {chromium}=require('playwright');
const base=process.env.SONAR_BROWSER_TEST_URL||'https://127.0.0.1:8443',token=process.env.SONAR_BROWSER_TEST_TOKEN;
if(!token||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local synthetic fixture and token required');
const output=process.env.SONAR_BROWSER_TEST_OUTPUT||'/tmp/sonar-telemetry-artifacts';fs.mkdirSync(output,{recursive:true});
function launch(id){const data={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id})};const secret=crypto.createHmac('sha256','WebAppData').update(token).digest();data.hash=crypto.createHmac('sha256',secret).update(Object.keys(data).sort().map(k=>k+'='+data[k]).join('\n')).digest('hex');return new URLSearchParams({tgWebAppData:new URLSearchParams(data).toString()});}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000},deviceScaleFactor:2,timezoneId:'Asia/Tehran'});
  await context.route('https://telegram.org/**',r=>r.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.chartLabels=[];const draw=CanvasRenderingContext2D.prototype.fillText;CanvasRenderingContext2D.prototype.fillText=function(text,x,y,...args){window.chartLabels.push({id:this.canvas.id,text:String(text),x,y});return draw.call(this,text,x,y,...args);};});
  await page.goto(base+'/#'+launch(901));
  await page.waitForFunction(()=>Number(document.getElementById('resource-chart').dataset.sampleCount)>0);
  await page.locator('#menu-trigger').click();await page.locator('[data-quick-tab=servers]').click();
  await page.getByRole('button',{name:'جزئیات',exact:true}).first().click();
  await page.waitForFunction(()=>Number(document.getElementById('detail-chart').dataset.sampleCount)>0);
  await page.waitForFunction(()=>document.getElementById('detail-live').textContent.includes('LIVE'));
  // Losing the next API response must not leave a permanent LIVE badge.
  await page.evaluate(()=>{window.savedTestNow=Date.now;Date.now=()=>window.savedTestNow()+181000;document.dispatchEvent(new Event('visibilitychange'));});
  assert.ok(!(await page.locator('#detail-live').innerText()).includes('LIVE'));
  await page.evaluate(()=>{Date.now=window.savedTestNow;delete window.savedTestNow;document.dispatchEvent(new Event('visibilitychange'));});
  const sid=await page.evaluate(()=>window.SonarConsole.currentServerId());
  const initial=await page.locator('[data-dial=ram]').getAttribute('aria-valuenow');assert.equal(Number(initial),50);
  // Keyboard-readable exact time/value tooltip, plus a selectable series legend.
  await page.locator('#detail-chart').focus();await page.keyboard.press('ArrowLeft');
  assert.ok((await page.locator('#detail-chart + .chart-legend + output').innerText()).includes('CPU'));
  const cpu=page.locator('#detail-chart + .chart-legend button').first();await cpu.click();assert.equal(await cpu.getAttribute('aria-pressed'),'false');await cpu.click();
  for(const hours of ['1','3','12','24','48']){
   const response=page.waitForResponse(r=>r.url().includes(`/api/server-resources/${sid}?hours=${hours}`)&&r.status()===200);
   await page.locator('#detail-hours').selectOption(hours);await response;
  }
  for(const width of [320,390,768,1440])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:1000});await page.evaluate(t=>window.SonarTheme.set(t),theme);
   await page.waitForTimeout(100);
   const state=await page.evaluate(()=>{
    const cell=document.querySelector('.console-detail-cell strong'),canvas=document.getElementById('detail-chart');
    const color=getComputedStyle(cell).color.match(/[\d.]+/g).slice(0,3).map(Number),bg=getComputedStyle(cell.parentElement).backgroundColor.match(/[\d.]+/g).slice(0,3).map(Number);
    const luminance=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
    const a=luminance(color),b=luminance(bg);
    return {scroll:document.documentElement.scrollWidth,width:document.documentElement.clientWidth,contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),pixels:canvas.width,css:canvas.clientWidth,labels:window.chartLabels.filter(x=>x.id==='detail-chart').slice(-30)};
   });
   assert.ok(state.scroll<=state.width+1,JSON.stringify(state));assert.ok(state.contrast>=4.5,JSON.stringify(state));assert.ok(state.pixels>=state.css*1.9);
   assert.ok(state.labels.some(p=>p.text.includes(':')),'missing clock axis');assert.ok(state.labels.filter(p=>p.text.endsWith('%')).every(p=>p.x>0),'clipped percent labels');
   if(width===390||width===1440)await page.screenshot({path:`${output}/server-${theme}-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('#web-agent-install').locator('..').locator('.field-help summary').click();
  const help=await page.locator('#web-agent-install').locator('..').locator('.field-help p').boundingBox();assert.ok(help.x>=0&&help.x+help.width<=391);
  await page.keyboard.press('Escape');
  // The normal poll must apply new samples, rather than merely animate old data.
  let freshness='fresh',calls=0;
  await page.route('**/api/console/servers/'+sid,async route=>{const r=await route.fetch();const data=await r.json();data.last_cpu=77.7;data.last_ram=0;data.last_checked_at=new Date(Date.now()-(freshness==='stale'?720000:0)).toISOString();data.last_status=freshness==='offline'?'Offline':'Online';calls++;await route.fulfill({response:r,json:data});});
  await page.waitForFunction(()=>document.querySelector('[data-dial=cpu]').getAttribute('aria-valuenow')==='77.7',null,{timeout:22000});
  assert.equal(await page.locator('[data-dial=ram]').getAttribute('aria-valuenow'),'0');assert.ok(calls>0);
  freshness='stale';await page.evaluate(()=>window.SonarNext.loadSensors());assert.ok(!(await page.locator('#sensor-freshness').innerText()).includes('LIVE'));
  freshness='offline';await page.evaluate(()=>window.SonarNext.loadSensors());assert.ok(!(await page.locator('#sensor-freshness').innerText()).includes('LIVE'));
  await page.unroute('**/api/console/servers/'+sid);
  await page.route('**/api/metrics/**',route=>route.fulfill({json:[{ok:true,ping:80,measured_at:new Date(Date.now()-120000).toISOString()},{ok:false,ping:null,measured_at:new Date(Date.now()-60000).toISOString()},{ok:true,ping:90,measured_at:new Date().toISOString()}]}));
  await page.evaluate(()=>tabClick('rankings'));await page.getByRole('button',{name:'📈 نمودار',exact:true}).first().click();await page.locator('#trend').waitFor();
  assert.equal(await page.locator('#trend-canvas').getAttribute('data-sample-count'),'3');await page.evaluate(()=>window.SonarTheme.set('light'));
  await page.screenshot({path:output+'/config-history-light.png'});
  await page.locator('#trend-close').click();
  await page.locator('#menu-trigger').click();assert.ok(await page.locator('[data-quick-tab=support]').isVisible());await page.keyboard.press('Escape');
  assert.equal(await page.locator('#menu-trigger').getAttribute('aria-expanded'),'false');
  assert.deepEqual(errors,[]);
  const user=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});await user.route('https://telegram.org/**',r=>r.abort());
  const ordinary=await user.newPage();await ordinary.goto(base+'/#'+launch(902));await ordinary.waitForFunction(()=>document.getElementById('topbar-status').textContent==='متصل');await ordinary.locator('#menu-trigger').click();
  assert.equal(await ordinary.locator('[data-quick-tab=support]').count(),0);assert.equal(await ordinary.locator('[data-quick-tab=balancer]').count(),0);
  await user.close();await context.close();console.log('PASS: live updates, stale/offline states, zero/decimal gauges, times, HiDPI, theme contrast, mobile help and role-aware mega menu');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
