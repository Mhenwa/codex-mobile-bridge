'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {pathToFileURL}=require('node:url');
const {createPairingLinks}=require('../desktop/pairing-links.cjs');
const legacy={id:'legacy-id',url:'https://fixture.example/#pair=one-use-secret',expires:200,state:'active'};
const connect={pairingId:'connect-id',url:'https://fixture.example/#connect_pair=one-use-secret',expires:200};

test('only exact locally issued URL is copyable before its deadline',()=>{
  let now=100;const links=createPairingLinks({clock:()=>now});
  assert.equal(links.has(legacy.url),false);links.remember('legacy',legacy);
  assert.equal(links.has(legacy.url),true);
  for(const value of [legacy.url+'&extra=1',legacy.url.replace('fixture.example','attacker.example'),{},null])assert.equal(links.has(value),false);
  now=200;assert.equal(links.has(legacy.url),false);
});
test('invalid or expired grant fails closed without echoing bearer URL',()=>{
  const links=createPairingLinks({clock:()=>100});
  for(const grant of [{...legacy,expires:100},{...legacy,expires:'200'},{...legacy,expires:Infinity},
    {...legacy,url:'javascript:one-use-secret'},{...legacy,url:'https://user:one-use-secret@fixture.example/'},{...legacy,id:''}]){
    assert.throws(()=>links.remember('legacy',grant),error=>!error.message.includes('one-use-secret'));
    assert.equal(links.has(grant.url),false);
  }
});
test('identifier replacement and capacity bounds retire older capabilities',()=>{
  const links=createPairingLinks({clock:()=>100,limit:2});
  links.remember('legacy',legacy);
  const replaced={...legacy,url:legacy.url+'replacement'};links.remember('legacy',replaced);
  assert.equal(links.has(legacy.url),false);assert.equal(links.has(replaced.url),true);
  links.remember('connect',connect);links.remember('legacy',{...legacy,id:'another',url:legacy.url+'another'});
  assert.equal(links.has(replaced.url),false);assert.equal(links.has(connect.url),true);
});
test('legacy used/expired status and revoke cleanup cannot revoke another scope',()=>{
  const links=createPairingLinks({clock:()=>100});links.remember('legacy',legacy);links.remember('connect',connect);
  links.legacyStatus([legacy.id],{[legacy.id]:'active'});assert.equal(links.has(legacy.url),true);
  links.legacyStatus([legacy.id],{[legacy.id]:'used'});assert.equal(links.has(legacy.url),false);assert.equal(links.has(connect.url),true);
  links.remember('legacy',legacy);links.legacyStatus([legacy.id],{});assert.equal(links.has(legacy.url),false);
  links.remember('legacy',legacy);links.forget('legacy',legacy.id);assert.equal(links.has(legacy.url),false);assert.equal(links.has(connect.url),true);
});
test('Connect claimed/approved rows retire URL but omission is not a revocation',()=>{
  const links=createPairingLinks({clock:()=>100});links.remember('legacy',legacy);links.remember('connect',connect);
  links.connectPairings([]);assert.equal(links.has(connect.url),true);
  links.connectPairings([{id:connect.pairingId,state:'offered'}]);assert.equal(links.has(connect.url),true);
  links.connectPairings([{id:connect.pairingId,state:'claimed'}]);assert.equal(links.has(connect.url),false);assert.equal(links.has(legacy.url),true);
  links.remember('connect',connect);links.forget('connect');assert.equal(links.has(connect.url),false);assert.equal(links.has(legacy.url),true);
  links.clear();assert.equal(links.has(legacy.url),false);
});

