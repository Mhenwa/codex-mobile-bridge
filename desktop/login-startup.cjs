'use strict';
const path=require('node:path');
const fs=require('node:fs');
const os=require('node:os');
const developmentConfig=root=>path.join(root,'.local','desktop-login-startup.json');
const argument='--login-startup';
function restoreDevelopmentEnvironment(app,argv=process.argv,env=process.env,platform=process.platform,root=path.resolve(__dirname,'..')){
  if(platform!=='win32'||app.isPackaged||!argv.includes(argument))return;
  if(fs.existsSync(developmentConfig(root))){
    const saved=JSON.parse(fs.readFileSync(developmentConfig(root),'utf8'));
    for(const key of ['CMB_DATA_DIR','CMB_PYTHON'])if(typeof saved[key]==='string'&&saved[key])env[key]=saved[key];
  }
  env.PYTHONUTF8='1';
}
function createLoginStartup(app,{platform=process.platform,executable=process.execPath,root=path.resolve(__dirname,'..'),env=process.env,home=os.homedir(),cwd=process.cwd()}={}){
  const supported=platform==='win32'||(['darwin','linux'].includes(platform)&&app.isPackaged);
  // Electron development launches need the project entry and the same gateway environment.
  const args=app.isPackaged?[argument]:[root,argument];
  const options={path:executable,args,name:app.isPackaged?'Codex Mobile Bridge':'Codex Mobile Bridge Development'};
  const config=env.XDG_CONFIG_HOME&&path.isAbsolute(env.XDG_CONFIG_HOME)?env.XDG_CONFIG_HOME:path.join(home,'.config');
  const desktopFile=path.join(config,'autostart','codex-mobile-bridge.desktop');
  const linuxExecutable=env.APPIMAGE||executable;
  function linuxCommand(){
    if(!path.isAbsolute(linuxExecutable)||/[\r\n\0=]/.test(linuxExecutable)||(!env.APPIMAGE&&linuxExecutable.includes('/.mount_')))
      throw Error('请将应用安装到固定目录后设置登录自启。');
    // Desktop Entry string escaping is applied before Exec argument unquoting.
    const quoted='"'+linuxExecutable.replace(/["`$\\]/g,'\\$&').replace(/%/g,'%%')+'"';
    return quoted.replace(/\\/g,'\\\\')+' '+argument;
  }
  function status(){
    if(!supported)return {supported:false,enabled:false};
    if(platform==='darwin'){
      const settings=app.getLoginItemSettings();
      return {supported:true,enabled:!!settings.openAtLogin,requiresApproval:settings.status==='requires-approval'};
    }
    if(platform==='linux'){
      try{
        const entry=fs.readFileSync(desktopFile,'utf8').split('[Desktop Entry]')[1]?.split(/\n\[/)[0]||'';
        const values=Object.fromEntries(entry.split(/\r?\n/).filter(line=>line.includes('=')).map(line=>[line.slice(0,line.indexOf('=')).trim(),line.slice(line.indexOf('=')+1).trim()]));
        return {supported:true,enabled:values.Type==='Application'&&values.Hidden!=='true'&&values['X-GNOME-Autostart-enabled']!=='false'&&values.Exec===linuxCommand()};
      }catch(error){if(error.code==='ENOENT')return {supported:true,enabled:false};throw error;}
    }
    let settings=app.getLoginItemSettings(options);
    // Electron 44's Windows lookup misses quoted executable paths containing spaces.
    if(executable.includes(' ')&&!settings.launchItems?.some(item=>item.name===options.name)){
      settings=app.getLoginItemSettings({...options,path:'"'+executable+'"'});
    }
    // openAtLogin queries Electron's default entry name, even when set uses a custom name.
    const enabled=settings.launchItems?.some(item=>item.name===options.name&&item.scope==='user'&&item.enabled);
    return {supported:true,enabled:!!enabled};
  }
  function validate(enabled){
    if(typeof enabled!=='boolean')throw Error('登录自启开关格式错误');
    if(!supported)throw Error('请使用已安装的桌面应用设置登录自启。');
    if(platform==='linux')linuxCommand();
  }
  function set(enabled){
    validate(enabled);
    if(enabled&&!app.isPackaged){
      // Keep Python and data paths out of Windows Run's 260-character command limit.
      const paths=platform==='win32'?path.win32:path;
      const saved={};for(const key of ['CMB_DATA_DIR','CMB_PYTHON'])if(env[key]){
        // A bare interpreter name still resolves through PATH; relative paths must not depend on login cwd.
        saved[key]=key==='CMB_PYTHON'&&!/[\\/]/.test(env[key])?env[key]:paths.isAbsolute(env[key])?env[key]:paths.resolve(cwd,env[key]);
      }
      fs.mkdirSync(path.dirname(developmentConfig(root)),{recursive:true});
      fs.writeFileSync(developmentConfig(root),JSON.stringify(saved),{mode:0o600});
    }
    if(platform==='linux'){
      fs.mkdirSync(path.dirname(desktopFile),{recursive:true,mode:0o700});
      fs.writeFileSync(desktopFile,'[Desktop Entry]\nType=Application\nName=Codex Mobile Bridge\nExec='+linuxCommand()+'\nTerminal=false\nHidden='+!enabled+'\n',{mode:0o600});
    }else app.setLoginItemSettings(platform==='darwin'?{openAtLogin:enabled}:{...options,openAtLogin:enabled});
    const current=status();
    if(current.enabled!==enabled&&!(enabled&&current.requiresApproval))throw Error('登录自启设置保存失败，请重试。');
  }
  return {status,validate,set,hidden:(argv,initial=false)=>supported&&platform!=='linux'&&(argv.includes(argument)||(initial&&platform==='darwin'&&!!app.getLoginItemSettings().wasOpenedAtLogin))};
}
module.exports={createLoginStartup,restoreDevelopmentEnvironment};
