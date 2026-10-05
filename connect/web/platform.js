'use strict';
// Relay injects this before the original UI scripts. Personal mode does not load it.
window.MhenwaConnect=true;
// An already authorized browser scanning a new QR must enter the pairing flow,
// never reinterpret the secret as a conversation fragment in the original UI.
if(location.hash.startsWith('#connect_pair='))location.replace('/connect/'+location.hash);
document.addEventListener('DOMContentLoaded',()=>{
  for(const id of ['accounts-button','account-button','pushplus-settings',
                   'settings-accounts','settings-pushplus','appearance-accounts','appearance-pushplus',
                   'notification-requests','notification-completions']){
    const node=document.getElementById(id);
    if(!node)continue;
    const container=node.closest('label');
    (container||node).hidden=true;
    node.disabled=true;
  }
});
