'use strict';
// One renderer for server observations and configuration latency. All points
// come from authenticated APIs; animation never manufactures measurements.
(() => {
 const charts=new Map(),liveHosts=new Map();
 const percent=[{key:'cpu',label:'CPU',color:'#1685f8'},{key:'ram',label:'RAM',color:'#9a78ed'},{key:'disk',label:'Disk',color:'#16ac7b'}];
 function time(value){if(!value)return NaN;if(typeof value==='number')return Number.isFinite(value)?value:NaN;const raw=typeof value==='string'?value.trim().replace(' ','T'):value;return Date.parse(typeof raw==='string'&&!/[zZ]|[+-]\d\d:\d\d$/.test(raw)?raw+'Z':raw);}
 function number(value){if(value==null||value==='')return null;const n=Number(value);return Number.isFinite(n)?n:null;}
 const format=value=>Number(value).toLocaleString('fa-IR',{maximumFractionDigits:1});
 function redraw(chart){
  const {canvas,options,points}=chart;
  const width=canvas.clientWidth;if(width<40)return;
  const height=canvas.clientHeight||280,dpr=Math.min(window.devicePixelRatio||1,3);
  canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
  const ctx=canvas.getContext('2d');if(!ctx)return;ctx.scale(dpr,dpr);
  const css=getComputedStyle(document.documentElement),light=document.documentElement.dataset.theme==='light';
  const ink=css.getPropertyValue('--sub').trim()||(light?'#526070':'#b0bac5');
  const bg=css.getPropertyValue('--surface2').trim()||(light?'#f1f3f5':'#272a2e');
  const grid=css.getPropertyValue('--outline').trim()||(light?'#dce0e5':'#35383d');
  ctx.fillStyle=bg;ctx.fillRect(0,0,width,height);
  ctx.font='12px Vazirmatn, Tahoma, sans-serif';ctx.direction='ltr';
  const maximum=options.unit==='ms'?Math.max(100,...points.map(p=>number(p.ping)||0))*1.15:100;
  const unit=options.unit==='ms'?' ms':'%';
  // Measure labels in CSS pixels, so they cannot be clipped by an RTL canvas
  // or by scaling a desktop bitmap into a narrow Telegram viewport.
  const left=Math.ceil(ctx.measureText(format(maximum)+unit).width)+16,right=width-16,top=20,bottom=height-48;
  const end=options.hours?Date.now():Math.max(...points.map(p=>p.t),Date.now()-1000);
  const first=points[0]?.t;
  const start=options.hours?end-options.hours*3600000:(Number.isFinite(first)?Math.min(first,end-60000):end-3600000);
  chart.geometry={left,right,top,bottom,start,end,maximum,width,height};
  for(let i=0;i<=4;i++){
   const y=top+(bottom-top)*i/4;ctx.beginPath();ctx.strokeStyle=grid;ctx.lineWidth=1;ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
   ctx.fillStyle=ink;ctx.textAlign='right';ctx.fillText(format(maximum*(1-i/4))+unit,left-9,y+4);
  }
  const ticks=width<500?3:5;
  for(let i=0;i<ticks;i++){
   const t=start+(end-start)*i/(ticks-1),x=left+(right-left)*i/(ticks-1),d=new Date(t);
   ctx.fillStyle=ink;ctx.textAlign=i===0?'left':i===ticks-1?'right':'center';
   ctx.fillText(d.toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'}),x,bottom+22);
   if(end-start>=24*3600000)ctx.fillText(d.toLocaleDateString('fa-IR',{month:'numeric',day:'numeric'}),x,bottom+39);
  }
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  for(const series of options.series||percent){
   if(chart.disabled.has(series.key))continue;
   const segments=[];let segment=[];let previous=null;
   const gap=options.hours?Math.max(300000,options.hours*15000):Infinity;
   for(const p of points){
    const v=number(p[series.key]);
    if(v===null||v<0||(options.unit!=='ms'&&v>100)||previous!==null&&p.t-previous>gap){if(segment.length)segments.push(segment);segment=[];}
    if(v!==null&&v>=0&&(options.unit==='ms'||v<=100)&&p.t>=start&&p.t<=end)segment.push({x:left+(p.t-start)/(end-start)*(right-left),y:bottom-v/maximum*(bottom-top)});
    previous=p.t;
   }
   if(segment.length)segments.push(segment);
   const gradient=ctx.createLinearGradient(0,top,0,bottom);gradient.addColorStop(0,series.color+'35');gradient.addColorStop(1,series.color+'00');
   for(const part of segments){
    ctx.beginPath();ctx.moveTo(part[0].x,bottom);part.forEach(p=>ctx.lineTo(p.x,p.y));ctx.lineTo(part.at(-1).x,bottom);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
    ctx.beginPath();part.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.strokeStyle=series.color;ctx.lineWidth=2.3;ctx.lineJoin='round';ctx.lineCap='round';ctx.stroke();
    if(part.length===1){ctx.beginPath();ctx.arc(part[0].x,part[0].y,3,0,2*Math.PI);ctx.fillStyle=series.color;ctx.fill();}
   }
   const last=segments.at(-1)?.at(-1);if(last){ctx.beginPath();ctx.arc(last.x,last.y,3.5,0,Math.PI*2);ctx.fillStyle=series.color;ctx.fill();}
  }
  ctx.restore();
  if(!points.length){ctx.fillStyle=ink;ctx.textAlign='center';ctx.fillText('هنوز نمونه‌ای ثبت نشده است',width/2,height/2);}
 }
 function render(canvas,values,options={}){
  if(!canvas)return;
  let chart=charts.get(canvas);
  if(!chart){
   const wrapper=document.createElement('div');wrapper.className='telemetry-chart';canvas.before(wrapper);wrapper.append(canvas);
   const legend=document.createElement('div');legend.className='chart-legend';wrapper.append(legend);
   const tooltip=document.createElement('output');tooltip.className='chart-tooltip';tooltip.hidden=true;wrapper.append(tooltip);
   chart={canvas,legend,tooltip,disabled:new Set(),options,points:[]};charts.set(canvas,chart);
   canvas.setAttribute('tabindex','0');canvas.setAttribute('aria-description','برای دیدن زمان و مقدار هر نمونه روی نمودار حرکت کنید یا کلیدهای جهت را بزنید.');
   function inspect(index){
    const p=chart.points[index];if(!p)return;chart.cursor=index;
    tooltip.textContent=new Date(p.t).toLocaleString('fa-IR')+' · '+(chart.options.series||percent).map(s=>s.label+': '+(number(p[s.key])===null?'—':format(p[s.key])+(chart.options.unit==='ms'?' ms':'٪'))).join(' · ');
    tooltip.hidden=false;
   }
   canvas.addEventListener('pointermove',event=>{
    const g=chart.geometry;if(!g||!chart.points.length)return;
    const ratio=Math.max(0,Math.min(1,(event.clientX-canvas.getBoundingClientRect().left-g.left)/(g.right-g.left)));
    const target=g.start+ratio*(g.end-g.start);let best=0;
    chart.points.forEach((p,i)=>{if(Math.abs(p.t-target)<Math.abs(chart.points[best].t-target))best=i;});inspect(best);
   });
   canvas.addEventListener('pointerleave',()=>{tooltip.hidden=true;});canvas.addEventListener('blur',()=>{tooltip.hidden=true;});
   canvas.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();inspect(Math.max(0,Math.min(chart.points.length-1,(chart.cursor??chart.points.length-1)+(event.key==='ArrowLeft'?-1:1))));}});
   if(window.ResizeObserver)new ResizeObserver(()=>redraw(chart)).observe(canvas);
  }
  chart.options=options;chart.tooltip.hidden=true;
  chart.points=values.map(p=>({...p,t:time(p.time)})).filter(p=>Number.isFinite(p.t)).sort((a,b)=>a.t-b.t);
  const current=options.latest;
  if(current&&Number.isFinite(time(current.time))&&time(current.time)>(chart.points[chart.points.length-1]?.t||0))chart.points.push({...current,t:time(current.time)});
  chart.legend.replaceChildren();
  for(const s of options.series||percent){const button=document.createElement('button');button.type='button';button.textContent=s.label;button.style.setProperty('--series',s.color);button.setAttribute('aria-pressed',String(!chart.disabled.has(s.key)));button.onclick=()=>{chart.disabled.has(s.key)?chart.disabled.delete(s.key):chart.disabled.add(s.key);button.setAttribute('aria-pressed',String(!chart.disabled.has(s.key)));redraw(chart);};chart.legend.append(button);}
  canvas.dataset.sampleCount=String(chart.points.length);redraw(chart);
 }
 function live(host,server,sampleTime){
  if(!host)return;
  liveHosts.set(host,{server,sampleTime});
  const stamp=time(sampleTime||server?.last_checked_at),age=Date.now()-stamp;
  const checkedAge=Date.now()-time(server?.last_checked_at);
  const hasMetrics=!server||!Object.prototype.hasOwnProperty.call(server,'last_cpu')||['last_cpu','last_ram','last_disk'].some(key=>number(server[key])!==null);
  const online=navigator.onLine!==false&&server?.last_status==='Online'&&hasMetrics&&Number.isFinite(age)&&age>=-30000&&age<=180000&&checkedAge>=-30000&&checkedAge<=180000;
  host.classList.toggle('is-live',online);host.classList.toggle('stale',!online);
  host.textContent=online?'LIVE · '+new Date(stamp).toLocaleTimeString('fa-IR'):'○ '+(Number.isFinite(stamp)?'آخرین نمونه: '+new Date(stamp).toLocaleString('fa-IR'):'در انتظار نمونه');
  host.title=online?'سرور آنلاین است؛ داده‌های واقعی هر ۱۵ ثانیه بررسی می‌شوند.':'داده زنده در دسترس نیست؛ آخرین نمونه ذخیره‌شده نمایش داده می‌شود.';
 }
 new MutationObserver(()=>charts.forEach(redraw)).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
 // Expire LIVE even if authentication or the network stops further polling.
 function expire(){for(const [host,data] of liveHosts){if(!host.isConnected){liveHosts.delete(host);continue;}live(host,data.server,data.sampleTime);}}
 window.setInterval?.(expire,10000);document.addEventListener('visibilitychange',expire);window.addEventListener('offline',expire);window.addEventListener('online',expire);
 window.SonarTelemetry={render,live,time};
})();
