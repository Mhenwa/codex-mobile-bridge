'use strict';
// Real Chromium Service Worker and notification engine; synthetic provider only.
// Close the chat page and stop its worker before dispatching a CDP push. This
// cannot verify FCM/APNs transport or delivery to a physical phone.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function freePort(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port;}
async function until(read,label,timeout=20000){const end=Date.now()+timeout;let last;while(Date.now()<end){try{const value=await read();if(value)return value;}catch(error){last=error;}await delay(100);}throw Error(label+' timed out'+(last?': '+last.message:''));}
async function cdp(url){
  const socket=new WebSocket(url),pending=new Map(),handlers=new Set();let next=0;
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  socket.addEventListener('message',event=>{
    const value=JSON.parse(event.data),request=pending.get(value.id);
    if(request){pending.delete(value.id);clearTimeout(request.timer);value.error?request.reject(Error(request.method+': '+value.error.message)):request.resolve(value.result);}
    else for(const handler of handlers)handler(value);
  });
  function call(method,params={},sessionId){return new Promise((resolve,reject)=>{
    const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' timed out'));},20000);
    pending.set(id,{resolve,reject,timer,method});socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
  });}
  return {call,on:handler=>handlers.add(handler),close:()=>socket.close(),async evaluate(expression,session){
    const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},session);
    if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||'Evaluation failed');return result.result.value;
  }};
}

