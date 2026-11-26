const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
let windows,session={},sent,now=1_000_000;
const listeners={};const event=name=>({addListener(fn){listeners[name]=fn;}});
const chrome={
  windows:{getAll:async()=>windows,onFocusChanged:event('focus'),onRemoved:event('removeWindow')},
  tabs:Object.fromEntries(['onActivated','onCreated','onRemoved','onAttached','onDetached','onReplaced','onUpdated'].map(n=>[n,event(n)])),
  storage:{local:{get:async()=>({token:'a'.repeat(64),client:'chrome'})},session:{get:async()=>structuredClone(session),set:async v=>Object.assign(session,structuredClone(v))},onChanged:event('storage')},
  alarms:{get:async()=>({}),create:async()=>{},onAlarm:event('alarm')},
  runtime:{onMessage:event('message')},
  action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}}
};
class Clock extends Date{static now(){return now;}}
const context=vm.createContext({chrome,URL,AbortSignal,Date:Clock,setTimeout:(fn,delay)=>{now+=delay;return setImmediate(fn);},fetch:async(url,options)=>{sent=JSON.parse(options.body);return {ok:true};}});
windows=[{focused:true,state:'normal',left:0,top:0,width:1000,height:800,tabs:[
 {id:1,active:true,splitViewId:42,url:'https://youtube.com/watch?v=private'},
 {id:2,active:false,splitViewId:42,url:'https://github.com/private'},
 {id:3,active:false,splitViewId:-1,url:'https://facebook.com/private'},
 {id:4,active:false,splitViewId:-1,url:'https://private.example/',incognito:true}
]}];
vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser-extension/bridge.js'),'utf8'),context);
async function snapshot(){now+=2000;await vm.runInContext('schedule()',context);return sent;}
(async()=>{
  await vm.runInContext('boot()',context);
  assert.deepEqual(sent.Windows[0].VisibleHosts.sort(),['github.com','youtube.com']);
  assert.equal(sent.ActiveHost,'youtube.com');assert.equal(sent.Windows[0].VisibilityComplete,true);
  assert.equal(JSON.stringify(sent).includes('private'),false);
  // A split implementation may expose two visible documents under one tab ID.
  session={};windows[0].tabs=[{id:1,active:true,url:'https://youtube.com/watch'}];
  listeners.message({type:'tab-visibility',visible:true,focused:false},{tab:windows[0].tabs[0],frameId:0,documentId:'left',url:'https://youtube.com/watch'},()=>{});
  await vm.runInContext('queue',context);
  listeners.message({type:'tab-visibility',visible:true,focused:true},{tab:windows[0].tabs[0],frameId:0,documentId:'right',url:'https://github.com/private'},()=>{});
  await vm.runInContext('queue',context);await snapshot();
  assert.deepEqual(sent.Windows[0].VisibleHosts.sort(),['github.com','youtube.com']);
  assert.equal(sent.ActiveHost,'github.com');
  windows[0].focused=false;await snapshot();assert.equal(sent.ActiveHost,null);
  windows[0].state='minimized';await snapshot();assert.deepEqual(sent.Windows[0].VisibleHosts,[]);
  session={};windows[0].state='normal';windows[0].tabs.push({id:3,active:false,url:'https://facebook.com/'});
  await snapshot();assert.equal(sent.Windows[0].VisibilityComplete,false);
  windows=[];await snapshot();assert.deepEqual(sent.Hosts,[]);
  console.log('PASS: Chrome split groups, multiple visible documents, focused pane, minimized/unfocused windows, unknown visibility fallback, incognito exclusion, no complete URLs.');
})().catch(e=>{console.error(e);process.exitCode=1;});
