'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const uiSource=fs.readFileSync(path.join(__dirname,'../connect/web/push.js'),'utf8');
const workerSource=fs.readFileSync(path.join(__dirname,'../connect/web/push-sw.js'),'utf8');
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};

function fixture(options={}){
  const nodes=new Map(),calls=[],listeners={};let observer;
  class Node{
    constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    append(...values){this.children.push(...values);}
    insertBefore(value){this.children.push(value);}
    querySelector(){return null;}
    setAttribute(name,value){this.attributes[name]=value;}
  }
  const dialog=new Node('dialog');dialog.id='appearance-dialog';
  const document={documentElement:{lang:'zh-CN'},hidden:false,getElementById:id=>nodes.get(id),
    createElement:tag=>new Node(tag),addEventListener:(name,callback)=>{listeners[name]=callback;}};
  const key=Buffer.concat([Buffer.from([4]),Buffer.alloc(64,1)]).toString('base64url');
  const state={status:{available:true,publicKey:key,subscribed:false,events:{requests:true,completion:true}},
    permission:'default',grant:'granted',browser:null,registration:null,authenticated:true,fail:null,...options};
  function subscription(){
    return {endpoint:'https://push.example.test/subscription',options:{},
      toJSON:()=>({endpoint:'https://push.example.test/subscription',keys:{p256dh:'fixture-key',auth:'fixture-auth'}}),
      unsubscribe:async()=>{calls.push(['browser-unsubscribe']);state.browser=null;return true;}};
  }
  function registration(){return {scope:'https://relay.test/',active:{scriptURL:'https://relay.test/connect/push-sw.js'},
    pushManager:{getSubscription:async()=>{calls.push(['get-subscription']);return state.browser;},
      subscribe:async value=>{calls.push(['browser-subscribe',value]);state.browser=subscription();return state.browser;}}};}
  if(options.browser){state.browser=subscription();state.registration=registration();}
  const serviceWorker={getRegistration:async scope=>{calls.push(['get-registration',scope]);return state.registration;},
    register:async(...args)=>{calls.push(['register',...args]);state.registration=registration();return state.registration;},
    get ready(){return Promise.resolve(state.registration);}};
  const notification={get permission(){return state.permission;},requestPermission:()=>{
    calls.push(['request-permission']);state.permission=state.grant;return Promise.resolve(state.grant);}};
  const context={window:{MhenwaConnect:true,isSecureContext:true,matchMedia:()=>({matches:false})},document,
    navigator:{userAgent:'Fixture Android',platform:'Linux',maxTouchPoints:1,serviceWorker},
    Notification:notification,PushManager:function(){},location:{href:'https://relay.test/',origin:'https://relay.test',pathname:'/',search:'',hash:''},
    history:{replaceState:(_,__,url)=>calls.push(['replace-state',url])},
    MutationObserver:class{constructor(callback){observer=callback;}observe(){}},URL,Uint8Array,Promise,
    atob:value=>Buffer.from(value,'base64').toString('binary'),fetch:async(url,options)=>{
      calls.push(['fetch',url,options]);
      if(state.fail===url)return {ok:false,status:500,json:async()=>({error:'fixture failure'})};
      if(url==='/connect/me')return {ok:true,json:async()=>({authenticated:state.authenticated,csrf:'relay-phone-csrf'})};
      if(url==='/connect/push')return {ok:true,json:async()=>JSON.parse(JSON.stringify(state.status))};
      return {ok:true,json:async()=>url.endsWith('/test')?{queued:true}:{ok:true}};
    }};
  if(options.ios){context.navigator.userAgent='Mozilla iPhone';context.navigator.standalone=!!options.standalone;}
  if(options.insecure)context.window.isSecureContext=false;
  if(options.unsupported)delete context.PushManager;
  if(options.navigationMarker){context.location.search='?connect_notification=1';context.location.hash='#'+thread+'~local';}
  vm.runInNewContext(uiSource,context);context.window.MhenwaWebPush.mount();
  return {get:id=>nodes.get(id),calls,state,context,renderLanguage:lang=>{document.documentElement.lang=lang;observer();},
    changeVisibility:()=>listeners.visibilitychange(),nodes};
}
const writes=fixture=>fixture.calls.filter(call=>call[0]==='fetch'&&call[2].method==='POST');

