'use strict';
const ENDPOINT = 'http://127.0.0.1:43127';
let queue = Promise.resolve();
let scheduled=false,dirty=false,lastSent=0;
function hostname(url) {
  try {
    const u = new URL(url);
    if (!['http:', 'https:'].includes(u.protocol) || ['localhost', '127.0.0.1'].includes(u.hostname)) return null;
    return u.hostname.toLowerCase().replace(/^www\./, '');
  } catch { return null; }
}
async function sendSnapshot() {
  const { token, client = 'chrome' } = await chrome.storage.local.get(['token', 'client']);
  if (!token) { await chrome.action.setBadgeText({text: '!'}); return; }
  try {
    const windows = await chrome.windows.getAll({populate: true, windowTypes: ['normal']});
    const {pageStates={}}=await chrome.storage.session.get('pageStates');
    const hosts = new Set();
    const descriptions=[];
    let activeHost = null, focused = false;
    for (const window of windows) {
      if (window.incognito) continue;
      if (window.focused && window.state !== 'minimized') focused = true;
      const tabs=(window.tabs||[]).filter(t=>!t.incognito);
      const active=tabs.filter(t=>t.active);
      const splitIds=new Set(active.map(t=>t.splitViewId).filter(id=>Number.isInteger(id)&&id>=0));
      const visibleHosts=new Set(),windowHosts=new Set(),focusedHosts=new Set();
      const httpTabs=tabs.filter(t=>hostname(t.url));
      const statesFor=tab=>Object.values(pageStates).filter(s=>s.tabId===tab.id&&Date.now()-s.at<75000);
      const complete=tabs.every(t=>Number.isInteger(t.splitViewId)) || httpTabs.every(t=>statesFor(t).some(s=>s.host===hostname(t.url)));
      for (const tab of tabs) {
        if (tab.incognito) continue;
        const host = hostname(tab.url);
        if (host) {hosts.add(host);windowHosts.add(host);}
        const states=statesFor(tab);
        if (host && window.state!=='minimized' && (tab.active || splitIds.has(tab.splitViewId))) visibleHosts.add(host);
        for(const state of states){
          if(state.visible && window.state!=='minimized'){
            hosts.add(state.host);windowHosts.add(state.host);visibleHosts.add(state.host);
            if(state.focused)focusedHosts.add(state.host);
          }
        }
      }
      if(window.focused && window.state!=='minimized') activeHost=focusedHosts.size===1?[...focusedHosts][0]:active.length===1?hostname(active[0].url):null;
      descriptions.push({Hosts:[...windowHosts],VisibleHosts:[...visibleHosts],Focused:window.focused,
        Minimized:window.state==='minimized',VisibilityComplete:complete,
        Bounds:[window.left,window.top,window.left+window.width,window.top+window.height]});
    }
    // Bound payload size even with many windows and very long hostnames.
    const selectedHosts=[...new Set([activeHost,...hosts].filter(Boolean))].slice(0,300);
    const included=new Set(selectedHosts);let remaining=300;
    const limitedWindows=descriptions.slice(0,64).map(d=>{
      const kept=d.Hosts.filter(h=>included.has(h)).slice(0,remaining);remaining-=kept.length;
      return {...d,Hosts:kept,VisibleHosts:d.VisibleHosts.filter(h=>kept.includes(h)),VisibilityComplete:d.VisibilityComplete&&kept.length===d.Hosts.length};
    });
    const response = await fetch(ENDPOINT + '/api/browser', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Tracking-Token': token},
      body: JSON.stringify({Client: client, ActiveHost: activeHost, Focused: focused, Hosts:selectedHosts,Windows:limitedWindows}),
      signal: AbortSignal.timeout(2500)
    });
    if (!response.ok) throw new Error(response.status === 401 ? 'Mã kết nối không đúng.' : 'App từ chối kết nối.');
    await chrome.storage.session.set({status: 'Đã kết nối', lastSuccess: Date.now()});
    await chrome.action.setBadgeText({text: ''});
  } catch (error) {
    await chrome.storage.session.set({status: error.message === 'Mã kết nối không đúng.' ? error.message : 'Chưa kết nối. Hãy mở TrackingApp-Python.exe.'});
    await chrome.action.setBadgeText({text: '!'});
    await chrome.action.setBadgeBackgroundColor({color: '#e08c54'});
  }
}
function schedule() {
  dirty=true;
  if(scheduled)return queue;
  scheduled=true;
  queue=queue.catch(()=>{}).then(async()=>{
    do {
      await new Promise(resolve=>setTimeout(resolve,Math.max(0,lastSent+1000-Date.now())));
      dirty=false;await sendSnapshot();lastSent=Date.now();
    } while(dirty);
  }).finally(()=>{scheduled=false;});
  return queue;
}
chrome.tabs.onActivated.addListener(schedule);
chrome.tabs.onCreated.addListener(schedule);
chrome.tabs.onRemoved.addListener(async id=>{
  const {pageStates={}}=await chrome.storage.session.get('pageStates');
  for(const key of Object.keys(pageStates))if(pageStates[key].tabId===id)delete pageStates[key];
  await chrome.storage.session.set({pageStates});schedule();
});
chrome.tabs.onAttached.addListener(schedule);
chrome.tabs.onDetached.addListener(schedule);
chrome.tabs.onReplaced.addListener(schedule);
chrome.tabs.onUpdated.addListener((id, info) => { if ('url' in info || 'splitViewId' in info) schedule(); });
chrome.windows.onFocusChanged.addListener(schedule);
chrome.windows.onRemoved.addListener(schedule);
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'heartbeat') schedule(); });
chrome.storage.onChanged.addListener((changes, area) => { if (area === 'local' && (changes.token || changes.client)) schedule(); });
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if(message.type==='tab-visibility'){
    if(!sender.tab || sender.tab.incognito || sender.frameId!==0)return false;
    const host=hostname(sender.url||sender.tab.url);if(!host)return false;
    queue=queue.catch(()=>{}).then(async()=>{
      const {pageStates={}}=await chrome.storage.session.get('pageStates');
      pageStates[sender.tab.id+':'+(sender.documentId||host)]={tabId:sender.tab.id,host,visible:message.visible===true,focused:message.focused===true,at:Date.now()};
      for(const key of Object.keys(pageStates))if(Date.now()-pageStates[key].at>75000)delete pageStates[key];
      const entries=Object.entries(pageStates);if(entries.length>1000)delete pageStates[entries[0][0]];
      await chrome.storage.session.set({pageStates});
    });
    queue.then(schedule);return false;
  }
  if (message.type !== 'connect') return false;
  schedule().then(() => reply({ok: true})).catch(() => reply({ok: false}));
  return true;
});
async function boot() {
  if (!(await chrome.alarms.get('heartbeat'))) await chrome.alarms.create('heartbeat', {periodInMinutes: 0.5});
  await schedule();
}
boot().catch(() => {});
