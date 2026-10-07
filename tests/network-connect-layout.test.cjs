'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');

function parse(source){
  const root={tag:'#root',attrs:{},children:[],parent:null};let current=root;
  const tokens=source.match(/<!--[\s\S]*?-->|<![^>]*>|<\/?[^>]+>|[^<]+/g)||[];
  for(const token of tokens){
    if(token.startsWith('<!--')||token.startsWith('<!'))continue;
    if(!token.startsWith('<')){current.children.push({tag:'#text',attrs:{},value:token,parent:current});continue;}
    if(token.startsWith('</')){const name=token.match(/^<\/\s*([\w-]+)/)?.[1]?.toLowerCase();if(name){let node=current;while(node!==root&&node.tag!==name)node=node.parent;if(node!==root)current=node.parent;}continue;}
    const m=token.match(/^<\s*([\w-]+)/);if(!m)continue;const attrs={};
    const rest=token.slice(m[0].length,-1);for(const a of rest.matchAll(/([:\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))attrs[a[1]]=a[2]??a[3]??a[4]??'';
    const node={tag:m[1].toLowerCase(),attrs,children:[],parent:current};current.children.push(node);
    if(!/^(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)$/i.test(node.tag)&&!token.endsWith('/>'))current=node;
  }
  return root;
}
function all(node,p=[]){for(const child of node.children){p.push(child);if(child.children)all(child,p);}return p;}
function text(node){return node.children.map(child=>child.tag==='#text'?child.value:text(child)).join('');}
function load(){return fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');}

test('network tab owns Mhenwa Connect and keeps legacy settings in a collapsed advanced block',()=>{
  const html=load(),root=parse(html),nodes=all(root),tabs=nodes.filter(n=>n.tag==='button'&&n.attrs['data-tab']);
  assert.equal(tabs.filter(n=>n.attrs['data-tab']==='network').length,1);
  assert.equal(tabs.filter(n=>n.attrs['data-tab']==='connect').length,0);
  const network=nodes.find(n=>n.attrs['data-panel']==='network');assert.ok(network);
  assert.equal(nodes.filter(n=>n.attrs['data-panel']==='network').length,1);
  assert.equal(nodes.filter(n=>n.attrs['data-panel']==='connect').length,0);
  const connect=nodes.find(n=>n.attrs.id==='connect-content');assert.ok(connect);
  assert.equal(connect.parent,network);
  assert.match(text(connect),/Mhenwa Connect/);
  const advanced=nodes.find(n=>n.attrs.id==='network-advanced');assert.ok(advanced);
  assert.equal(advanced.parent,network);assert.equal(advanced.attrs.open,undefined);
  assert.equal(network.children.filter(n=>n.tag!=='#text').at(-1),advanced);
  for(const id of ['lan','lan-scope','lan-addresses','local-access','port','network-lock','connections','connection-kind','add-connection','origins','auth-mode','username','session-hours','password']){
    const matches=nodes.filter(n=>n.attrs.id===id);assert.equal(matches.length,1,id+' unique');let parent=matches[0];while(parent&&parent!==advanced)parent=parent.parent;assert.equal(parent,advanced,id+' in advanced');
  }
  for(const id of ['connect-state','connect-device','connect-discover','connect-provider','connect-name','connect-register','connect-pair','connect-pairings','connect-phones']){
    const node=nodes.find(n=>n.attrs.id===id);assert.ok(node,id+' present');let parent=node;while(parent&&parent!==connect)parent=parent.parent;assert.equal(parent,connect,id+' in Connect');
  }
  for(const id of ['connect-refresh','connect-disable','connect-discover','connect-register','connect-pair']){
    const node=nodes.find(n=>n.attrs.id===id);assert.equal(node.attrs.type,'button',id+' explicit button type');
  }
  assert.doesNotMatch(html,/connect-consent/);
  for(const id of ['connect-provider','connect-name'])assert.equal(nodes.find(n=>n.attrs.id===id).attrs.form,'connect-actions',id+' isolated form owner');
});

test('renderer and Connect controller bind to merged network panel and isolate registration controls',()=>{
  const renderer=fs.readFileSync(path.join(__dirname,'../desktop/renderer.js'),'utf8');
  const connect=fs.readFileSync(path.join(__dirname,'../desktop/connect.js'),'utf8');
  assert.match(renderer,/!node\.closest\('\#connect-content'\)/);
  assert.match(renderer,/if\(name==='connect'\)name='network'/);
  assert.match(connect,/querySelector\('\[data-panel="network"\]'\)/);
  assert.match(connect,/\$\('connect-actions'\)\.onsubmit/);
});

test('advanced settings text remains complete and migration guidance points to the collapsed section',()=>{
  const html=load(),i18n=fs.readFileSync(path.join(__dirname,'../web/i18n.js'),'utf8');
  for(const phrase of ['连接方式','登录验证','额外 HTTPS 地址（高级）','在“网络与登录”底部展开“高级选项”'])assert.match(html,new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  assert.match(i18n,/"高级选项":"Advanced options"/);
});

test('computer registration button has a scoped gap after the computer-name field',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../desktop/style.css'),'utf8');
  assert.match(css,/#connect-register\s*\{[^}]*margin-top\s*:\s*16px(?:\s*;|\s*\})/);
});