test('initial mount checks status but never prompts, registers or subscribes; duplicate mount is harmless',async()=>{
  const f=fixture();await settle();f.context.window.MhenwaWebPush.mount();
  assert.equal(f.get('appearance-dialog').children.length,1);
  assert.equal(f.calls.filter(call=>['request-permission','register','browser-subscribe'].includes(call[0])).length,0);
  assert.deepEqual(f.calls.filter(call=>call[0]==='fetch').map(call=>call[1]),['/connect/push']);
  assert.equal(f.get('connect-push-enable').disabled,false);
  assert.equal(f.get('connect-push-test').disabled,true);
  assert.equal(f.get('connect-push-requests').checked,true);
  assert.equal(f.get('connect-push-completion').checked,true);
  assert.match(f.get('connect-push-status').textContent,/尚未开启/);
});

test('fixed notification reload marker is erased before app boot without changing the target chat',async()=>{
  const f=fixture({navigationMarker:true});await settle();
  assert.deepEqual(f.calls[0],['replace-state','/#'+thread+'~local']);
  assert.ok(f.calls.findIndex(call=>call[0]==='replace-state')<f.calls.findIndex(call=>call[0]==='fetch'));
});

test('click prompts synchronously before network, registers root worker and sends independent CSRF subscription',async()=>{
  const f=fixture();await settle();f.calls.length=0;
  const enabling=f.get('connect-push-enable').onclick();
  assert.equal(f.calls[0][0],'request-permission','user gesture precedes every await and network call');
  await enabling;
  const register=f.calls.find(call=>call[0]==='register');
  assert.equal(register[1],'/connect/push-sw.js');
  assert.deepEqual(JSON.parse(JSON.stringify(register[2])),{scope:'/',updateViaCache:'none'});
  const subscribe=f.calls.find(call=>call[0]==='browser-subscribe');
  assert.equal(subscribe[1].userVisibleOnly,true);assert.equal(subscribe[1].applicationServerKey.length,65);
  const post=writes(f)[0];assert.equal(post[1],'/connect/push/subscribe');
  assert.equal(post[2].credentials,'same-origin');assert.equal(post[2].headers['X-CSRF-Token'],'relay-phone-csrf');
  assert.deepEqual(JSON.parse(post[2].body),{subscription:{endpoint:'https://push.example.test/subscription',keys:{p256dh:'fixture-key',auth:'fixture-auth'}},events:{requests:true,completion:true}});
  assert.equal(f.get('connect-push-enable').hidden,true);assert.equal(f.get('connect-push-test').disabled,false);
});

test('permission denial and dismissal never create a worker or send writes',async()=>{
  for(const grant of ['denied','default']){
    const f=fixture({grant});await settle();await f.get('connect-push-enable').onclick();
    assert.equal(f.calls.filter(call=>['register','browser-subscribe'].includes(call[0])).length,0);
    assert.equal(writes(f).length,0);
    assert.match(f.get('connect-push-status').textContent,grant==='denied'?/被拒绝/:/尚未允许/);
  }
  const blocked=fixture({permission:'denied'});await settle();await blocked.get('connect-push-enable').onclick();
  assert.equal(blocked.calls.some(call=>call[0]==='request-permission'),false);
  assert.match(blocked.get('connect-push-status').textContent,/浏览器或系统设置/);
});

test('a saved subscription without currently granted permission never reports notifications enabled',async()=>{
  const f=fixture({browser:true,status:{available:true,publicKey:Buffer.concat([Buffer.from([4]),Buffer.alloc(64,1)]).toString('base64url'),subscribed:true,events:{requests:true,completion:true}}});
  await settle();assert.match(f.get('connect-push-status').textContent,/尚未允许通知/);
  assert.equal(f.get('connect-push-enable').hidden,false);assert.equal(f.get('connect-push-test').disabled,true);
});

test('unsupported browser, insecure context, and iOS outside home screen explain prerequisites without prompting',async()=>{
  for(const [options,expected] of [[{unsupported:true},/不支持 Web Push/],[{insecure:true},/HTTPS/],[{ios:true},/添加到主屏幕/]]){
    const f=fixture(options);await settle();assert.equal(f.get('connect-push-enable').disabled,true);
    await f.get('connect-push-enable').onclick();assert.match(f.get('connect-push-status').textContent,expected);
    assert.equal(f.calls.some(call=>call[0]==='request-permission'),false);assert.equal(writes(f).length,0);
  }
  const home=fixture({ios:true,standalone:true});await settle();await home.get('connect-push-enable').onclick();
  assert.equal(writes(home)[0][1],'/connect/push/subscribe');assert.equal(home.get('connect-push-ios').hidden,false);
});

