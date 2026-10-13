#!/usr/bin/env python3
"""Build an Android APK with an existing signing key, or an explicit unsigned APK."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, default=ROOT/'dist/mobile-preview')
parser.add_argument('--jdk', type=Path, help='JDK home; defaults to JAVA_HOME or project-local tools')
parser.add_argument('--sdk', type=Path, help='Android SDK; defaults to project-local tools')
parser.add_argument('--unsigned', action='store_true', help='Build an aligned APK without a signature')
parser.add_argument('--keystore', '--release-key', dest='keystore', type=Path, help='Existing signing keystore; never creates a new key')
parser.add_argument('--signing-config', type=Path, help='Local JSON with keystore, keyAlias, storePassword, and optional keyPassword')
parser.add_argument('--key-alias', help='Alias in the existing signing keystore')
parser.add_argument('--ks-pass-env', help='Environment variable containing the keystore password')
options = parser.parse_args()
tools = ROOT/'.tmp/mobile-tools'
jdks = list((tools/'jdk').glob('*/Contents/Home'))
jdk = options.jdk or (Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None) or (jdks[0] if jdks else None)
if jdk is None:
    raise SystemExit('Set JAVA_HOME, pass --jdk, or place a JDK in .tmp/mobile-tools/jdk.')
sdk = options.sdk or tools/'sdk'

def executable(directory, name):
    candidates = [directory/(name+suffix) for suffix in (('.exe', '.bat', '') if os.name == 'nt' else ('',))]
    return next((path for path in candidates if path.is_file()), None)

def version_order(path):
    return tuple(int(part) if part.isdigit() else 0 for part in path.name.replace('android-', '').split('.'))

build_tools = next((p for p in sorted((sdk/'build-tools').iterdir(), key=version_order, reverse=True) if executable(p, 'aapt2')), None)
if build_tools is None:
    raise SystemExit('No Android build tools found in '+str(sdk/'build-tools'))
platforms = sorted((sdk/'platforms').glob('*/android.jar'), key=lambda p: version_order(p.parent), reverse=True)
if not platforms:
    raise SystemExit('No Android platform android.jar found in '+str(sdk/'platforms'))
android = platforms[0]
signing_config = json.loads(options.signing_config.read_text(encoding='utf-8')) if options.signing_config else {}
if options.signing_config and (options.keystore or options.ks_pass_env or options.key_alias):
    raise SystemExit('Use --signing-config or individual signing options, not both.')
key = Path(signing_config['keystore']) if signing_config else options.keystore or ROOT/'.local/android-signing/mhenwa-android-release.p12'
if not options.unsigned and not key.is_file():
    raise SystemExit('Existing Android signing key missing: '+str(key)+'. Pass --keystore or use --unsigned; signing identity will not be generated.')
password_variable = options.ks_pass_env or 'MHENWA_ANDROID_KEYSTORE_PASSWORD'
if not options.unsigned and not signing_config and password_variable not in os.environ:
    raise SystemExit('Use --signing-config or set the keystore password environment variable: '+password_variable)
zxing = tools/'zxing-core-3.5.3.jar'
if hashlib.sha1(zxing.read_bytes()).hexdigest() != (tools/'zxing.sha1').read_text().strip():
    raise SystemExit('ZXing checksum mismatch')
work = ROOT/'.tmp/mobile-preview/android-build'
work.mkdir(parents=True, exist_ok=True)
for name in ('gen', 'classes', 'dex'):
    folder = work/name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
env = {**os.environ, 'JAVA_HOME': str(jdk), 'PATH': str(jdk/'bin')+os.pathsep+os.environ['PATH']}
if signing_config:
    env['MHENWA_ANDROID_KEYSTORE_PASSWORD'] = signing_config['storePassword']
    env['MHENWA_ANDROID_KEY_PASSWORD'] = signing_config.get('keyPassword', signing_config['storePassword'])
def run(*args):
    subprocess.run([str(a) for a in args], env=env, check=True, cwd=ROOT)

def run_build_tool(name, *args):
    tool = executable(build_tools, name)
    if tool is None:
        raise SystemExit('Android build tool missing: '+name)
    # Run Java tools directly on Windows, avoiding cmd.exe argument expansion and limits.
    if tool.suffix == '.bat' and name == 'd8':
        run(executable(jdk/'bin', 'java'), '-Xmx1024M', '-cp', build_tools/'lib/d8.jar', 'com.android.tools.r8.D8', *args)
    elif tool.suffix == '.bat' and name == 'apksigner':
        run(executable(jdk/'bin', 'java'), '-Xmx1024M', '-jar', build_tools/'lib/apksigner.jar', *args)
    else:
        run(tool, *args)

source = ROOT/'mobile/android'
run_build_tool('aapt2','compile','--dir',source/'res','-o',work/'resources.zip')
run_build_tool('aapt2','link','-I',android,'--manifest',source/'AndroidManifest.xml','--java',work/'gen','-o',work/'base.apk',work/'resources.zip')
java = list((source/'src').rglob('*.java')) + list((work/'gen').rglob('*.java'))
run(executable(jdk/'bin', 'javac'),'-encoding','UTF-8','--release','8','-classpath',str(android)+os.pathsep+str(zxing),'-d',work/'classes',*java)
run_build_tool('d8','--lib',android,'--min-api','26','--output',work/'dex',*list((work/'classes').rglob('*.class')),zxing)
shutil.copy2(work/'base.apk',work/'unsigned.apk')
with zipfile.ZipFile(work/'unsigned.apk','a') as archive:
    for path in (work/'dex').glob('*.dex'):
        archive.write(path,path.name)
    for directory in (ROOT/'mobile/shared/web', source/'assets', ROOT/'web/client-icons'):
        for asset in directory.glob('*.js'):
            archive.write(asset,'assets/'+asset.name)
    for provider in ('codex', 'claude', 'deepseek'):
        archive.write(ROOT/'web/client-icons'/f'{provider}.png', 'assets/'+provider+'.png')
    archive.write(ROOT/'mobile/NOTICE.md','assets/NOTICE.md')
    archive.write(ROOT/'mobile/licenses/ZXing-LICENSE.txt','assets/ZXing-LICENSE.txt')
run_build_tool('zipalign','-f','4',work/'unsigned.apk',work/'aligned.apk')
out = options.output_dir.resolve()
out.mkdir(parents=True,exist_ok=True)
version = json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
apk = out/('Codex-Mobile-Bridge-'+version+('-Android-unsigned.apk' if options.unsigned else '-Android.apk'))
if options.unsigned:
    shutil.copy2(work/'aligned.apk', apk)
    print('UNSIGNED: this APK requires the existing signing identity before installation or release.')
else:
    password_source = 'env:MHENWA_ANDROID_KEYSTORE_PASSWORD' if signing_config else 'env:'+password_variable
    signing_options = ['--ks', key, '--ks-pass', password_source]
    key_alias = signing_config.get('keyAlias') or options.key_alias
    if key_alias:
        signing_options.extend(['--ks-key-alias', key_alias])
    if signing_config:
        signing_options.extend(['--key-pass', 'env:MHENWA_ANDROID_KEY_PASSWORD'])
    run_build_tool('apksigner','sign',*signing_options,'--out',apk,work/'aligned.apk')
    run_build_tool('apksigner','verify','--verbose',apk)
print(apk)
print('SHA-256',hashlib.sha256(apk.read_bytes()).hexdigest())
