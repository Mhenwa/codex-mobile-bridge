'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

function fixture(){
  let now=0,id=0,calls=0,ready=true,visible=true,refresh=async()=>{};
  const timers=new Map(),frames=new Map(),events={};
  const node=()=>({hidden:true,textContent:'',scrollTop:0,style:{setProperty(key,value){this[key]=value;}},classList:{values:new Set(),add(key){this.values.add(key);},remove(key){this.values.delete(key);},toggle(key,on){on?this.add(key):this.remove(key);}},setAttribute(key,value){this[key]=value;},addEventListener(type,fn){(events[type]??=[]).push(fn);}});
  const root=node(),status=node();let writes=0,text='';Object.defineProperty(status,'textContent',{get:()=>text,set:value=>{writes++;text=value;}});
  const context=vm.createContext({BridgeI18n:{t:value=>value},document:{hidden:false,addEventListener(){}},window:{addEventListener(){}},
    setTimeout:(fn,delay)=>{timers.set(++id,{fn,at:now+delay});return id;},clearTimeout:key=>timers.delete(key),requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:key=>frames.delete(key)});
  vm.runInContext(fs.readFileSync(require.resolve('../web/features/chat/list-sync.js'),'utf8'),context);
  const sync=new (vm.runInContext('ListSync',context))({root,status,visible:()=>visible,ready:()=>ready,refresh:async()=>{calls++;await refresh();}});
  const frame=()=>{const tasks=[...frames.values()];frames.clear();for(const fn of tasks)fn();};
  const advance=ms=>{const end=now+ms;while(true){const next=[...timers].filter(([,timer])=>timer.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();}now=end;};
  const touch=(type,y,x=30,count=1)=>{const event={touches:['touchend','touchcancel'].includes(type)?[]:Array.from({length:count},()=>({clientX:x,clientY:y})),preventDefault(){this.prevented=true;}};for(const fn of events[type]||[])fn(event);return event;};
  return {sync,root,status,frame,advance,touch,get calls(){return calls;},get writes(){return writes;},setReady:value=>{ready=value;},setVisible:value=>{visible=value;},setRefresh:fn=>{refresh=fn;}};
}
const settled=()=>new Promise(setImmediate);

test('pull feedback is coalesced per frame and only changes text at the release threshold',()=>{
  const ui=fixture();ui.touch('touchstart',20);
  for(let y=32;y<=70;y++)ui.touch('touchmove',y);
  assert.equal(ui.writes,0);ui.frame();assert.equal(ui.writes,1);assert.match(ui.status.textContent,/下拉/);
  for(let y=71;y<=90;y++)ui.touch('touchmove',y);
  ui.frame();assert.equal(ui.writes,1);
  ui.touch('touchmove',110);ui.frame();assert.equal(ui.writes,2);assert.match(ui.status.textContent,/松开/);
});

test('successful refresh dismisses its hint within 800ms and resets pull offset',async()=>{
  const ui=fixture();await ui.sync.update(true);assert.match(ui.status.textContent,/已更新/);
  ui.advance(800);assert.equal(ui.status.hidden,true);assert.equal(ui.root.style['--list-pull'],'0px');
});

test('a new pull is not hidden by the previous success timer and background refresh waits for release',async()=>{
  const ui=fixture();await ui.sync.update(true);ui.advance(400);
  ui.touch('touchstart',20);ui.touch('touchmove',110);ui.frame();
  await ui.sync.update();assert.equal(ui.calls,1);
  ui.advance(3000);assert.equal(ui.status.hidden,false);assert.match(ui.status.textContent,/松开/);
  ui.touch('touchend',110);await settled();assert.equal(ui.calls,2);
});

test('cancelling, reversing or switching to multiple fingers never refreshes or leaves stale feedback',async()=>{
  for(const end of ['cancel','reverse','multi','multiStart','horizontal']){
    const ui=fixture();ui.touch('touchstart',20);ui.touch('touchmove',110);ui.frame();
    if(end==='cancel')ui.touch('touchcancel',110);
    if(end==='reverse')ui.touch('touchmove',25);
    if(end==='multi')ui.touch('touchmove',110,30,2);
    if(end==='multiStart')ui.touch('touchstart',110,30,2);
    if(end==='horizontal')ui.touch('touchmove',110,180);
    ui.touch('touchend',110);ui.frame();ui.advance(800);await settled();
    assert.equal(ui.calls,0,end);assert.equal(ui.status.hidden,true,end);assert.equal(ui.root.style['--list-pull'],'0px',end);
  }
});

test('busy, hidden and unavailable lists reject pull gestures and failure remains retryable',async()=>{
  const ui=fixture();ui.setReady(false);ui.touch('touchstart',20);ui.touch('touchmove',110);ui.frame();assert.equal(ui.status.hidden,true);ui.touch('touchend',110);
  ui.setReady(true);ui.setVisible(false);ui.touch('touchstart',20);ui.touch('touchmove',110);ui.frame();assert.equal(ui.status.hidden,true);ui.touch('touchend',110);assert.equal(ui.calls,0);
  ui.setVisible(true);let finish;ui.setRefresh(()=>new Promise(resolve=>{finish=resolve;}));const pending=ui.sync.update(true);ui.touch('touchstart',20);ui.touch('touchmove',110);ui.touch('touchend',110);assert.equal(ui.calls,1);finish();await pending;
  ui.setRefresh(async()=>{throw Error('offline');});await ui.sync.update(true);ui.advance(5000);assert.equal(ui.status.hidden,false);assert.match(ui.status.textContent,/点击重试/);
  ui.setRefresh(async()=>{});ui.status.onclick();await settled();assert.equal(ui.calls,3);ui.advance(800);assert.equal(ui.status.hidden,true);
});