test('VAPID-unconfigured service disables enabling without an authorization prompt',async()=>{
  const f=fixture({status:{available:false,publicKey:null,subscribed:false}});await settle();
  assert.equal(f.get('connect-push-enable').disabled,true);await f.get('connect-push-enable').onclick();
  assert.match(f.get('connect-push-status').textContent,/尚未配置/);
  assert.equal(f.calls.some(call=>call[0]==='request-permission'),false);
});

test('per-phone preferences are independent and tests report queued rather than delivered',async()=>{
  const f=fixture();await settle();await f.get('connect-push-enable').onclick();f.calls.length=0;
  f.get('connect-push-completion').checked=false;await f.get('connect-push-completion').onchange();
  assert.deepEqual(JSON.parse(writes(f)[0][2].body),{events:{requests:true,completion:false}});
  assert.equal(writes(f)[0][1],'/connect/push/preferences');
  await f.get('connect-push-test').onclick();assert.equal(writes(f)[1][1],'/connect/push/test');
  assert.match(f.get('connect-push-status').textContent,/排队不表示已经送达/);
  assert.equal(f.calls.filter(call=>call[0]==='fetch'&&call[1]==='/connect/me').length,2,'writes refresh the relay phone CSRF');
  f.renderLanguage('en');assert.match(f.get('connect-push-status').textContent,/queued does not mean delivered/);
  assert.equal(f.get('connect-push-title').textContent,'System notifications on this phone');
  assert.equal(f.get('connect-push-enable').textContent,'Enable notifications again');
});

test('failed preference write restores saved values and expired phone authorization sends no write',async()=>{
  const f=fixture();await settle();await f.get('connect-push-enable').onclick();
  f.state.fail='/connect/push/preferences';f.get('connect-push-requests').checked=false;await f.get('connect-push-requests').onchange();
  assert.equal(f.get('connect-push-requests').checked,true);assert.match(f.get('connect-push-status').textContent,/操作失败/);
  f.state.authenticated=false;f.calls.length=0;await f.get('connect-push-test').onclick();
  assert.equal(writes(f).length,0);assert.match(f.get('connect-push-status').textContent,/先连接并授权/);
});

test('disable revokes server delivery before browser subscription; browser-only revoked endpoint can be rebound explicitly',async()=>{
  const f=fixture();await settle();await f.get('connect-push-enable').onclick();f.calls.length=0;
  await f.get('connect-push-disable').onclick();
  assert.equal(writes(f)[0][1],'/connect/push/unsubscribe');assert.deepEqual(JSON.parse(writes(f)[0][2].body),{});
  assert.ok(f.calls.findIndex(call=>call[0]==='fetch'&&call[1]==='/connect/push/unsubscribe')<f.calls.findIndex(call=>call[0]==='browser-unsubscribe'));
  assert.equal(f.get('connect-push-test').disabled,true);assert.match(f.get('connect-push-status').textContent,/已关闭/);
  const old=fixture({browser:true,permission:'granted'});await settle();
  assert.match(old.get('connect-push-status').textContent,/服务记录已关闭/);old.calls.length=0;
  await old.get('connect-push-enable').onclick();
  assert.equal(old.calls.some(call=>call[0]==='browser-subscribe'),false);
  assert.equal(old.calls.some(call=>call[0]==='request-permission'),false);
  assert.equal(writes(old)[0][1],'/connect/push/subscribe');
});

test('on a stale browser record re-enable repairs it and selecting types before enabling is respected',async()=>{
  const f=fixture({status:{available:true,publicKey:Buffer.concat([Buffer.from([4]),Buffer.alloc(64,1)]).toString('base64url'),subscribed:true,events:{requests:false,completion:true}}});
  await settle();assert.match(f.get('connect-push-status').textContent,/订阅已失效/);
  f.get('connect-push-requests').checked=true;await f.get('connect-push-enable').onclick();
  assert.equal(JSON.parse(writes(f)[0][2].body).events.requests,true);
  const fresh=fixture();await settle();fresh.get('connect-push-requests').checked=false;await fresh.get('connect-push-requests').onchange();
  assert.equal(writes(fresh).length,0);await fresh.get('connect-push-enable').onclick();
  assert.equal(JSON.parse(writes(fresh)[0][2].body).events.requests,false);
});

