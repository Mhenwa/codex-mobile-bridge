'use strict';

class ListSync {
  constructor({root,status,refresh,visible,ready}) {
    Object.assign(this,{root,status,refresh,visible,ready});this.busy=false;this.frame=0;
    status.onclick=()=>this.update(true);
    const resume=()=>{if(!document.hidden)this.update();};
    document.addEventListener('visibilitychange',resume);window.addEventListener('focus',resume);
    root.addEventListener('touchstart',event=>{
      if(this.start)this.endPull(true);
      this.start=!this.busy&&!document.hidden&&visible()&&ready()&&root.scrollTop<=0&&event.touches.length===1?{x:event.touches[0].clientX,y:event.touches[0].clientY}:null;this.distance=0;
    },{passive:true});
    root.addEventListener('touchmove',event=>{
      if(!this.start)return;
      if(event.touches.length!==1){this.endPull(true);return;}
      const dx=event.touches[0].clientX-this.start.x,dy=event.touches[0].clientY-this.start.y;
      if(Math.abs(dx)>Math.max(10,Math.abs(dy))||dy<0){this.endPull(true);return;}
      this.distance=dy;
      if(dy>10)event.preventDefault();
      if(!this.frame)this.frame=requestAnimationFrame(()=>{
        this.frame=0;
        if(this.distance<=10){this.setPull(0);this.hide();return;}
        const pull=Math.min(64,this.distance*.5);
        this.setPull(pull,true);this.show(this.distance>=72?'松开刷新聊天列表':'下拉刷新聊天列表',pull);
      });
    },{passive:false});
    root.addEventListener('touchend',()=>this.endPull());
    root.addEventListener('touchcancel',()=>this.endPull(true));
    this.schedule();
  }
  setPull(distance,dragging=false){this.root.classList.toggle('list-pulling',dragging);this.root.style.setProperty('--list-pull',distance+'px');}
  show(text,pull=0){
    clearTimeout(this.hideTimer);clearTimeout(this.dismissTimer);
    const translated=BridgeI18n.t(text);if(this.status.textContent!==translated)this.status.textContent=translated;
    this.status.hidden=false;this.status.style.setProperty('--list-hint-y',Math.max(4,(pull-28)/2)+'px');this.status.classList.add('list-sync-visible');
  }
  hide(){
    clearTimeout(this.hideTimer);clearTimeout(this.dismissTimer);this.status.classList.remove('list-sync-visible');
    this.dismissTimer=setTimeout(()=>this.status.hidden=true,160);
  }
  endPull(cancel=false){
    if(!this.start)return;
    const refresh=!cancel&&this.distance>=72;this.start=null;this.distance=0;
    if(this.frame)cancelAnimationFrame(this.frame);this.frame=0;
    if(refresh)this.update(true);else{this.setPull(0);this.hide();}
  }
  schedule(){clearTimeout(this.timer);this.timer=setTimeout(async()=>{await this.update();this.schedule();},15000);}
  async update(explicit=false){
    if(this.start||this.busy)return;
    if(document.hidden||!this.visible()||!this.ready()||(!explicit&&this.root.scrollTop>24)){
      if(explicit){this.setPull(0);this.hide();}return;
    }
    this.busy=true;
    if(explicit){this.setPull(32);this.show('正在刷新聊天列表…',32);}
    try{await this.refresh();if(explicit){this.setPull(0);this.show('聊天列表已更新');this.hideTimer=setTimeout(()=>this.hide(),500);}}
    catch(e){this.setPull(0);this.show('列表暂未更新，点击重试');}
    finally{this.busy=false;}
  }
}
