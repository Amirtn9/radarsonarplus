'use strict';
(()=>{
 const fields={health:'config-health',config_type:'config-type',protocol:'config-protocol',sort:'config-sort',tag:'config-tag'};
 function sync(){document.getElementById('config-filters').hidden=state.tab!=='configs';for(const [key,id]of Object.entries(fields)){const input=document.getElementById(id);if(document.activeElement!==input)input.value=state.configFilters[key];}}
 function apply(){for(const [key,id]of Object.entries(fields))state.configFilters[key]=document.getElementById(id).value;state.index=1;if(state.tab==='configs')loadList();}
 for(const id of Object.values(fields))document.getElementById(id).addEventListener('change',apply);
 document.getElementById('config-filters').onsubmit=event=>{event.preventDefault();apply();};
 function summary(raw){const target=document.getElementById('config-summary');if(!raw){target.textContent='';return;}try{const data=JSON.parse(raw);target.textContent=`در نتایج همین فیلتر: ${data.total} مورد · سالم ${data.healthy} · ناموفق ${data.failed} · سایر/نامشخص ${data.unknown} (خطای محیط ${data.environment_error}، تست‌نشده ${data.untested})`;}catch{target.textContent='خلاصه در دسترس نیست.';}}
 window.SonarConfigFilters={sync,summary};
})();