function workerFixture(){
  const handlers={},shown=[],calls=[],clients=[];
  const self={location:{origin:'https://relay.test'},registration:{showNotification:async(...args)=>shown.push(args)},
    addEventListener:(name,callback)=>{handlers[name]=callback;},clients:{
      matchAll:async options=>{calls.push(['match-all',options]);return clients;},
      openWindow:async url=>{calls.push(['open',url]);}}};
  const context={self,URL};vm.runInNewContext(workerSource,context);
  return {handlers,shown,calls,clients,url:value=>context.notificationUrl(value),push:async value=>{
    let promise;handlers.push({data:value===undefined?null:{json:()=>value},waitUntil:value=>{promise=value;}});await promise;
  },click:async url=>{let promise;handlers.notificationclick({notification:{data:{url},close:()=>calls.push(['close'])},waitUntil:value=>{promise=value;}});await promise;}};
}
const thread='12345678-1234-1234-1234-123456789abc';

test('background push displays a system notification without any page, cache or fetch handler',async()=>{
  const f=workerFixture();assert.deepEqual(Object.keys(f.handlers).sort(),['notificationclick','push']);
  await f.push({title:'Codex 待确认',body:'电脑需要你的确认。',url:'/#'+thread+'~ssh%3Acomputer',tag:'approval-fixture'});
  assert.equal(f.shown.length,1);assert.equal(f.shown[0][0],'Codex 待确认');
  assert.equal(f.shown[0][1].data.url,'https://relay.test/#'+thread+'~ssh%3Acomputer');
  assert.equal(f.shown[0][1].tag,'approval-fixture');
  assert.doesNotMatch(workerSource,/\bcaches\b|addEventListener\(['"]fetch['"]|\bfetch\s*\(/);
  await f.push(undefined);assert.equal(f.shown[1][0],'Codex 提醒');
});

test('notification navigation rejects external URLs, credentials, unsafe hashes and paths; root is safe',()=>{
  const f=workerFixture(),root='https://relay.test/';
  for(const candidate of ['https://attacker.test/#'+thread,'https://user:password@relay.test/#'+thread,
    'javascript:alert(1)','//attacker.test/','/api/auth','/?token=fixture','/#connect_pair=secret',
    '/#'+thread+'~bad%0Ahost','/#'+thread+'~%ZZ','/#'+thread+'~bad%7Chost','/#'+thread+'~local#extra','/#'+thread+'~host~extra',
    '/#'+thread+'~ssh%2Fhost','/#'+thread+'~ssh%C2%A0host','/#'+thread+'~ssh%C2%85host','/#'+thread+'~ssh%E3%80%80host',null]){
    assert.equal(f.url(candidate),root,String(candidate));
  }
  assert.equal(f.url('/'),root);assert.equal(f.url('/#'+thread),root+'#'+thread);
  assert.equal(f.url('/#'+thread+'~ssh%3Ahost%28dev%29'),root+'#'+thread+'~ssh%3Ahost%28dev%29');
});

test('click navigates the existing same-origin root window to the exact new chat before focusing',async()=>{
  const f=workerFixture(),target='https://relay.test/#'+thread+'~local';
  f.clients.push({url:'https://attacker.test/',navigate:async()=>{throw Error('must not touch');}},
    {url:'https://relay.test/connect/',navigate:async()=>{throw Error('must not touch pairing');}},
    {url:'https://relay.test/#87654321-4321-4321-4321-cba987654321',navigate:async url=>{
      f.calls.push(['navigate',url]);return {focus:async()=>f.calls.push(['focus'])};}});
  await f.click(target);
  assert.deepEqual(f.calls.filter(call=>['navigate','focus','open'].includes(call[0])),[['navigate','https://relay.test/?connect_notification=1#'+thread+'~local'],['focus']]);
});

test('click opens a new window when there is no usable chat window; malformed payloads stay at root',async()=>{
  const f=workerFixture();f.clients.push({url:'https://relay.test/',navigate:async()=>null});
  await f.click('/#'+thread);assert.deepEqual(f.calls.at(-1),['open','https://relay.test/#'+thread]);
  await f.click('https://attacker.test/');assert.deepEqual(f.calls.at(-1),['open','https://relay.test/']);
  await f.push(['malformed']);assert.equal(f.shown.at(-1)[1].data.url,'https://relay.test/');
});
