'use strict';
// Notification delivery only. Deliberately no fetch/cache handlers: chats,
// credentials and API responses must not be retained for offline use.
function notificationUrl(raw){
  if(typeof raw!=='string')return self.location.origin+'/';
  try{
    const target=new URL(raw,self.location.origin+'/');
    if(target.origin!==self.location.origin||target.username||target.password||target.pathname!=='/'||target.search)return self.location.origin+'/';
    if(!target.hash)return target.href;
    const match=target.hash.match(/^#([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:~([^#]+))?$/i);
    if(!match)return self.location.origin+'/';
    if(match[2]){
      const host=decodeURIComponent(match[2]);
      if(!host||host.length>256||/[\s\p{C}\p{Z}|#~\/]/u.test(host))return self.location.origin+'/';
      // Accept only the canonical percent-encoded host form produced by the
      // gateway. It is an identifier, never a URL to navigate independently.
      const encoded=encodeURIComponent(host).replace(/[!'()*]/g,character=>'%'+character.charCodeAt(0).toString(16).toUpperCase());
      if(encoded!==match[2])return self.location.origin+'/';
    }
    return target.href;
  }catch{return self.location.origin+'/';}
}
function notificationNavigationUrl(raw){
  const target=new URL(notificationUrl(raw));
  // navigate('/#chatB') from '/#chatA' can be a same-document navigation.
  // A fixed, secret-free query forces a new document, whose startup reads B.
  // The page removes this exact marker before its original UI scripts boot.
  target.search='?connect_notification=1';return target.href;
}
self.addEventListener('push',event=>{
  event.waitUntil((async()=>{
    let value={};try{if(event.data)value=event.data.json();}catch{}
    if(!value||typeof value!=='object'||Array.isArray(value))value={};
    const safeText=(candidate,fallback,max)=>typeof candidate==='string'&&candidate.trim()?candidate.slice(0,max):fallback;
    const title=safeText(value.title,'Codex 提醒',120);
    const body=safeText(value.body,'电脑上的 Codex 有新的待确认或任务完成消息。',300);
    const tag=safeText(value.tag,'codex-connect',200);
    await self.registration.showNotification(title,{body,tag,icon:'/icon.png',badge:'/icon.png',
      data:{url:notificationUrl(value.url)}});
  })());
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const url=notificationUrl(event.notification.data?.url);
  event.waitUntil((async()=>{
    const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    for(const client of windows){
      const current=new URL(client.url);
      if(current.origin!==self.location.origin||current.pathname!=='/'||typeof client.navigate!=='function')continue;
      // Always navigate to the notification's exact chat before focusing.
      // Focusing an existing chat alone would open the wrong conversation.
      try{const navigated=await client.navigate(notificationNavigationUrl(url));if(navigated){await navigated.focus();return;}}catch{}
    }
    await self.clients.openWindow(url);
  })());
});
