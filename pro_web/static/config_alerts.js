'use strict';
(() => {
 const byId=id=>document.getElementById(id);
 const form=byId('config-alert-form');if(!form)return;
 const bools=['enabled','notify_down','notify_recovery','notify_score','scheduled','report_enabled'];
 const numbers=['failure_threshold','score_delta','cooldown_minutes','probe_interval_minutes','report_interval_minutes'];
 let loaded=false;
 async function load(){
  try{
   const prefs=await window.SonarWebRequest('/api/config-alerts');
   bools.forEach(key=>{byId('ca-'+key).checked=!!prefs[key];});
   numbers.forEach(key=>{byId('ca-'+key).value=prefs[key];});
   ['quiet_start','quiet_end'].forEach(key=>{byId('ca-'+key).value=prefs[key]??'';});
   byId('ca-timezone').value=prefs.timezone;
   byId('ca-muted').value=(prefs.muted_config_ids||[]).join(', ');
   loaded=true;byId('ca-save').disabled=false;byId('ca-feedback').textContent='تنظیمات فعلی دریافت شد؛ تغییرات فقط با ذخیره اعمال می‌شوند.';
  }catch(error){byId('ca-feedback').textContent=error.message;}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(!loaded)return;
  const data={};bools.forEach(key=>{data[key]=byId('ca-'+key).checked;});
  numbers.forEach(key=>{data[key]=Number(byId('ca-'+key).value);});
  ['quiet_start','quiet_end'].forEach(key=>{const raw=byId('ca-'+key).value;data[key]=raw===''?null:Number(raw);});
  if((data.quiet_start===null)!==(data.quiet_end===null)){byId('ca-feedback').textContent='شروع و پایان ساعت سکوت را با هم وارد کنید یا هر دو را خالی بگذارید.';return;}
  data.timezone=byId('ca-timezone').value.trim();
  const ids=byId('ca-muted').value.trim();data.muted_config_ids=ids?ids.split(/[,،\s]+/).filter(Boolean).map(Number):[];
  if(data.muted_config_ids.some(id=>!Number.isSafeInteger(id)||id<1)){byId('ca-feedback').textContent='شناسه‌های مثبت کانفیگ را با کاما جدا کنید.';return;}
  byId('ca-save').disabled=true;
  try{await window.SonarWebPost('/api/config-alerts',data);byId('ca-feedback').textContent='✓ تنظیمات اعلان و زمان‌بندی با موفقیت ذخیره شد.';}
  catch(error){byId('ca-feedback').textContent=error.message;}
  finally{byId('ca-save').disabled=false;}
 });
 window.SonarConfigAlerts={load};
 if(typeof state!=='undefined'&&state.tab==='settings'&&panelAuthenticated)void load();
})();
