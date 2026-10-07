'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function fixture(){
  const elements=new Map(),calls=[],copies=[];let observer,interval,now=1000000;
  class Node{
    constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.value='';this.checked=false;this.textContent='';this.classList={toggle(){}};}
    append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&this.children.length===1)this.value=nodes[0].value;}
    replaceChildren(...nodes){this.children=[...nodes];}
    removeAttribute(name){delete this[name];}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Node(id==='connect-provider'?'select':'div'));return elements.get(id);};
  const panel=new Node('section');panel.hidden=false;get('connect-name').value='Fixture computer';
  const state={enabled:true,registered:true,deviceId:'fixture-computer-id',deviceName:'Fixture computer',state:'online',message:'online'};
  const handlers={status:()=>({...state}),discover:()=>({providers:[{id:'fixture-provider',label:'Mhenwa',baseUrl:'https://api.mhenwa.cc/v1'}]}),
    pairings:()=>({pairings:[{id:'fixture-pairing',phoneName:'Fixture phone',state:'claimed'}]}),
    phones:()=>({phones:[{id:'fixture-phone',name:'Fixture phone'}]}),register:()=>state,
    approve:()=>({ok:true}),'revoke-phone':()=>({ok:true}),disable:()=>({...state,enabled:false,state:'disabled',message:'closed'}),
    pair:()=>({image:'data:image/png;base64,fixture',url:'https://codex.mhenwa.cc/#connect_pair=one-use-fixture',expires:now/1000+30})};
  // Connect is rendered inside the Network tab after the layout merge. Keep
  // this selector exact so a stale `[data-panel="connect"]` lookup cannot
  // silently pass the polling tests.
  const document={hidden:false,getElementById:get,
    querySelector:selector=>selector==='[data-panel="network"]'?panel:null,
    createElement:tag=>new Node(tag)};
  const context={window:{confirm:()=>true,bridgeDesktop:{connect:async value=>{calls.push(value);return handlers[value.action]();},
    copy:async value=>{copies.push(value);}}},
    document,MutationObserver:class{constructor(callback){observer=callback;}observe(){}},setInterval:callback=>{interval=callback;},console,URL,
    Date:class extends Date{static now(){return now;}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../desktop/connect.js'),'utf8'),context);
  return {get,calls,copies,handlers,state,panel,show:()=>observer(),tick:()=>interval(),advance:ms=>{now+=ms;},context};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('Network is the single Connect host and legacy settings are collapsed under Advanced',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');
  assert.equal((html.match(/data-tab="connect"/g)||[]).length,0);
  assert.equal((html.match(/data-panel="connect"/g)||[]).length,0);
  assert.equal((html.match(/data-tab="network"/g)||[]).length,1);
  assert.equal((html.match(/data-panel="network"/g)||[]).length,1);
  const detailsStart=html.indexOf('<details id="network-advanced"');
  assert.ok(detailsStart>html.indexOf('<div id="connect-content"'),'Connect content precedes legacy settings');
  const detailsTag=html.slice(detailsStart,html.indexOf('>',detailsStart)+1);
  assert.doesNotMatch(detailsTag,/\bopen(?:\s|=|>)/,'legacy settings start collapsed');
  for(const id of ['lan','lan-scope','lan-addresses','local-access','port','connections','connection-kind','origins','auth-mode','username','session-hours','password']){
    assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1,id+' remains available exactly once');
  }
});

test('register validates the provider and uses API Key qualification without a renderer consent checkbox',async()=>{
  const ui=fixture();await ui.get('connect-register').onclick();assert.equal(ui.calls.length,0);
  assert.match(ui.get('connect-feedback').textContent,/检测并选择/);
  await ui.get('connect-discover').onclick();assert.deepEqual(ui.calls.map(value=>value.action),['discover']);
  await ui.get('connect-register').onclick();assert.equal(ui.calls[1].action,'register');assert.equal(ui.calls[1].consent,true);
  assert.equal(ui.calls[1].provider,'fixture-provider');assert.equal(ui.calls[1].deviceName,'Fixture computer');
  assert.deepEqual(Object.keys(ui.calls[1]).sort(),['action','consent','deviceName','provider']);
  assert.match(ui.get('connect-feedback').textContent,/重新启动/);
  const source=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');
  assert.doesNotMatch(source,/connect-consent/);assert.match(source,/注册时会使用当前配置中的 API Key 验证 Mhenwa 服务资格/);
});

