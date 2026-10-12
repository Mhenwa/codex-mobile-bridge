'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../shared/web/mobile-system-bars.js'),'utf8');
function page(){
  const events={},frames=[],calls=[],observed=[];
  const html={color:'rgb(255, 255, 255)'},body={color:'rgb(10, 20, 30)',parentElement:html};
  const top={color:'rgb(30, 40, 50)',parentElement:body},bottom={color:'rgb(240, 241, 242)',parentElement:body};
  let accepted=true,observer;
  const document={documentElement:html,body,getElementById:()=>body,querySelectorAll:()=>[],elementFromPoint:(_x,y)=>y===1?top:bottom,addEventListener:(name,callback)=>events[name]=callback};
  const window={addEventListener:(name,callback)=>events[name]=callback,prompt:(name,value)=>{calls.push([name,JSON.parse(value)]);return accepted?'saved':null;}};window.top=window;
  const context=vm.createContext({window,document,innerWidth:390,innerHeight:800,getComputedStyle:element=>({backgroundColor:element.color}),requestAnimationFrame:callback=>frames.push(callback),MutationObserver:class{constructor(callback){observer=callback;}observe(node,options){observed.push({node,options});}}});
  vm.runInContext(source,context);
  return {calls,events,top,bottom,observed,flush:()=>{while(frames.length)frames.shift()();},accept:value=>{accepted=value;},change:()=>observer([])};
}
test('shared theme sampler sends top and bottom colors once until they change',()=>{
  const ui=page();ui.flush();assert.deepEqual(ui.calls[0][1],{top:'#1e2832',bottom:'#f0f1f2'});
  ui.events.resize();ui.change();ui.flush();assert.equal(ui.calls.length,1);
  ui.bottom.color='rgb(0, 0, 0)';ui.events.resize();ui.flush();assert.equal(ui.calls[1][1].bottom,'#000000');
  assert.ok(ui.observed.every(value=>!value.options.subtree));
});
test('theme sampler retries after a native rejection and foreground return',()=>{
  const ui=page();ui.accept(false);ui.flush();assert.equal(ui.calls.length,1);
  ui.accept(true);ui.events.focus();ui.events.visibilitychange();ui.flush();assert.equal(ui.calls.length,2);
  ui.events.focus();ui.flush();assert.equal(ui.calls.length,2);
});
test('transparent elements use their opaque parent background',()=>{
  const ui=page();ui.top.color='rgba(255, 255, 255, 0)';ui.flush();assert.equal(ui.calls[0][1].top,'#0a141e');
});
