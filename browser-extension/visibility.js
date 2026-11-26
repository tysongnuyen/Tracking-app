'use strict';
// State only: no DOM content, URLs, keys, screenshots, or repeating timers.
let heartbeat;
function reportVisibility(forceHidden=false) {
  const visible=!forceHidden&&document.visibilityState==='visible';
  chrome.runtime.sendMessage({type:'tab-visibility',visible,focused:visible&&document.hasFocus()}).catch(()=>{});
  clearTimeout(heartbeat);
  if(visible)heartbeat=setTimeout(()=>reportVisibility(),60000);
}
document.addEventListener('visibilitychange',()=>reportVisibility());
window.addEventListener('focus',()=>reportVisibility());
window.addEventListener('blur',()=>reportVisibility());
window.addEventListener('pageshow',()=>reportVisibility());
window.addEventListener('pagehide',()=>reportVisibility(true));
reportVisibility();
