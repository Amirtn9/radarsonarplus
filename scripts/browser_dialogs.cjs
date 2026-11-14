// Respond through real custom dialog controls, never replace application functions.
module.exports=async function install(page,answer){
 function setup(answer){
  const observe=()=>{const observer=new MutationObserver(()=>{const d=document.querySelector('.sonar-dialog[open]');if(!d||d.dataset.testHandled)return;d.dataset.testHandled='1';setTimeout(()=>{const input=d.querySelector('textarea');if(input){input.value=answer;input.dispatchEvent(new Event('input',{bubbles:true}));}d.querySelector('button[type=submit]')?.click();},30);});observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});};
  if(document.body)observe();else document.addEventListener('DOMContentLoaded',observe,{once:true});
 }
 await page.addInitScript(setup,answer);await page.evaluate(setup,answer);
};
