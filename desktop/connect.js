'use strict';
// This component only calls authorized local Electron IPC. The renderer never
// receives the API key or device token, and never makes its own network request.
(()=>{
  const api=window.bridgeDesktop,$=id=>document.getElementById(id);
  const panel=document.querySelector('[data-panel="network"]');
  if(!panel||!api?.connect)return;
  $('connect-actions').onsubmit=event=>event.preventDefault();
  let current=null,busy=false,polling=false,grantExpires=0;
  const text=(tag,value)=>{const node=document.createElement(tag);node.textContent=String(value??'');return node;};
  function note(message,error=false){$('connect-feedback').textContent=String(message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,'');$('connect-feedback').classList.toggle('error',error);}
  function renderStatus(value){
    current=value;
    $('connect-state').textContent=value.message;
    $('connect-device').textContent=value.registered?`${value.deviceName} · ${value.deviceId}`:'尚未注册电脑';
    $('connect-register').disabled=busy||value.enabled;
    $('connect-discover').disabled=busy||value.enabled;
    $('connect-pair').disabled=busy||value.state!=='online';
    $('connect-disable').disabled=busy||!value.enabled;
    $('connect-consent').disabled=value.enabled;
  }
  async function command(value){
    if(busy)return;
    busy=true;if(current)renderStatus(current);
    try{return await api.connect(value);}
    catch(error){note(error.message,true);throw error;}
    finally{busy=false;if(current)renderStatus(current);}
  }
  function actionButton(label,action){
    const button=text('button',label);button.type='button';
    button.onclick=()=>action().catch(()=>{});return button;
  }
  function pairings(rows){
    $('connect-pairings').replaceChildren();
    const pending=rows.filter(row=>row.state==='claimed'||row.state==='pending');
    if(!pending.length)$('connect-pairings').append(text('p','暂无待确认的手机配对'));
    for(const row of pending){
      const item=document.createElement('div');item.className='address';
      item.append(text('strong',row.phoneName||'手机'));
      item.append(actionButton('批准这台手机',async()=>{
        if(!window.confirm(`允许“${row.phoneName||'手机'}”查看聊天、发送任务、处理命令/文件确认？`))return;
        await command({action:'approve',id:row.id,approved:true});note('已批准配对');await refresh();
      }));
      item.append(actionButton('拒绝',async()=>{await command({action:'approve',id:row.id,approved:false});note('已拒绝配对');await refresh();}));
      $('connect-pairings').append(item);
    }
  }
  function phones(rows){
    $('connect-phones').replaceChildren();
    if(!rows.length)$('connect-phones').append(text('p','暂无已授权手机'));
    for(const row of rows){
      const item=document.createElement('div');item.className='address';item.append(text('strong',row.name||'手机'));
      item.append(actionButton('撤销此手机',async()=>{
        if(!window.confirm('立即撤销这台手机的远程控制权限？'))return;
        await command({action:'revoke-phone',id:row.id});note('手机授权已撤销');await refresh();
      }));$('connect-phones').append(item);
    }
  }
  async function refresh(){
    if(polling||busy)return;
    polling=true;
    try{
      renderStatus(await api.connect({action:'status'}));
      if(current.enabled){
        const [claims,authorized]=await Promise.all([api.connect({action:'pairings'}),api.connect({action:'phones'})]);
        pairings(claims.pairings||[]);phones(authorized.phones||[]);
      }else{pairings([]);phones([]);$('connect-qr').hidden=true;}
    }catch(error){note(error.message,true);}finally{polling=false;}
  }
  $('connect-discover').onclick=async()=>{
    try{
      const result=await command({action:'discover'});if(!result)return;
      $('connect-provider').replaceChildren();
      for(const row of result.providers||[]){const option=text('option',`${row.label||row.provider||row.id} · ${row.baseUrl||''}`);option.value=row.id;$('connect-provider').append(option);}
      note(result.providers?.length?'已检测当前生效的 Mhenwa 配置；密钥不会显示在界面':'未检测到当前生效的 Mhenwa 提供商，请先在 Codex 中配置');
    }catch{}
  };
  $('connect-register').onclick=async()=>{
    if(!$('connect-consent').checked){note('请先阅读并勾选明确同意',true);return;}
    if(!$('connect-provider').value){note('请先检测并选择当前提供商',true);return;}
    try{
      const value=await command({action:'register',consent:true,provider:$('connect-provider').value,deviceName:$('connect-name').value});
      if(value){renderStatus(value);note('已注册。请在“连接与状态”停止后重新启动网关，或直接启动尚未运行的网关。');}
    }catch{}
  };
  $('connect-pair').onclick=async()=>{
    try{
      const grant=await command({action:'pair'});if(!grant)return;
      $('connect-qr-image').src=grant.image;$('connect-qr').hidden=false;grantExpires=Number(grant.expires)||0;
      note('请用手机扫描一次性二维码，然后在本机批准配对。二维码不含模型 Key 或设备令牌。');
    }catch{}
  };
  $('connect-disable').onclick=async()=>{
    if(!window.confirm('关闭这台电脑的远程连接，并撤销关联手机？'))return;
    try{const value=await command({action:'disable'});if(value){renderStatus(value);$('connect-qr').hidden=true;note(value.message);await refresh();}}catch{}
  };
  $('connect-refresh').onclick=()=>refresh();
  const observer=new MutationObserver(()=>{if(!panel.hidden)refresh();});observer.observe(panel,{attributes:true,attributeFilter:['hidden']});
  setInterval(()=>{
    if(grantExpires&&Date.now()/1000>=grantExpires){$('connect-qr').hidden=true;$('connect-qr-image').removeAttribute('src');grantExpires=0;}
    if(!panel.hidden&&!document.hidden)refresh();
  },3000);
})();
