'use strict';
// A short-lived, exact-match capability cache for links issued by the local
// backend. Neither bearer URLs nor secrets are persisted or included in errors.
function createPairingLinks({clock=()=>Date.now()/1000,limit=256}={}){
  const issued=new Map();
  function prune(){for(const [url,grant] of issued)if(grant.expires<=clock())issued.delete(url);}
  function forget(scope,id){
    for(const [url,grant] of issued)if(grant.scope===scope&&(id===undefined||grant.id===id))issued.delete(url);
  }
  function remember(scope,grant){
    prune();
    const id=scope==='connect'?grant?.pairingId:grant?.id,url=grant?.url,expires=grant?.expires;
    let parsed;try{parsed=new URL(url);}catch{throw Error('配对链接无效或已过期');}
    if(!['legacy','connect'].includes(scope)||typeof id!=='string'||!id||typeof url!=='string'||
       !['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password||
       typeof expires!=='number'||!Number.isFinite(expires)||expires<=clock())throw Error('配对链接无效或已过期');
    // Reissuing an ID must not leave an earlier URL authorized.
    forget(scope,id);
    while(issued.size>=limit)issued.delete(issued.keys().next().value);
    issued.set(url,{scope,id,expires});
  }
  function has(url){prune();return typeof url==='string'&&issued.has(url);}
  function legacyStatus(ids,states){
    for(const id of ids||[])if(states?.[id]!=='active')forget('legacy',id);
    prune();
  }
  function connectPairings(rows){
    // The relay omits unclaimed offers. Only explicit non-offered states can
    // retire a URL here; absence from this list is not a revocation.
    for(const row of rows||[])if(typeof row?.id==='string'&&row.state!=='offered')forget('connect',row.id);
    prune();
  }
  return {remember,has,forget,legacyStatus,connectPairings,clear:()=>issued.clear()};
}
module.exports={createPairingLinks};