test('Connect action form never submits the surrounding settings form',()=>{
  const ui=fixture(),event={prevented:0,preventDefault(){this.prevented++;}};
  assert.equal(typeof ui.get('connect-actions').onsubmit,'function');
  ui.get('connect-actions').onsubmit(event);
  assert.equal(event.prevented,1);
});

test('paired phone is not auto-approved and explicit local approval uses exact claim identity',async()=>{
  const ui=fixture();ui.show();await settle();await settle();
  assert.deepEqual(ui.calls.map(value=>value.action),['status','pairings','phones']);
  const approve=ui.get('connect-pairings').children[0].children[1];
  await approve.onclick();await settle();assert.equal(ui.calls.filter(value=>value.action==='approve').length,1);
  const request=ui.calls.find(value=>value.action==='approve');assert.equal(request.id,'fixture-pairing');assert.equal(request.approved,true);
});

test('local confirmation cancellation blocks phone approval and revoke',async()=>{
  const ui=fixture();ui.context.window.confirm=()=>false;ui.show();await settle();await settle();
  await ui.get('connect-pairings').children[0].children[1].onclick();
  await ui.get('connect-phones').children[0].children[1].onclick();
  assert.equal(ui.calls.filter(value=>['approve','revoke-phone'].includes(value.action)).length,0);
});

