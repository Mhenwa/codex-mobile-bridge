'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../connect/web/pair.js'),'utf8');

function harness({hash='',responses=[]}={}){
  const elements=new Map(),calls=[],timers=[];
  const element=id=>{if(!elements.has(id))elements.set(id,{hidden:true,textContent:'',value:'手机',disabled:false});return elements.get(id);};
  const location={hash,pathname:'/',search:'',replace:value=>calls.push(['navigate',value])};
  const context={document:{getElementById:element},location,
    history:{replaceState:(_,__,value)=>{calls.push(['erase',value]);location.hash='';}},
    setTimeout:fn=>timers.push(fn),fetch:async(url,options)=>{
      calls.push(['fetch',url,options]);
      const reply=responses.shift()||{ok:true,body:{authenticated:false}};
      return {ok:reply.ok,json:async()=>reply.body};
    }};
  vm.runInNewContext(source,context);
  return {element,calls,timers,tick:async()=>{for(let i=0;i<12;i++)await Promise.resolve();}};
}

test('pairing secret is erased before network and never stored; explicit submit only',async()=>{
  const h=harness({hash:'#connect_pair=one-use-fixture',responses:[
    {ok:true,body:{claimToken:'independent-claim'}},{ok:true,body:{state:'pending'}}]});
  await h.tick();
  assert.deepEqual(h.calls,[['erase','/']]);
  assert.equal(h.element('claim-form').hidden,false);
  await h.element('claim-form').onsubmit({preventDefault(){}});
  assert.equal(h.calls[1][1],'/connect/pair/claim');
  assert.deepEqual(JSON.parse(h.calls[1][2].body),{token:'one-use-fixture',phoneName:'手机'});
  assert.equal(h.calls[2][1],'/connect/pair/status');
  assert.deepEqual(JSON.parse(h.calls[2][2].body),{claimToken:'independent-claim'});
  assert.equal(h.calls[1][2].credentials,'same-origin');
  assert.equal(h.timers.length,1);
  assert.ok(!source.includes('localStorage')&&!source.includes('sessionStorage'));
});
test('only desktop-approved claim enters original app',async()=>{
  const h=harness({hash:'#connect_pair=fixture',responses:[
    {ok:true,body:{claimToken:'claim'}},{ok:true,body:{state:'pending'}},
    {ok:true,body:{state:'approved',csrf:'independent-csrf'}}]});
  await h.element('claim-form').onsubmit({preventDefault(){}});
  assert.equal(h.calls.some(call=>call[0]==='navigate'),false);
  await h.timers[0]();
  assert.deepEqual(h.calls.at(-1),['navigate','/']);
});
test('denied claim stops and renders errors as text, not HTML',async()=>{
  const h=harness({hash:'#connect_pair=fixture',responses:[
    {ok:true,body:{claimToken:'claim'}},{ok:true,body:{state:'denied'}}]});
  await h.element('claim-form').onsubmit({preventDefault(){}});
  assert.equal(h.timers.length,0);
  assert.equal(h.element('error').hidden,false);
  assert.ok(h.element('error').textContent.includes('过期'));
  assert.equal(h.calls.some(call=>call[0]==='navigate'),false);
  assert.ok(!source.includes('innerHTML'));
});
test('public landing asks for no model API credential',async()=>{
  const h=harness();await h.tick();
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0][1],'/connect/me');
  const html=fs.readFileSync(path.join(__dirname,'../connect/web/index.html'),'utf8');
  assert.ok(!html.includes('type="password"'));
  assert.ok(html.includes('不等于端到端加密'));
});
test('platform hides administrative UI and redirects old authorized pairing links',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../connect/web/platform.js'),'utf8');
  let callback,redirect;const hidden=[];
  const window={};
  vm.runInNewContext(source,{window,location:{hash:'#connect_pair=fixture',replace:value=>{redirect=value;}},
    document:{addEventListener:(_,fn)=>{callback=fn;},getElementById:id=>({closest:()=>null,set hidden(v){if(v)hidden.push(id);},set disabled(v){}}),
      }});
  assert.equal(window.MhenwaConnect,true);
  assert.equal(redirect,'/connect/#connect_pair=fixture');
  callback();assert.ok(hidden.includes('accounts-button'));
  assert.ok(fs.readFileSync(path.join(__dirname,'../connect/web/platform.css'),'utf8').includes('!important'));
});
test('platform uploads do not replay automatically on unknown network outcomes',async()=>{
  const app=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
  const fn=app.match(/async function uploadAttachmentWithRetry\([^\n]+/)[0];
  let attempts=0;
  const context={window:{MhenwaConnect:true},uploadAttachment:async()=>{attempts++;throw TypeError('network unknown');}};
  vm.runInNewContext(fn+';globalThis.upload=uploadAttachmentWithRetry;',context);
  await assert.rejects(context.upload('local|fixture','upload-fixture',{}),/network unknown/);
  assert.equal(attempts,1);
});

function stopHarness(host,{error}={}){
  const app=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
  const sessionUrl=app.match(/^function sessionUrl\([^\n]+/m)[0];
  const handler=app.match(/^\$\('stop'\)\.onclick=[^\n]+/m)[0];
  const node={},calls=[],toasts=[];
  const context={currentId:'00000000-0000-4000-8000-000000000001',currentHost:host,
    $:id=>{assert.equal(id,'stop');return node;},encodeURIComponent,
    api:async(url,body)=>{calls.push({url,body});if(error)throw Error(error);},
    toast:value=>toasts.push(value)};
  vm.runInNewContext(sessionUrl+'\n'+handler,context);
  return {stop:node.onclick,calls,toasts};
}

test('phone stop targets the selected SSH host, not the local default',async()=>{
  const h=stopHarness('remote:mhenwa alias');await h.stop();
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].url,
    '/api/sessions/00000000-0000-4000-8000-000000000001/stop?host=remote%3Amhenwa%20alias');
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].body)),{});
  assert.deepEqual(h.toasts,['已请求停止']);
});

test('phone stop preserves local selection and does not retry an unknown result',async()=>{
  const h=stopHarness('local',{error:'desktop outcome unknown'});await h.stop();
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].url,
    '/api/sessions/00000000-0000-4000-8000-000000000001/stop?host=local');
  assert.deepEqual(h.toasts,['desktop outcome unknown']);
});
