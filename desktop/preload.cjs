'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('bridgeDesktop',{
  checkUpdate:()=>ipcRenderer.invoke('bridge:check-update'),
  installUpdate:()=>ipcRenderer.invoke('bridge:install-update'),
  language:()=>ipcRenderer.invoke('bridge:language'),
  setLanguage:value=>ipcRenderer.invoke('bridge:set-language',value),
  notificationWatches:value=>ipcRenderer.invoke('bridge:notification-watches',value),
  devices:value=>ipcRenderer.invoke('bridge:devices',value),
  pairing:value=>ipcRenderer.invoke('bridge:pairing',value),
  connect:value=>ipcRenderer.invoke('bridge:connect',value),
  installCloudflared:()=>ipcRenderer.invoke('bridge:install-cloudflared'),
  checkCloudflared:value=>ipcRenderer.invoke('bridge:check-cloudflared',value),
  snapshot:()=>ipcRenderer.invoke('bridge:snapshot'),
  accounts:value=>ipcRenderer.invoke('bridge:accounts',value),
  account:value=>ipcRenderer.invoke('bridge:account',value),
  save:value=>ipcRenderer.invoke('bridge:save',value),
  start:()=>ipcRenderer.invoke('bridge:start'),
  stop:()=>ipcRenderer.invoke('bridge:stop'),
  logs:()=>ipcRenderer.invoke('bridge:logs'),
  testNotification:value=>ipcRenderer.invoke('bridge:test-notification',value),
  exportDeployment:value=>ipcRenderer.invoke('bridge:export-deployment',value),
  copyDeployment:value=>ipcRenderer.invoke('bridge:copy-deployment',value),
  checkEntry:value=>ipcRenderer.invoke('bridge:check-entry',value),
  choose:kind=>ipcRenderer.invoke('bridge:choose',kind),
  open:target=>ipcRenderer.invoke('bridge:open',target),
  copy:target=>ipcRenderer.invoke('bridge:copy',target)
});