async function mainFixture(){
  const handlers=new Map(),writes=[],actions=[],persisted=[];let currentWindow,failImage=false,clipboardWriter=async value=>{writes.push(value);};
  const expires=Date.now()/1000+300;
  const legacyGrant={...legacy,expires},connectGrant={...connect,expires};
  const sourceRoot=path.resolve(__dirname,'..'),desktop=path.join(sourceRoot,'desktop');
  const snapshot={runtime:{running:true},urls:['https://fixture.example/'],preferences:{}};
  let answer=(action,payload)=>{
    if(action==='snapshot')return snapshot;
    if(action==='pairing')return payload.action==='create'?legacyGrant:payload.action==='status'?{states:{[legacyGrant.id]:'active'}}:{};
    if(action==='connect')return payload.action==='pair'?connectGrant:payload.action==='pairings'?{pairings:[]}:payload.action==='status'?{enabled:true}:{};
    return {};
  };
  class BrowserWindow{
    constructor(){currentWindow=this;this.webContents={mainFrame:{url:pathToFileURL(path.join(desktop,'index.html')).href},setWindowOpenHandler(){},on(){},once(){}};}
    on(){}loadFile(){}getTitle(){return '';}setTitle(){}isMinimized(){return false;}show(){}focus(){}
  }
  const app={isPackaged:false,setPath(){},getPath(){return '/isolated-fixture';},getVersion(){return '1.3.3';},getLocale(){return 'zh-CN';},requestSingleInstanceLock:()=>true,whenReady:()=>Promise.resolve(),on(){},quit(){}};
  const electron={app,BrowserWindow,ipcMain:{handle:(name,fn)=>handlers.set(name,fn)},clipboard:{writeText:value=>clipboardWriter(value)},
    dialog:{showOpenDialog:async()=>({canceled:false,filePaths:['/different-isolated-fixture']})},shell:{openExternal(){},openPath(){}},net:{}};
  const runWorker=async(_options,action,payload)=>{actions.push({action,payload});return answer(action,payload);};
  const mockedFs={...fs,existsSync:()=>false,mkdirSync(){},writeFileSync:(file,value)=>persisted.push({file,value}),readFileSync:file=>{
    if(String(file).endsWith('update-public-key.pem'))return Buffer.from('isolated-public-key');throw Error('fixture file absent');
  }};
  function mockRequire(name){
    if(name==='electron')return electron;
    if(name==='node:fs')return mockedFs;
    if(name==='./controller.cjs')return {runWorker,workerFor:()=>({}),createSnapshotWorker:()=>({read:()=>runWorker(null,'snapshot'),close(){}})};
    if(name==='./qr.cjs')return {pairingImage:async grant=>{if(failImage)throw Error('QR fixture failure');return {...grant,image:'data:image/png;base64,fixture'};}};
    if(name==='./updater.cjs')return {allowedUrl(){},Updater:class{busy=false;status(){return {};}check(){}}};
    if(name==='./cloudflared.cjs')return {electronFetch(){}};
    if(name.startsWith('./'))return require(path.join(desktop,name));
    return require(name);
  }
  const context=vm.createContext({require:mockRequire,__dirname:desktop,process:{env:{CMB_DATA_DIR:'/isolated-fixture'},platform:'linux',resourcesPath:'/resources',execPath:'/isolated-app',pid:1,arch:'x64'},
    URL,Date,Buffer,setTimeout:()=>({unref(){}}),setInterval:()=>({unref(){}})});
  vm.runInContext(fs.readFileSync(path.join(desktop,'main.cjs'),'utf8'),context,{filename:'desktop/main.cjs'});await new Promise(setImmediate);
  const event={sender:currentWindow.webContents,senderFrame:currentWindow.webContents.mainFrame};
  return {invoke:(name,payload,source=event)=>handlers.get('bridge:'+name)(source,payload),writes,actions,persisted,legacyGrant,connectGrant,
    respond:fn=>{answer=fn;},failImage:()=>{failImage=true;},clipboardWriter:fn=>{clipboardWriter=fn;},event};
}
test('real Main IPC copies issued legacy/Connect URL, not arbitrary or altered URL; authorization remains mandatory',async()=>{
  const ui=await mainFixture();
  await assert.rejects(ui.invoke('copy',ui.legacyGrant.url),/地址不可用/);
  const result=await ui.invoke('pairing',{action:'create',url:'https://fixture.example/'});assert.equal(result.url,ui.legacyGrant.url);
  await ui.invoke('copy',result.url);assert.equal(ui.writes.at(-1),result.url);
  await assert.rejects(ui.invoke('copy',result.url+'changed'),/地址不可用/);
  const issued=await ui.invoke('connect',{action:'pair'});await ui.invoke('copy',issued.url);assert.equal(ui.writes.at(-1),issued.url);
  await ui.invoke('copy','https://fixture.example/');assert.equal(ui.writes.at(-1),'https://fixture.example/');
  await assert.rejects(ui.invoke('copy',issued.url,{sender:ui.event.sender,senderFrame:{...ui.event.senderFrame}}),/不允许的界面来源/);
  assert.deepEqual(ui.persisted,[]);assert.equal(JSON.stringify(ui.actions).includes('one-use-secret'),false);
});
test('Main only registers URLs after QR succeeds and legacy failure revokes the generated grant',async()=>{
  const ui=await mainFixture();ui.failImage();
  await assert.rejects(ui.invoke('pairing',{action:'create',url:'https://fixture.example/'}),/QR fixture failure/);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.actions.at(-1))),{action:'pairing',payload:{action:'revoke',id:ui.legacyGrant.id}});
  await assert.rejects(ui.invoke('copy',ui.legacyGrant.url),/地址不可用/);
  await assert.rejects(ui.invoke('connect',{action:'pair'}),/QR fixture failure/);
  await assert.rejects(ui.invoke('copy',ui.connectGrant.url),/地址不可用/);
});
test('Main expires used legacy URL independently of Connect and clears all on gateway stop',async()=>{
  const ui=await mainFixture();await ui.invoke('pairing',{action:'create',url:'https://fixture.example/'});await ui.invoke('connect',{action:'pair'});
  ui.respond((action,payload)=>action==='snapshot'?{urls:[],runtime:{running:true}}:action==='pairing'?{states:{[ui.legacyGrant.id]:'used'}}:{});
  await ui.invoke('pairing',{action:'status',ids:[ui.legacyGrant.id]});
  await assert.rejects(ui.invoke('copy',ui.legacyGrant.url),/地址不可用/);await ui.invoke('copy',ui.connectGrant.url);
  await ui.invoke('stop');await assert.rejects(ui.invoke('copy',ui.connectGrant.url),/地址不可用/);
});
test('Main rejects Connect URL after claim, approval, disable, or data-directory switch',async()=>{
  for(const operation of ['claim','approve','disable','data']){
    const ui=await mainFixture();await ui.invoke('connect',{action:'pair'});
    if(operation==='claim'){
      ui.respond(action=>action==='connect'?{pairings:[{id:ui.connectGrant.pairingId,state:'claimed'}]}:{urls:[]});await ui.invoke('connect',{action:'pairings'});
    }else if(operation==='data')await ui.invoke('choose','data');
    else await ui.invoke('connect',{action:operation,id:ui.connectGrant.pairingId,approved:true});
    await assert.rejects(ui.invoke('copy',ui.connectGrant.url),/地址不可用/);
  }
});
test('Main copy waits for native clipboard completion and propagates native errors on both branches',async()=>{
  const ui=await mainFixture();await ui.invoke('connect',{action:'pair'});
  for(const url of [ui.connectGrant.url,'https://fixture.example/']){
    let release,settled=false;
    ui.clipboardWriter(()=>new Promise(resolve=>{release=resolve;}));
    const pending=ui.invoke('copy',url).then(()=>{settled=true;});
    await new Promise(setImmediate);assert.equal(settled,false);
    release();await pending;assert.equal(settled,true);
    ui.clipboardWriter(async()=>{throw Error('native clipboard unavailable');});
    await assert.rejects(ui.invoke('copy',url),/native clipboard unavailable/);
  }
});
