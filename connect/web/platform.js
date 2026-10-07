'use strict';
// Relay injects this before the original UI scripts. Personal mode does not load it.
window.MhenwaConnect=true;
// An already authorized browser scanning a new QR must enter the pairing flow,
// never reinterpret the secret as a conversation fragment in the original UI.
if(location.hash.startsWith('#connect_pair='))location.replace('/connect/'+location.hash);
document.addEventListener('DOMContentLoaded',()=>{
  for(const id of ['accounts-button','account-button','pushplus-settings',
                   'settings-accounts','settings-pushplus','appearance-accounts','appearance-pushplus',
                   'notification-requests','notification-completions','notify-button','notify-dialog']){
    const node=document.getElementById(id);
    if(!node)continue;
    const container=node.closest('label');
    (container||node).hidden=true;
    node.disabled=true;
  }
  // Connect notifications belong to this phone. The old gateway-wide settings
  // would change other users' delivery channels and cannot configure Web Push.
  const legacy=document.getElementById('notification-requests');
  const section=legacy&&legacy.closest('fieldset');
  if(section)section.hidden=true;
  if(window.MhenwaWebPush)window.MhenwaWebPush.mount();
});