async function main(){
  const chrome=process.env.CMB_SMOKE_CHROME||'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const python=process.env.CMB_SMOKE_PYTHON||path.join(root,'.tmp/build-venv/Scripts/python.exe');
  const output=path.join(root,'output/playwright');await fs.mkdir(output,{recursive:true});
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const temporary=await fs.mkdtemp(path.join(root,'.tmp','web-push-smoke-'));
  const port=await freePort(),debugPort=await freePort(),controlToken=crypto.randomBytes(32).toString('hex');
  let fixture,browser,client,fixtureErrors='',browserErrors='',success=false;
  const processes=[];
  try{
    fixture=spawn(python,[path.join(__dirname,'smoke-web-push-fixture.py'),'--port',String(port),'--data-dir',path.join(temporary,'relay'),'--control-token',controlToken],
      {cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});processes.push(fixture);
    fixture.stderr.on('data',value=>fixtureErrors+=value);
    let ready='';fixture.stdout.on('data',value=>ready+=value);
    const config=await until(()=>{try{return JSON.parse(ready.split('\n')[0]);}catch{if(fixture.exitCode!==null)throw Error(fixtureErrors);}},'synthetic relay startup');
    async function fixtureRequest(action,method='GET'){
      const response=await fetch(config.origin+'/__fixture/'+action,{method,headers:{'X-Fixture-Token':controlToken}});
      assert.equal(response.ok,true);return response.json();
    }
    browser=spawn(chrome,['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-extensions',
      '--disable-sync','--disable-component-update','--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debugPort,
      '--user-data-dir='+path.join(temporary,'chrome'),'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});processes.push(browser);
    browser.stderr.on('data',value=>browserErrors+=value);
    const version=await until(async()=>{const response=await fetch('http://127.0.0.1:'+debugPort+'/json/version');return response.ok&&response.json();},'isolated Chromium startup');
    client=await cdp(version.webSocketDebuggerUrl);
    await client.call('Browser.setPermission',{permission:{name:'notifications'},setting:'granted',origin:config.origin});
    const observer=(await client.call('Target.createTarget',{url:'about:blank'})).targetId;
    const observerSession=(await client.call('Target.attachToTarget',{targetId:observer,flatten:true})).sessionId;
    const registrations=new Map(),versions=new Map();
    client.on(event=>{
      if(event.method==='ServiceWorker.workerRegistrationUpdated')for(const item of event.params.registrations)registrations.set(item.registrationId,item);
      if(event.method==='ServiceWorker.workerVersionUpdated')for(const item of event.params.versions)versions.set(item.versionId,item);
    });
    await client.call('ServiceWorker.enable',{},observerSession);
    const chat=(await client.call('Target.createTarget',{url:'about:blank'})).targetId;
    const chatSession=(await client.call('Target.attachToTarget',{targetId:chat,flatten:true})).sessionId;
    await client.call('Page.enable',{},chatSession);await client.call('Runtime.enable',{},chatSession);
    await client.call('Network.enable',{},chatSession);
    await client.call('Network.setCookie',{...config.cookie,url:config.origin+'/',httpOnly:true,sameSite:'Strict'},chatSession);
    await client.call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true},chatSession);
    // Only provider registration is stubbed. ServiceWorker registration,
    // activation, scope, auth/CSRF requests, PushEvent and notifications are real.
    const mock=`(()=>{const json=${JSON.stringify(config.subscription)};let saved=null;
      Object.defineProperty(PushManager.prototype,'getSubscription',{value:async()=>saved});
      Object.defineProperty(PushManager.prototype,'subscribe',{value:async options=>{saved={options,
        toJSON:()=>JSON.parse(JSON.stringify(json)),unsubscribe:async()=>{saved=null;return true;}};return saved;}});})();`;
    await client.call('Page.addScriptToEvaluateOnNewDocument',{source:mock},chatSession);
    await client.call('Page.navigate',{url:config.origin+'/'},chatSession);
    await until(()=>client.evaluate(`!!document.getElementById('connect-push-enable')&&!document.getElementById('connect-push-enable').disabled`,chatSession),'Web Push settings loaded');
    const sources=await client.evaluate(`Array.from(document.scripts).map(value=>new URL(value.src).pathname)`,chatSession);
    assert.ok(sources.includes('/connect/platform.js'));assert.ok(sources.includes('/connect/push.js'));
    assert.equal((await fixtureRequest('state')).status.subscribed,false);
    assert.equal(await client.evaluate(`navigator.serviceWorker.getRegistrations().then(values=>values.length)`,chatSession),0,'initial page does not register a worker');
    assert.equal(await client.evaluate(`fetch('/connect/push/preferences',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({events:{requests:false,completion:false}})}).then(response=>response.status)`,chatSession),403,'real relay rejects writes without phone CSRF');
    assert.equal(await client.evaluate(`document.getElementById('notification-requests').closest('fieldset').hidden&&getComputedStyle(document.getElementById('notify-button')).display==='none'`,chatSession),true,'old gateway notification controls stay hidden');
    await client.evaluate(`document.querySelector('[data-open-appearance]').click()`,chatSession);
    await client.evaluate(`document.getElementById('connect-push-enable').click()`,chatSession);
    await until(async()=>{const result=await fixtureRequest('state');return result.status.subscribed;},'UI subscribes through authenticated relay');
    await until(()=>client.evaluate(`!document.getElementById('connect-push-test').disabled`,chatSession),'test button enabled');
    await client.evaluate(`document.getElementById('connect-push-disable').click()`,chatSession);
    await until(async()=>!(await fixtureRequest('state')).status.subscribed,'UI revokes phone subscription');
    await until(()=>client.evaluate(`!document.getElementById('connect-push-enable').disabled`,chatSession),'enable button restored after revoke');
    assert.equal(await client.evaluate(`document.getElementById('connect-push-test').disabled`,chatSession),true);
    await client.evaluate(`document.getElementById('connect-push-enable').click()`,chatSession);
    await until(async()=>(await fixtureRequest('state')).status.subscribed,'UI resubscribes after revoke');
    await until(()=>client.evaluate(`!document.getElementById('connect-push-test').disabled`,chatSession),'test button restored after re-enable');
    await client.evaluate(`document.getElementById('connect-push-completion').click()`,chatSession);
    await until(async()=>!(await fixtureRequest('state')).status.events.completion,'per-phone preferences saved');
    await until(()=>client.evaluate(`!document.getElementById('connect-push-test').disabled`,chatSession),'preference write finished');
    await client.evaluate(`document.getElementById('connect-push-test').click()`,chatSession);
    await until(async()=>(await fixtureRequest('state')).pending===1,'test queued');
    await until(()=>client.evaluate(`document.getElementById('connect-push-status').textContent.includes('排队不表示已经送达')`,chatSession),'queued delivery wording');
    await client.evaluate(`document.getElementById('connect-web-push').scrollIntoView({block:'center'})`,chatSession);
    const layout=await client.evaluate(`(()=>{const panel=document.getElementById('connect-web-push'),dialog=document.getElementById('appearance-dialog');return {page:document.documentElement.scrollWidth,viewport:innerWidth,dialogScroll:dialog.scrollWidth,dialogWidth:dialog.clientWidth,panel:panel.getBoundingClientRect().toJSON(),text:panel.textContent};})()`,chatSession);
    assert.ok(layout.page<=layout.viewport,'mobile page has no horizontal overflow');
    assert.ok(layout.dialogScroll<=layout.dialogWidth+1,'mobile settings have no horizontal overflow');
    const screenshot=await client.call('Page.captureScreenshot',{format:'png'},chatSession);
    await fs.writeFile(path.join(output,'web-push-mobile-zh.png'),Buffer.from(screenshot.data,'base64'));
    await client.evaluate(`const select=document.getElementById('appearance-language');select.value='en';select.dispatchEvent(new Event('change',{bubbles:true}));`,chatSession);
    await until(()=>client.evaluate(`document.getElementById('connect-push-title').textContent==='System notifications on this phone'`,chatSession),'English notification settings');
    const english=await client.call('Page.captureScreenshot',{format:'png'},chatSession);
    await fs.writeFile(path.join(output,'web-push-mobile-en.png'),Buffer.from(english.data,'base64'));
    const rootRegistration=await until(()=>Array.from(registrations.values()).find(value=>value.scopeURL===config.origin+'/'),'root Service Worker registration');
    const active=await until(()=>Array.from(versions.values()).find(value=>value.registrationId===rootRegistration.registrationId&&value.status==='activated'),'Service Worker activation');
    const initialWorker=await until(async()=>(await client.call('Target.getTargets')).targetInfos.find(value=>value.type==='service_worker'&&value.url===config.origin+'/connect/push-sw.js'),'worker for notification chat navigation');
    const initialWorkerSession=(await client.call('Target.attachToTarget',{targetId:initialWorker.targetId,flatten:true})).sessionId;
    const chatA='11111111-1111-1111-1111-111111111111',chatB='22222222-2222-2222-2222-222222222222';
    await client.call('Page.navigate',{url:config.origin+'/?smoke-page=A#'+chatA+'~local'},chatSession);
    await until(()=>client.evaluate(`typeof currentId!=='undefined'&&currentId===${JSON.stringify(chatA)}&&state?.id===${JSON.stringify(chatA)}`,chatSession),'page boot opens real fixture chat A');
    await client.evaluate(`history.replaceState(null,'','/#'+${JSON.stringify(chatA)}+'~local');window.__notificationNavigationMarker='existing-chat-A'`,chatSession);
    const exactChat=config.origin+'/#'+chatB+'~local';
    await client.evaluate(`self.clients.matchAll({type:'window',includeUncontrolled:true}).then(values=>values.find(value=>new URL(value.url).origin===self.location.origin).navigate(notificationNavigationUrl(${JSON.stringify(exactChat)}))).then(value=>({url:value.url}))`,initialWorkerSession);
    await until(()=>client.evaluate(`window.__notificationNavigationMarker===undefined&&typeof currentId!=='undefined'&&currentId===${JSON.stringify(chatB)}&&state?.id===${JSON.stringify(chatB)}&&document.getElementById('chat-title').textContent.includes(${JSON.stringify(chatB)})&&currentHost==='local'&&location.href===${JSON.stringify(exactChat)}`,chatSession),'worker navigation reloads and renders exact fixture chat B without retaining the marker');
    await client.evaluate(`window.__notificationNavigationMarker='already-chat-B'`,chatSession);
    await client.evaluate(`self.clients.matchAll({type:'window',includeUncontrolled:true}).then(values=>values.find(value=>new URL(value.url).origin===self.location.origin).navigate(notificationNavigationUrl(${JSON.stringify(exactChat)}))).then(value=>({url:value.url}))`,initialWorkerSession);
    await until(()=>client.evaluate(`window.__notificationNavigationMarker===undefined&&currentId===${JSON.stringify(chatB)}&&state?.id===${JSON.stringify(chatB)}&&location.href===${JSON.stringify(exactChat)}`,chatSession),'repeated notification navigation reloads the current chat B');
    await client.call('Target.closeTarget',{targetId:chat});
    const targets=(await client.call('Target.getTargets')).targetInfos;
    assert.equal(targets.filter(value=>value.type==='page'&&value.url.startsWith(config.origin)).length,0,'no chat page remains open');
    await client.call('ServiceWorker.stopWorker',{versionId:active.versionId},observerSession);
    await until(()=>versions.get(active.versionId)?.runningStatus==='stopped','Service Worker stopped after chat closed');
    const delivered=await fixtureRequest('flush','POST');assert.equal(delivered.sent.length,1);assert.equal(delivered.pending,0);
    await client.call('ServiceWorker.deliverPushMessage',{origin:config.origin,registrationId:rootRegistration.registrationId,data:JSON.stringify(delivered.sent[0].payload)},observerSession);
    await until(()=>versions.get(active.versionId)?.runningStatus==='running','push restarts stopped Service Worker');
    const workerTarget=await until(async()=>(await client.call('Target.getTargets')).targetInfos.find(value=>value.type==='service_worker'&&value.url===config.origin+'/connect/push-sw.js'),'background worker target');
    const workerSession=(await client.call('Target.attachToTarget',{targetId:workerTarget.targetId,flatten:true})).sessionId;
    const notifications=await until(async()=>{const list=await client.evaluate(`self.registration.getNotifications().then(list=>list.map(value=>({title:value.title,body:value.body,tag:value.tag,url:value.data.url})))`,workerSession);return list.length&&list;},'system notification stored with no open chat');
    assert.equal(notifications[0].title,'Codex · 通知测试');assert.equal(notifications[0].url,config.origin+'/');
    const after=(await client.call('Target.getTargets')).targetInfos;
    assert.equal(after.filter(value=>value.type==='page'&&value.url.startsWith(config.origin)).length,0,'push did not require reopening a chat');
    const report={browser:version.Browser,checks:['no initial permission/subscription','real root Service Worker activation',
      'authenticated subscription; revoke and re-enable','missing CSRF write rejected','per-phone preferences; old gateway controls hidden',
      'queued wording','Chinese and English mobile settings without overflow','worker navigation reloads existing chat A and renders fixture chat B',
      'repeated navigation to the same chat B reloads correctly and removes the inert marker',
      'chat page closed and worker stopped','CDP push restarted worker','native getNotifications confirmed notification without a chat page'],
      provider:'Synthetic subscription and mock sender. FCM/APNs and physical phone delivery are not tested.',notifications};
    await fs.writeFile(path.join(output,'web-push-smoke.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));success=true;
  }catch(error){console.error(error.stack);if(fixtureErrors)console.error('fixture:',fixtureErrors);if(browserErrors)console.error('isolated Chromium:',browserErrors.slice(-3000));process.exitCode=1;}
  finally{
    if(client){try{await client.call('Browser.close');}catch{}client.close();}
    for(const child of processes)if(child.exitCode===null)child.kill();
    await delay(700);
    try{
      assert.equal(path.dirname(path.resolve(temporary)),path.resolve(root,'.tmp'),'cleanup must remain inside the smoke workspace');
      assert.ok(path.basename(temporary).startsWith('web-push-smoke-'),'cleanup only removes this isolated smoke profile');
      await fs.rm(temporary,{recursive:true,force:true,maxRetries:10,retryDelay:200});
    }catch(error){console.error('temporary cleanup:',error.message);process.exitCode=1;}
    if(!success)console.error('Web Push browser smoke did not complete.');
  }
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
