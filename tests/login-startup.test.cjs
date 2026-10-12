'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {createLoginStartup,restoreDevelopmentEnvironment}=require('../desktop/login-startup.cjs');
const temporary=()=>fs.mkdtempSync(path.join(__dirname,'../.tmp/login-startup-'));

test('macOS registration reports approval and distinguishes login from manual reopening',()=>{
  let settings={openAtLogin:false,status:'not-registered',wasOpenedAtLogin:true},written;
  const app={isPackaged:true,getLoginItemSettings:()=>settings,setLoginItemSettings:value=>{written=value;settings={...settings,status:value.openAtLogin?'requires-approval':'not-registered',openAtLogin:false};}};
  const startup=createLoginStartup(app,{platform:'darwin'});
  startup.set(true);assert.deepEqual(written,{openAtLogin:true});
  assert.equal(startup.status().requiresApproval,true);assert.equal(startup.status().enabled,false);
  assert.equal(startup.hidden([],true),true);assert.equal(startup.hidden([]),false);
  settings={...settings,openAtLogin:true,status:'enabled'};assert.equal(startup.status().enabled,true);
  startup.set(false);assert.equal(startup.status().requiresApproval,false);
});

test('Linux autostart uses a permanent AppImage path and disabling writes a user override',()=>{
  const root=temporary();
  try{
    const env={XDG_CONFIG_HOME:path.join(root,'config'),APPIMAGE:'/opt/Bridge Space/Bridge$100%.AppImage'};
    const startup=createLoginStartup({isPackaged:true},{platform:'linux',executable:'/tmp/.mount_bridge/bin/bridge',home:root,env});
    assert.equal(startup.status().enabled,false);startup.set(true);assert.equal(startup.status().enabled,true);
    const file=path.join(env.XDG_CONFIG_HOME,'autostart/codex-mobile-bridge.desktop'),source=fs.readFileSync(file,'utf8');
    assert.match(source,/Exec="\/opt\/Bridge Space\/Bridge\\\\\$100%%\.AppImage" --login-startup/);
    assert.doesNotMatch(source,/\.mount_/);assert.equal(startup.hidden(['--login-startup'],true),false);
    startup.set(false);assert.match(fs.readFileSync(file,'utf8'),/Hidden=true/);assert.equal(startup.status().enabled,false);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('Linux ignores relative XDG_CONFIG_HOME and rejects an unstable executable',()=>{
  const root=temporary();
  try{
    const options={platform:'linux',home:root,env:{XDG_CONFIG_HOME:'relative'},executable:'/usr/bin/codex-mobile-bridge'};
    const startup=createLoginStartup({isPackaged:true},options);startup.set(true);
    assert.ok(fs.existsSync(path.join(root,'.config/autostart/codex-mobile-bridge.desktop')));
    const unstable=createLoginStartup({isPackaged:true},{...options,executable:'/tmp/.mount_bridge/bridge'});
    assert.throws(()=>unstable.set(true),/固定目录/);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('Windows development login restores absolute data and interpreter paths',()=>{
  const root=temporary();let settings={launchItems:[]};
  try{
    const app={isPackaged:false,getLoginItemSettings:()=>settings,setLoginItemSettings:value=>{settings={launchItems:[{name:value.name,scope:'user',enabled:value.openAtLogin}]};}};
    createLoginStartup(app,{platform:'win32',root,cwd:'D:\\Project',env:{CMB_DATA_DIR:'.local/data',CMB_PYTHON:'.venv/python.exe'}}).set(true);
    const env={};restoreDevelopmentEnvironment(app,['--login-startup'],env,'win32',root);
    assert.equal(env.CMB_DATA_DIR,'D:\\Project\\.local\\data');assert.equal(env.CMB_PYTHON,'D:\\Project\\.venv\\python.exe');
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
