'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');

test('Android accepts Mhenwa Connect pairing links and leaves manual address empty',()=>{
  const url=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/GatewayURL.java'),'utf8');
  const activity=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java'),'utf8');
  assert.match(url,/\(\?:pair\|connect_pair\)=\[A-Za-z0-9_\-\]\{43\}/);
  assert.match(activity,/void manualAddress\(\).*?setText\(""\)/s);
  assert.doesNotMatch(activity,/void manualAddress\(\).*?setText\("https:\/\/"\)/s);
});

test('Android session bookkeeping accepts and clears both gateway cookie names',()=>{
  const source=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/EventClient.java'),'utf8');
  assert.match(source,/SESSION_COOKIE_NAMES=\{"mhenwa_connect_phone","codex_mobile_session"\}/);
  assert.match(source,/for\(String name:SESSION_COOKIE_NAMES\)store\.setCookie/);
  assert.match(source,/static String sessionCookie\(String header\)/);
});
