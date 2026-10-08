import pathlib, zipfile, hashlib, json, subprocess, sys, os
import argparse
parser=argparse.ArgumentParser(description='Build and verify the complete Windows release')
parser.add_argument('--build-only',action='store_true',help='Development build only; no installer acceptance or distribution claim')
args=parser.parse_args()
root=pathlib.Path(__file__).resolve().parents[1];dist=root/'dist'
version=json.loads((root/'package.json').read_text(encoding='utf-8'))['version']
import tomllib
python_version=tomllib.loads((root.parent/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
dist.mkdir(exist_ok=True)
# A rebuilt ZIP must never inherit an earlier acceptance receipt.
(dist/'INSTALLER-ACCEPTANCE.json').unlink(missing_ok=True)
# One entrypoint, no stale lib/tgz/wheel carried into a release.
subprocess.run([sys.executable,str(root/'dev/generate-contract.py')],cwd=root,check=True)
subprocess.run([sys.executable,str(root/'dev/windows-launchers.py')],cwd=root,check=True)
subprocess.run([os.environ.get('QPM_BUILD_NODE','node'),str(root/'dev/build.cjs')],cwd=root,check=True)
subprocess.run([sys.executable,str(root/'dev/pack.py')],cwd=root,check=True)
if not args.build_only:
    test_python=os.environ.get('QPM_TEST_PYTHON',sys.executable)
    subprocess.run([test_python,'-m','pytest','tests','-q','--basetemp',str(root/'.fixtures/release-pytest')],cwd=root.parent,check=True)
    if (root.parent/'original_hashes.json').exists():
        subprocess.run([test_python,str(root.parent/'verify_original_hashes.py')],cwd=root.parent,check=True)
    else:
        print('Private original-file audit not applicable to public source checkout.')
    subprocess.run([os.environ.get('QPM_BUILD_NODE','node'),'--test','tests/runtime.test.mjs','tests/skill.test.mjs','tests/serialization.test.mjs'],cwd=root,check=True)
    subprocess.run([os.environ.get('QPM_BUILD_NODE','node'),'dev/plot-acceptance.mjs'],cwd=root,check=True)

build_temp=root/'.fixtures/build-temp';build_temp.mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable,'-m','pip','wheel',str(root.parent),'--no-deps','--no-build-isolation','--no-cache-dir','--no-index','--wheel-dir',str(dist)],cwd=root,check=True,
               env={**os.environ,'TEMP':str(build_temp),'TMP':str(build_temp),'PYTHONUTF8':'1'})
# Windows PowerShell 5.1 treats BOM-less UTF-8 scripts as the system ANSI codepage.
# Normalize at release time so later edits cannot reintroduce broken Chinese text.
for script in (root/'scripts').glob('*.ps1'):
    source=script.read_text(encoding='utf-8-sig')
    script.write_bytes(b'\xef\xbb\xbf'+source.encode('utf-8'))
files=[]
for name in ('README.md','COMPATIBILITY.md','VALIDATION.md','LICENSE','package.json','cordis.patch.yml'):
    files.append(root/name)
files += [root/f'{operation}-Qpm.cmd' for operation in ('Install','Check','Uninstall','Repair')]
for directory in ('src','lib','skills','scripts','docs','dev','tests','examples'):
    files += [p for p in (root/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += [dist/f'dsh-qpm-agent-{version}.tgz',dist/f'qpm_simulation-{python_version}-py3-none-any.whl']
release_metadata=root/'release.json'
release_metadata.write_text(json.dumps({'plugin_version':version,'python_version':python_version,'contract_version':'qpm-design/1'},indent=2),encoding='utf-8')
files.append(release_metadata)
checks={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
manifest=dist/'SHA256SUMS.json';manifest.write_text(json.dumps(checks,indent=2),encoding='utf-8')
archive=dist/f'QPM-Agent-{version}-Windows-Harness-0.2.0-rc.2.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for p in files+[manifest]:z.write(p,f'QPM-Agent-{version}/'+str(p.relative_to(root)))
print(archive);print(f'{len(files)} files; {archive.stat().st_size} bytes; SHA256 {hashlib.sha256(archive.read_bytes()).hexdigest()}')
subprocess.run([sys.executable,str(root/'dev/verify-release.py')],cwd=root,check=True)
if args.build_only:
    print('BUILD ONLY: installer acceptance not run; do not distribute as a verified release.')
else:
    subprocess.run([sys.executable,str(root/'dev/installer-acceptance.py')],cwd=root,check=True)
    report=json.loads((root/'.fixtures/installer-acceptance-report.json').read_text(encoding='utf-8'))
    if not all(r['passed'] and r['zip_sha256']==hashlib.sha256(archive.read_bytes()).hexdigest() for r in report):
        raise RuntimeError('installer acceptance does not match this ZIP')
    (dist/'INSTALLER-ACCEPTANCE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RELEASE VERIFIED: final ZIP entrypoint acceptance passed on PS5.1 and PS7.')
