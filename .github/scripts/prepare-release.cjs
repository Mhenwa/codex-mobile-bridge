'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

const packages = [
  'Windows-x64-Setup.exe', 'Windows-x64.zip',
  'macOS-arm64.dmg', 'macOS-arm64.zip', 'macOS-x64.dmg', 'macOS-x64.zip',
  'Linux-amd64.deb', 'Linux-x86_64.AppImage', 'Linux-arm64.deb', 'Linux-arm64.AppImage',
];

function validateSource(run, jobs, repository) {
  if (run.repository?.full_name !== repository || run.head_repository?.full_name !== repository ||
      run.head_branch !== 'main' || run.path !== '.github/workflows/desktop.yml' ||
      !['push', 'workflow_dispatch'].includes(run.event) ||
      run.status !== 'completed' || run.conclusion !== 'success' ||
      !/^[a-f0-9]{40}$/.test(run.head_sha) || !Number.isSafeInteger(run.id)) {
    throw Error('Release source must be a successful Desktop builds run from this repository main branch.');
  }
  if (jobs.jobs?.length !== 5 || jobs.jobs.some(job => job.status !== 'completed' || job.conclusion !== 'success')) {
    throw Error('All five platform jobs must have passed before publishing.');
  }
  return run.head_sha;
}

function validateVersion(version) {
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) {
    throw Error('This workflow publishes stable semantic versions only.');
  }
  return version;
}

async function sha256(file) {
  const hash = createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

async function prepare(directory, version) {
  validateVersion(version);
  const prefix = `Codex-Mobile-Bridge-${version}-`;
  const expected = packages.map(suffix => prefix + suffix).sort();
  const actual = fs.readdirSync(directory).sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw Error('Release must contain exactly the ten Windows, macOS and Linux packages for the source version.');
  }
  for (const name of expected) {
    const stat = fs.lstatSync(path.join(directory, name));
    if (!stat.isFile() || !stat.size) throw Error(`Missing or empty release package: ${name}`);
  }
  const assets = [];
  for (const name of expected) {
    const original = path.join(directory, name);
    const alias = name.replace(prefix, 'Codex-Mobile-Bridge-');
    const digest = await sha256(original);
    await fs.promises.copyFile(original, path.join(directory, alias));
    if (await sha256(path.join(directory, alias)) !== digest) throw Error(`Alias checksum mismatch: ${alias}`);
    const size = fs.statSync(original).size;
    assets.push({ name, size, sha256: digest }, { name: alias, size, sha256: digest });
  }
  assets.sort((a, b) => a.name.localeCompare(b.name, 'en'));
  const sums = assets.map(asset => `${asset.sha256}  ${asset.name}`).join('\n') + '\n';
  fs.writeFileSync(path.join(directory, 'SHA256SUMS.txt'), sums);
  assets.push({ name: 'SHA256SUMS.txt', size: Buffer.byteLength(sums), sha256: await sha256(path.join(directory, 'SHA256SUMS.txt')) });
  return assets;
}

function verifyUploaded(plan, release) {
  const uploaded = release.assets || [];
  if (!release.draft || release.prerelease || release.tag_name !== plan.tag || uploaded.length !== plan.assets.length) {
    throw Error('Draft release identity or asset count does not match the publication plan.');
  }
  for (const expected of plan.assets) {
    const matches = uploaded.filter(asset => asset.name === expected.name);
    if (matches.length !== 1 || matches[0].state !== 'uploaded' || matches[0].size !== expected.size ||
        matches[0].digest !== `sha256:${expected.sha256}`) {
      throw Error(`Uploaded asset failed size or SHA-256 verification: ${expected.name}`);
    }
  }
}

function output(key, value) {
  if (process.env.GITHUB_OUTPUT) fs.appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`);
}

async function main(args) {
  const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
  if (args[0] === 'validate') {
    output('commit', validateSource(read(args[1]), read(args[2]), args[3]));
  } else if (args[0] === 'prepare') {
    const [, directory, packageFile, notesFile, sourceFile, planFile, bodyFile] = args;
    const version = validateVersion(read(packageFile).version);
    const source = read(sourceFile);
    const tag = `v${version}`;
    const notes = fs.readFileSync(notesFile, 'utf8').trim();
    if (!notes.startsWith(`# ${tag}`)) throw Error('Release notes must describe the source build version.');
    const assets = await prepare(directory, version);
    fs.writeFileSync(planFile, JSON.stringify({ tag, commit: source.head_sha, runId: source.id, assets }, null, 2) + '\n');
    fs.writeFileSync(bodyFile, `${notes}\n\n构建来源：[Desktop builds #${source.run_number}](${source.html_url})，源码提交 \`${source.head_sha}\`。\n`);
    output('tag', tag);
  } else if (args[0] === 'verify') {
    verifyUploaded(read(args[1]), read(args[2]));
  } else {
    throw Error('Usage: prepare-release.cjs validate|prepare|verify ...');
  }
}

if (require.main === module) main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { packages, validateSource, validateVersion, prepare, verifyUploaded };