test('one-use QR comes only from local IPC and disable removes it',async()=>{
  const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  assert.equal(ui.get('connect-qr').hidden,false);assert.match(ui.get('connect-qr-image').src,/^data:image/);
  await ui.get('connect-disable').onclick();assert.equal(ui.get('connect-qr').hidden,true);
  assert.equal(ui.calls.filter(value=>value.action==='disable').length,1);
  const source=fs.readFileSync(path.join(__dirname,'../desktop/connect.js'),'utf8');
  assert.doesNotMatch(source,/\bfetch\s*\(|new WebSocket|apiKey|deviceToken/);
});

test('one-use Connect link is visible, readonly, and copies the exact pairing URL instead of a QR image or landing page',async()=>{
  const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  const expected='https://codex.mhenwa.cc/#connect_pair=one-use-fixture';
  assert.equal(ui.get('connect-pair-link').value,expected);
  assert.equal(ui.get('connect-qr').hidden,false);
  assert.equal(ui.get('connect-copy-link').disabled,false);
  await ui.get('connect-copy-link').onclick();
  assert.deepEqual(ui.copies,[expected]);
  assert.notEqual(ui.copies[0],ui.get('connect-qr-image').src);
  assert.notEqual(ui.copies[0],'https://codex.mhenwa.cc/');
  assert.match(ui.get('connect-feedback').textContent,/复制/);
  const html=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');
  const input=html.match(/<input\b[^>]*id="connect-pair-link"[^>]*>/)?.[0];
  assert.ok(input,'pairing URL has an input field');
  assert.match(input,/\breadonly(?:\s|=|>)/,'display cannot edit the URL to copy');
  const button=html.match(/<button\b[^>]*id="connect-copy-link"[^>]*>/)?.[0];
  assert.ok(button,'one-use pairing link has its own copy button');
  assert.match(button,/\btype="button"/,'copy does not submit settings');
});

test('Connect disables and clears both one-use QR and link when grant expires, even with pane hidden',async()=>{
  const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  ui.panel.hidden=true;ui.advance(31000);ui.tick();await settle();
  assert.equal(ui.get('connect-qr').hidden,true);
  assert.equal(ui.get('connect-pair-link').value,'');
  assert.equal(ui.get('connect-copy-link').disabled,true);
  assert.equal(ui.get('connect-qr-image').src,undefined);
  await ui.get('connect-copy-link').onclick();
  assert.deepEqual(ui.copies,[]);
});

test('Connect copy checks expiry immediately, before the polling timer runs',async()=>{
  const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  ui.advance(31000);await ui.get('connect-copy-link').onclick();
  assert.deepEqual(ui.copies,[]);
  assert.equal(ui.get('connect-pair-link').value,'');
  assert.equal(ui.get('connect-qr').hidden,true);
});

test('local disable and externally observed disabled status remove stale Connect link contents',async()=>{
  for(const external of [false,true]){
    const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
    if(external){Object.assign(ui.state,{enabled:false,state:'disabled'});ui.tick();await settle();await settle();}
    else await ui.get('connect-disable').onclick();
    assert.equal(ui.get('connect-qr').hidden,true);
    assert.equal(ui.get('connect-pair-link').value,'');
    assert.equal(ui.get('connect-copy-link').disabled,true);
    assert.equal(ui.get('connect-qr-image').src,undefined);
    await ui.get('connect-copy-link').onclick();assert.deepEqual(ui.copies,[]);
  }
});

test('Connect copy failure gives error feedback rather than claiming a successful copy',async()=>{
  const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  ui.context.window.bridgeDesktop.copy=async()=>{throw Error('clipboard unavailable');};
  await ui.get('connect-copy-link').onclick();
  assert.equal(ui.get('connect-feedback').textContent,'clipboard unavailable');
  assert.deepEqual(ui.copies,[]);
  assert.equal(ui.get('connect-pair-link').value,'https://codex.mhenwa.cc/#connect_pair=one-use-fixture');
});

test('Connect has no copyable link before generation and cannot copy an old grant while regenerating',async()=>{
  const ui=fixture();await ui.get('connect-copy-link').onclick();assert.deepEqual(ui.copies,[]);
  ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  let resolve;ui.handlers.pair=()=>new Promise(done=>{resolve=done;});
  const generating=ui.get('connect-pair').onclick();await settle();
  await ui.get('connect-copy-link').onclick();assert.deepEqual(ui.copies,[]);
  resolve({image:'data:image/png;base64,newfixture',url:'https://codex.mhenwa.cc/#connect_pair=replaced-fixture',expires:1030});
  await generating;assert.equal(ui.get('connect-pair-link').value,'https://codex.mhenwa.cc/#connect_pair=replaced-fixture');
  await ui.get('connect-copy-link').onclick();assert.deepEqual(ui.copies,['https://codex.mhenwa.cc/#connect_pair=replaced-fixture']);
});

test('Connect rejects incomplete or already expired pairing grants without exposing a copyable link',async()=>{
  for(const grant of [
    {image:'data:image/png;base64,fixture',expires:1030},
    {image:'data:image/png;base64,fixture',url:'https://codex.mhenwa.cc/#connect_pair=expired-fixture',expires:999},
  ]){
    const ui=fixture();ui.show();await settle();await settle();ui.handlers.pair=()=>grant;
    await ui.get('connect-pair').onclick();
    assert.equal(ui.get('connect-qr').hidden,true);
    assert.equal(ui.get('connect-pair-link').value,'');
    assert.equal(ui.get('connect-copy-link').disabled,true);
    assert.match(ui.get('connect-feedback').textContent,/无效|过期/);
    await ui.get('connect-copy-link').onclick();assert.deepEqual(ui.copies,[]);
  }
});

test('Connect clears a previous pairing link when status switches to another computer identity',async()=>{
  const ui=fixture();ui.show();await settle();await settle();await ui.get('connect-pair').onclick();
  ui.state.deviceId='different-fixture-computer';ui.tick();await settle();await settle();
  assert.equal(ui.get('connect-qr').hidden,true);
  assert.equal(ui.get('connect-pair-link').value,'');
  assert.equal(ui.get('connect-copy-link').disabled,true);
  await ui.get('connect-copy-link').onclick();assert.deepEqual(ui.copies,[]);
});

test('hidden Connect pane pauses network control polling',async()=>{
  const ui=fixture();ui.panel.hidden=true;ui.tick();await settle();assert.equal(ui.calls.length,0);
  ui.panel.hidden=false;ui.tick();await settle();assert.ok(ui.calls.some(value=>value.action==='status'));
});

test('Connect desktop IPC is local-only and worker allowlist is explicit',()=>{
  const main=fs.readFileSync(path.join(__dirname,'../desktop/main.cjs'),'utf8');
  assert.match(main,/ipcMain\.handle\('bridge:connect',[\s\S]*?authorize\(event\)/);
  const preload=fs.readFileSync(path.join(__dirname,'../desktop/preload.cjs'),'utf8');
  assert.match(preload,/connect:value=>ipcRenderer\.invoke\('bridge:connect',value\)/);
  assert.match(fs.readFileSync(path.join(__dirname,'../desktop/controller.cjs'),'utf8'),/'pairing','connect'/);
});

test('fork desktop project links stay on Mhenwa repository',()=>{
  const main=fs.readFileSync(path.join(__dirname,'../desktop/main.cjs'),'utf8');
  for(const [target,suffix] of [['project-home',''],['project-issues','/issues'],['project-pulls','/pulls'],['releases','/releases']]){
    assert.ok(main.includes(`if(target==='${target}')return shell.openExternal('https://github.com/Mhenwa/codex-mobile-bridge${suffix}');`));
  }
  assert.doesNotMatch(main,/shell\.openExternal\('https:\/\/github\.com\/try2love\/codex-mobile-bridge/);
});
