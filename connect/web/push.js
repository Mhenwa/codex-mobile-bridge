'use strict';
// A notification navigation carries one inert marker to force a document
// navigation when an existing window is already displaying another chat hash.
// Erase it before the original app boots; the chat fragment stays untouched.
if(window.MhenwaConnect&&location.pathname==='/'&&location.search==='?connect_notification=1'){
  history.replaceState(null,'','/'+location.hash);
}
// This phone's Web Push subscription is independent of gateway notification
// channels. Never ask permission or create a subscription during page load.
window.MhenwaWebPush=(()=>{
  const words={
    title:['手机网页系统通知','System notifications on this phone'],
    note:['开启后，即使关闭网页或锁屏，也可接收待确认和任务完成提醒。电脑与网关需保持运行，手机需联网并允许系统通知。','Receive action required and task completion notifications after closing the page or locking your phone. Keep the computer and gateway running, and allow system notifications with network access on your phone.'],
    ios:['iPhone / iPad：需 iOS 16.4 或以上。在 Safari 点“分享 → 添加到主屏幕”，从主屏幕图标打开，再点击“开启通知”授权。','iPhone / iPad: requires iOS 16.4 or later. In Safari, use Share → Add to Home Screen, open the home screen icon, then tap Enable notifications to grant permission.'],
    requests:['待确认提醒','Action required'], completion:['任务完成提醒','Task completed'],
    enable:['开启通知','Enable notifications'], reenable:['重新开启通知','Enable notifications again'],
    disable:['关闭通知','Disable notifications'], test:['发送测试通知','Send test notification'],
    loading:['正在检查通知状态…','Checking notification status…'],
    off:['当前手机尚未开启系统通知。','System notifications are off on this phone.'],
    on:['当前手机已开启通知，关闭网页后仍可接收。','Notifications are enabled on this phone, including after closing this page.'],
    none:['已开启通知，两类提醒均已关闭。可勾选需要的提醒。','Notifications are enabled, but both event types are off. Select the reminders you want.'],
    unsupported:['此浏览器不支持 Web Push，请使用支持系统推送的浏览器。','This browser does not support Web Push. Use a browser that supports system push notifications.'],
    insecure:['系统通知需要 HTTPS 安全连接。请从 Connect 的 HTTPS 入口打开。','System notifications require a secure HTTPS connection. Open the Connect HTTPS address.'],
    denied:['通知权限已被拒绝。请在浏览器或系统设置中允许此网站通知，再重新开启。','Notification permission was denied. Allow notifications for this site in browser or system settings, then enable them again.'],
    dismissed:['尚未允许通知。需要时点击“开启通知”并允许授权。','Notifications were not allowed. Tap Enable notifications and grant permission when you are ready.'],
    unavailable:['连接服务尚未配置 Web Push，暂时无法开启。','The connection service has not configured Web Push yet. Notifications are unavailable.'],
    unsigned:['请先连接并授权这部手机，再开启通知。','Connect and authorize this phone before enabling notifications.'],
    working:['正在保存通知设置…','Saving notification settings…'],
    disabled:['当前手机通知已关闭。','Notifications are off on this phone.'],
    saved:['当前手机的提醒类型已保存。','Reminder preferences have been saved for this phone.'],
    queued:['测试通知已排队，请查看手机通知栏；排队不表示已经送达。','The test notification is queued. Check your phone notifications; queued does not mean delivered.'],
    failed:['通知操作失败，请重试。','The notification operation failed. Please try again.'],
    expired:['网页授权已过期，请重新连接后重试。','Your browser authorization expired. Reconnect and try again.'],
    browserOnly:['浏览器仍保留订阅，但这部手机的服务记录已关闭。点击“重新开启通知”可重新绑定。','The browser still has a subscription, but this phone is no longer enabled on the service. Tap Enable notifications again to reconnect it.'],
    missingBrowser:['服务记录仍存在，但浏览器订阅已失效。请重新开启通知。','The service still has a record, but this browser subscription is missing. Enable notifications again.']
  };
  let panel,ui,status=null,subscription=null,busy=false,loaded=false,message='loading',messageError=false;
  const text=key=>words[key][document.documentElement.lang.startsWith('en')?1:0];
  const ios=()=>/iPad|iPhone|iPod/.test(navigator.userAgent||'')||
    ((navigator.platform||'')==='MacIntel'&&navigator.maxTouchPoints>1);
  const standalone=()=>navigator.standalone===true||
    (typeof window.matchMedia==='function'&&window.matchMedia('(display-mode: standalone)').matches);
  const hasApis=()=>typeof Notification!=='undefined'&&typeof PushManager!=='undefined'&&
    !!navigator.serviceWorker;
  function block(){
    if(window.isSecureContext!==true)return 'insecure';
    if(ios()&&!standalone())return 'ios';
    if(!hasApis())return 'unsupported';
    return null;
  }
  const permission=()=>typeof Notification==='undefined'?'default':Notification.permission;
  function setMessage(key,error=false){message=key;messageError=error;render();}
  function render(){
    if(!ui)return;
    for(const key of ['title','note','ios','requests','completion','disable','test'])ui[key].textContent=text(key);
    ui.ios.hidden=!ios();
    const blocked=block(),active=!!(status&&status.subscribed&&subscription&&permission()==='granted');
    ui.enable.textContent=text(subscription||status?.subscribed?'reenable':'enable');
    ui.enable.hidden=active;
    ui.enable.disabled=busy||!loaded||!!blocked||!status?.available;
    ui.disable.hidden=!status?.subscribed&&!subscription;
    ui.disable.disabled=busy||!loaded;
    ui.test.disabled=busy||!active||!status?.available;
    ui.requestsInput.disabled=ui.completionInput.disabled=busy||!loaded;
    ui.status.textContent=text(message);
    ui.status.dataset.error=messageError?'true':'false';
    panel.setAttribute('aria-busy',busy?'true':'false');
  }
  function events(){return {requests:ui.requestsInput.checked,completion:ui.completionInput.checked};}
  function useEvents(value){
    ui.requestsInput.checked=value?.requests!==false;
    ui.completionInput.checked=value?.completion!==false;
  }
  async function request(path,body){
    const options={credentials:'same-origin',cache:'no-store',headers:{}};
    if(body!==undefined){
      // The CSRF token belongs to the relay phone session, not the gateway or
      // the selected chat host. Refresh it for each write; never store it.
      const me=await request('/connect/me');
      if(!me.authenticated||typeof me.csrf!=='string'||!me.csrf){const error=Error();error.kind='unsigned';throw error;}
      options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':me.csrf};
      options.body=JSON.stringify(body);
    }
    const response=await fetch(path,options);
    let value;
    try{value=await response.json();}catch{const error=Error();error.kind='failed';throw error;}
    if(!response.ok){const error=Error();error.kind=response.status===401?'expired':'failed';throw error;}
    return value;
  }
  function ownWorker(registration){
    if(!registration)return false;
    const worker=registration.active||registration.waiting||registration.installing;
    return !!worker&&new URL(worker.scriptURL,location.href).pathname==='/connect/push-sw.js';
  }
  async function browserSubscription(){
    if(!hasApis()||!navigator.serviceWorker.getRegistration)return null;
    const registration=await navigator.serviceWorker.getRegistration('/');
    return ownWorker(registration)?registration.pushManager.getSubscription():null;
  }
  function summary(){
    if(block())return block();
    if(permission()==='denied')return 'denied';
    if(permission()!=='granted'&&status?.subscribed&&subscription)return 'dismissed';
    if(!status?.available)return 'unavailable';
    if(status.subscribed&&!subscription)return 'missingBrowser';
    if(!status.subscribed&&subscription)return 'browserOnly';
    if(!status.subscribed)return 'off';
    return events().requests||events().completion?'on':'none';
  }
  async function refresh(){
    if(busy)return;busy=true;render();
    try{
      status=await request('/connect/push');useEvents(status.events);
      subscription=await browserSubscription();loaded=true;setMessage(summary());
    }catch(error){loaded=true;setMessage(error.kind||'failed',true);}
    finally{busy=false;render();}
  }
  function publicKey(value){
    if(typeof value!=='string'||!/^[A-Za-z0-9_-]+$/.test(value))throw Error();
    const decoded=atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4));
    const key=Uint8Array.from(decoded,character=>character.charCodeAt(0));
    if(key.length!==65||key[0]!==4)throw Error();
    return key;
  }
  function sameKey(existing,key){
    if(!existing.options?.applicationServerKey)return true;
    const current=new Uint8Array(existing.options.applicationServerKey);
    return current.length===key.length&&current.every((value,index)=>value===key[index]);
  }
  function enable(){
    if(busy||!loaded)return Promise.resolve();
    const reason=block();
    if(reason){setMessage(reason,true);return Promise.resolve();}
    if(!status?.available){setMessage('unavailable',true);return Promise.resolve();}
    if(permission()==='denied'){setMessage('denied',true);return Promise.resolve();}
    // requestPermission must run synchronously in this click stack. An earlier
    // await (even a CSRF fetch) loses the user gesture on iOS home screen apps.
    let grant;
    try{grant=permission()==='granted'?Promise.resolve('granted'):Notification.requestPermission();}
    catch{setMessage('failed',true);return Promise.resolve();}
    busy=true;setMessage('working');
    return (async()=>{
      try{
        const result=await grant;
        if(result!=='granted'){setMessage(result==='denied'?'denied':'dismissed',result==='denied');return;}
        const key=publicKey(status.publicKey);
        const registration=await navigator.serviceWorker.register('/connect/push-sw.js',{scope:'/',updateViaCache:'none'});
        // Wait for activation before subscribing. The root scope is allowed by
        // the relay's Service-Worker-Allowed header on this specific script.
        const ready=await navigator.serviceWorker.ready;
        if(ready.scope!==registration.scope||!ownWorker(ready))throw Error();
        let current=await ready.pushManager.getSubscription();
        if(current&&!sameKey(current,key)){await current.unsubscribe();current=null;}
        current=current||await ready.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
        // Reuse a browser subscription if only the service record was revoked.
        // The backend prevents an active endpoint from changing phone owners.
        await request('/connect/push/subscribe',{subscription:current.toJSON(),events:events()});
        subscription=current;status.subscribed=true;status.events=events();setMessage(summary());
      }catch(error){setMessage(error.kind||'failed',true);}
      finally{busy=false;render();}
    })();
  }
  async function disable(){
    if(busy)return;busy=true;setMessage('working');
    try{
      // Revoke the service first. If the browser API fails, no further pushes
      // can be delivered through this phone's authorization.
      await request('/connect/push/unsubscribe',{});status.subscribed=false;
      if(subscription)await subscription.unsubscribe();subscription=null;setMessage('disabled');
    }catch(error){setMessage(error.kind||'failed',true);}
    finally{busy=false;render();}
  }
  async function preferences(){
    if(busy||!loaded)return;
    if(!status?.subscribed){render();return;}
    busy=true;setMessage('working');
    try{await request('/connect/push/preferences',{events:events()});status.events=events();setMessage('saved');}
    catch(error){useEvents(status.events);setMessage(error.kind||'failed',true);}
    finally{busy=false;render();}
  }
  async function test(){
    if(busy||ui.test.disabled)return;busy=true;setMessage('working');
    try{await request('/connect/push/test',{});setMessage('queued');}
    catch(error){setMessage(error.kind||'failed',true);}
    finally{busy=false;render();}
  }
  function mount(){
    if(panel||!window.MhenwaConnect)return;
    const dialog=document.getElementById('appearance-dialog');if(!dialog)return;
    const node=(tag,id,className)=>{const value=document.createElement(tag);value.id=id;if(className)value.className=className;return value;};
    panel=node('fieldset','connect-web-push','appearance-section connect-web-push');
    ui={title:node('legend','connect-push-title'),note:node('p','connect-push-note','muted connect-push-note'),
      ios:node('p','connect-push-ios','muted connect-push-note'),status:node('p','connect-push-status','connect-push-status')};
    panel.append(ui.title,ui.note,ui.ios);
    for(const key of ['requests','completion']){
      const label=node('label','connect-push-'+key+'-label','display-option');
      ui[key]=node('span','connect-push-'+key+'-text');ui[key+'Input']=node('input','connect-push-'+key);
      ui[key+'Input'].type='checkbox';ui[key+'Input'].checked=true;ui[key+'Input'].onchange=preferences;
      label.append(ui[key],ui[key+'Input']);panel.append(label);
    }
    const actions=node('div','connect-push-actions','connect-push-actions');
    for(const key of ['enable','disable','test']){ui[key]=node('button','connect-push-'+key,key==='enable'?'primary':'plain');ui[key].type='button';actions.append(ui[key]);}
    ui.enable.onclick=enable;ui.disable.onclick=disable;ui.test.onclick=test;
    ui.status.setAttribute('role','status');ui.status.setAttribute('aria-live','polite');
    panel.append(actions,ui.status);
    dialog.insertBefore(panel,dialog.querySelector('.appearance-grid'));
    if(typeof MutationObserver!=='undefined')new MutationObserver(render).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)refresh();});
    render();refresh();
  }
  return {mount};
})();
