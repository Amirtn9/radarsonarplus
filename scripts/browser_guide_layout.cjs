'use strict';
// Layout-only fixture: public training content and local static files; no database or Telegram access.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const web=path.join(root,'pro_web');
const data=JSON.parse(execFileSync(process.env.SONAR_TEST_PYTHON||'python3',['-c','import json;from guide_content import content;print(json.dumps(content(False)))'],{cwd:root,encoding:'utf8'}));
data.chapters[0].lessons[0].action+=' '+('long-unbroken-example-'.repeat(35));
const server=http.createServer((request,response)=>{
 const pathname=new URL(request.url,'http://localhost').pathname;
 if(pathname.startsWith('/api/guide')){response.setHeader('Content-Type','application/json');response.end(JSON.stringify(data));return;}
 const target=pathname==='/guide'?path.join(web,'guide.html'):path.resolve(web,'.'+pathname);
 if(!target.startsWith(web+path.sep)||!fs.existsSync(target)||!fs.statSync(target).isFile()){response.writeHead(404);response.end();return;}
 const types={'.css':'text/css','.js':'text/javascript','.html':'text/html','.woff2':'font/woff2'};
 response.setHeader('Content-Type',types[path.extname(target)]||'application/octet-stream');
 const override=process.env.SONAR_GUIDE_CSS_OVERRIDE;
 response.end(fs.readFileSync(override&&pathname==='/static/guide.css'?override:target));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base=`http://127.0.0.1:${server.address().port}`;
 const browser=await chromium.launch({executablePath:process.env.SONAR_CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  for(const width of [320,360,390,600,820,1280]){
   await page.setViewportSize({width,height:720});
   await page.goto(base+'/guide');
   await page.waitForFunction(count=>document.querySelectorAll('#chapter-content section').length===count,data.chapters.length);
   await page.evaluate(()=>document.fonts.ready);
   for(const fontSize of [16,24,32]){
    await page.evaluate(size=>{document.documentElement.style.fontSize=size+'px';document.querySelector('details').open=true;},fontSize);
    const result=await page.evaluate(()=>{
     const width=document.documentElement.clientWidth;
     const selectors=['.layout','aside','main','#guide-search','nav','#guide-status','details[open]','.lesson-body'];
     return {width,scroll:document.documentElement.scrollWidth,clipped:selectors.flatMap(selector=>[...document.querySelectorAll(selector)].filter(el=>{const r=el.getBoundingClientRect();return r.width>width+1||r.left< -1||r.right>width+1;}).map(()=>selector))};
    });
    assert.ok(result.scroll<=result.width+1,`Horizontal overflow at ${width}px / ${fontSize}px: ${JSON.stringify(result)}`);
    assert.deepEqual(result.clipped,[],`Clipped content at ${width}px / ${fontSize}px`);
   }
  }
  await page.setViewportSize({width:390,height:720});
  await page.goto(base+'/guide');await page.waitForSelector('#chapter-content details');
  assert.ok(await page.locator('.hero').evaluate(el=>el.getBoundingClientRect().height)<480,'Mobile header is too tall');
  assert.equal(await page.locator('body').innerText().then(text=>text.includes('فونت وزیر')),false);
  await page.locator('#guide-search').fill('وایرگارد');
  assert.ok(await page.locator('#configs details:not([hidden])').count());
  await page.locator('#guide-search').fill('');
  await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
  const output=process.env.SONAR_GUIDE_SCREENSHOTS||'/tmp/sonar-guide-layout';fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'mobile-top.png')});
  await page.locator('#guide-search').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'mobile-categories.png')});
  assert.deepEqual(errors,[]);
  console.log('PASS: 18 viewport/text-size combinations; no horizontal overflow or clipped lessons; compact mobile header; Persian search; dark mode; no implementation copy or JavaScript errors.');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
