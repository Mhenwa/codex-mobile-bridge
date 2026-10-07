'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { packages, validateSource, prepare, verifyUploaded } = require('./prepare-release.cjs');

const run = {
  id: 37623516766, repository: { full_name: 'Mhenwa/codex-mobile-bridge' },
  head_repository: { full_name: 'Mhenwa/codex-mobile-bridge' }, head_branch: 'main',
  path: '.github/workflows/desktop.yml', event: 'push', status: 'completed',
  conclusion: 'success', head_sha: 'cf8b5a61596b19be74b8a71856d2aac10dd3aa54',
};
const jobs = { jobs: Array.from({ length: 5 }, () => ({ status: 'completed', conclusion: 'success' })) };

test('publication refuses foreign, failed, partial and untrusted source builds', () => {
  assert.equal(validateSource(run, jobs, run.repository.full_name), run.head_sha);
  for (const change of [
    { head_repository: { full_name: 'other/fork' } }, { head_branch: 'feature/test' },
    { conclusion: 'failure' }, { status: 'in_progress' }, { event: 'pull_request' },
    { path: '.github/workflows/tests.yml' }, { head_sha: 'main' },
  ]) assert.throws(() => validateSource({ ...run, ...change }, jobs, run.repository.full_name));
  assert.throws(() => validateSource(run, { jobs: jobs.jobs.slice(1) }, run.repository.full_name));
  assert.throws(() => validateSource(run, { jobs: [...jobs.jobs.slice(1), { status: 'completed', conclusion: 'skipped' }] }, run.repository.full_name));
});

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'cmb-release-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const name of packages) fs.writeFileSync(path.join(directory, `Codex-Mobile-Bridge-1.4.0-${name}`), `fixture ${name}`);
  return directory;
}

test('complete platform packages produce identical fixed aliases and checksums', async t => {
  const directory = fixture(t);
  const assets = await prepare(directory, '1.4.0');
  assert.equal(assets.length, 21);
  const sums = fs.readFileSync(path.join(directory, 'SHA256SUMS.txt'), 'utf8');
  assert.equal(sums.trim().split('\n').length, 20);
  for (const suffix of packages) {
    const original = assets.find(asset => asset.name === `Codex-Mobile-Bridge-1.4.0-${suffix}`);
    const alias = assets.find(asset => asset.name === `Codex-Mobile-Bridge-${suffix}`);
    assert.equal(original.sha256, alias.sha256);
    assert.equal(original.size, alias.size);
    assert.deepEqual(fs.readFileSync(path.join(directory, original.name)), fs.readFileSync(path.join(directory, alias.name)));
  }
  const release = {
    id: 123, tag_name: 'v1.4.0', target_commitish: run.head_sha, draft: true, prerelease: false,
    assets: assets.map(asset => ({ ...asset, state: 'uploaded', digest: `sha256:${asset.sha256}` })),
  };
  const plan = { tag: 'v1.4.0', commit: run.head_sha, assets };
  verifyUploaded(plan, release);
  assert.throws(() => verifyUploaded(plan, { ...release, assets: release.assets.slice(1) }));
  assert.throws(() => verifyUploaded(plan, { ...release, assets: release.assets.map((asset, index) => index ? asset : { ...asset, digest: 'sha256:bad' }) }));
  assert.throws(() => verifyUploaded(plan, { ...release, draft: false }));
  assert.throws(() => verifyUploaded(plan, { ...release, target_commitish: 'main' }));
});

test('incomplete or unexpected packages cannot be published', async t => {
  const directory = fixture(t);
  fs.unlinkSync(path.join(directory, 'Codex-Mobile-Bridge-1.4.0-Linux-arm64.deb'));
  await assert.rejects(prepare(directory, '1.4.0'));
  fs.writeFileSync(path.join(directory, 'Codex-Mobile-Bridge-1.4.0-Linux-arm64.deb'), 'fixture');
  fs.writeFileSync(path.join(directory, 'bridge-update.json'), '{}');
  await assert.rejects(prepare(directory, '1.4.0'));
  fs.unlinkSync(path.join(directory, 'bridge-update.json'));
  fs.writeFileSync(path.join(directory, 'Codex-Mobile-Bridge-1.4.0-Windows-x64.zip'), '');
  await assert.rejects(prepare(directory, '1.4.0'));
  await assert.rejects(prepare(directory, '../1.4.0'));
});

test('publication CLI uses the source version and repository release notes', t => {
  const directory = fixture(t);
  const metadata = fs.mkdtempSync(path.join(os.tmpdir(), 'cmb-release-metadata-'));
  t.after(() => fs.rmSync(metadata, { recursive: true, force: true }));
  const packageFile = path.join(metadata, 'package.json');
  const sourceFile = path.join(metadata, 'source.json');
  const planFile = path.join(metadata, 'plan.json');
  const bodyFile = path.join(metadata, 'body.md');
  fs.writeFileSync(packageFile, JSON.stringify({ version: '1.4.0' }));
  fs.writeFileSync(sourceFile, JSON.stringify({ ...run, run_number: 12, html_url: 'https://github.com/Mhenwa/codex-mobile-bridge/actions/runs/37623516766' }));
  const result = spawnSync(process.execPath, [path.join(__dirname, 'prepare-release.cjs'), 'prepare', directory, packageFile,
    path.join(__dirname, '../../docs/releases/v1.4.0.md'), sourceFile, planFile, bodyFile], {
    encoding: 'utf8', env: { ...process.env, GITHUB_OUTPUT: path.join(metadata, 'output.txt') },
  });
  assert.equal(result.status, 0, result.stderr);
  const plan = JSON.parse(fs.readFileSync(planFile, 'utf8'));
  assert.equal(plan.tag, 'v1.4.0');
  assert.equal(plan.commit, run.head_sha);
  assert.equal(plan.assets.length, 21);
  assert.match(fs.readFileSync(bodyFile, 'utf8'), /37623516766/);
  assert.equal(fs.readFileSync(path.join(metadata, 'output.txt'), 'utf8'), 'tag=v1.4.0\n');
});
