'use strict';
// Capture the one-use pairing secret and erase it BEFORE any network request.
(async()=>{
  const $=id=>document.getElementById(id);
  const prefix='#connect_pair=';
  let token=location.hash.startsWith(prefix)?location.hash.slice(prefix.length):null;
  if(token!==null)history.replaceState(null,'',location.pathname+location.search);
  let claimToken=null, stopped=false;
  const fail=message=>{$('error').hidden=false;$('error').textContent=message;};
  async function request(path,body){
    const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const value=await response.json();
    if(!response.ok)throw Error(value.error||'连接失败，请重新扫码。');
    return value;
  }
  async function poll(){
    if(stopped)return;
    try{
      const value=await request('/connect/pair/status',{claimToken});
      if(value.state==='approved'){claimToken=null;stopped=true;$('status').textContent='电脑已确认，正在进入…';location.replace('/');return;}
      if(['expired','rejected','denied','revoked','used','consumed'].includes(value.state)){stopped=true;claimToken=null;fail('配对已拒绝或过期，请在电脑上重新生成二维码。');return;}
      $('status').textContent='请求已发送。请在电脑客户端核对手机名称并确认；等待期间不要重复扫码。';
      setTimeout(poll,1800);
    }catch(error){stopped=true;claimToken=null;fail(error.message);}
  }
  $('claim-form').onsubmit=async event=>{
    event.preventDefault();$('claim').disabled=true;$('error').hidden=true;
    try{const value=await request('/connect/pair/claim',{token,phoneName:$('phone-name').value});token=null;claimToken=value.claimToken;$('claim-form').hidden=true;await poll();}
    catch(error){token=null;stopped=true;fail(error.message);}
  };
  if(token!==null){$('heading').textContent='请求连接这台电脑';$('claim-form').hidden=false;$('status').textContent='提交后需要在电脑上确认。二维码一次有效，不包含 API Key。';return;}
  try{const value=await request('/connect/me');if(value.authenticated){$('status').textContent=value.online?'这台电脑已在线。':'这台电脑暂时离线，请保持电脑唤醒并启动客户端。';$('enter').hidden=false;}}
  catch(error){fail(error.message);}
})();
