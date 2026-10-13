'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const read = value => fs.readFileSync(path.join(root, value), 'utf8');

test('Mhenwa APK variants have isolated application and downloaded-file identities', () => {
  for (const file of ['mobile/android/AndroidManifest.xml', 'mobile/android-push/AndroidManifest.xml']) {
    const manifest = read(file);
    assert.equal(manifest.match(/<manifest[^>]*\bpackage="([^"]+)"/)[1], 'cc.mhenwa.codexbridge');
    assert.equal(manifest.match(/<application[^>]*android:label="([^"]+)"/)[1], 'Mhenwa Bridge');
    const authority = manifest.match(/<provider[^>]*android:authorities="([^"]+)"/)[1];
    assert.equal(authority.replace('${applicationId}', 'cc.mhenwa.codexbridge'), 'cc.mhenwa.codexbridge.downloads');
    const externalSchemes = [...manifest.matchAll(/<data android:scheme="([^"]+)" android:host="open"/g)].map(match => match[1]);
    assert.deepEqual(externalSchemes, ['mhenwabridge', 'codexbridge']);
  }
  assert.match(read('mobile/android-push/build.gradle'), /applicationId 'cc\.mhenwa\.codexbridge'/);
});

test('legacy and Mhenwa notification links still require a saved computer before navigation', () => {
  const activity = read('mobile/android/src/io/github/try2love/codexbridge/MainActivity.java');
  const handler = activity.slice(activity.indexOf('boolean openIntent('), activity.indexOf('void inbox('));
  assert.match(handler, /!"mhenwabridge"\.equals\(link\.getScheme\(\)\)&&!"codexbridge"\.equals\(link\.getScheme\(\)\)/);
  assert.ok(handler.indexOf('if(!saved().contains(base))') >= 0);
  assert.ok(handler.indexOf('if(!saved().contains(base))') < handler.indexOf('connect(thread'));
});
