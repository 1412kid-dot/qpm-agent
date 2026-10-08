"""Execute only isolated desktop-profile tests from the released ZIP."""
import os,sys,json,zipfile,pathlib,subprocess,uuid,hashlib
root=pathlib.Path(__file__).resolve().parents[1]
version=json.loads((root/'package.json').read_text(encoding='utf-8'))['version']
archive=root/f'dist/QPM-Agent-{version}-Windows-Harness-0.2.0-rc.2.zip'
harness=pathlib.Path(os.environ.get('QPM_HARNESS_ROOT','E:/Deepseek Harness'))
harness=pathlib.Path(os.environ.get('QPM_HARNESS_ROOT','E:/Deepseek Harness'))
harness=pathlib.Path(os.environ.get('QPM_HARNESS_ROOT','E:/Deepseek Harness'))
python=pathlib.Path(os.environ.get('QPM_TEST_PYTHON',sys.executable))
engines=[('ps51',pathlib.Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),('ps7',pathlib.Path(subprocess.check_output(['where.exe','pwsh'],text=True).splitlines()[0]))]
reports=[]
shell_env={k:v for k,v in os.environ.items() if k.lower()!='psmodulepath'}
for label,engine in engines:
 base=root/'.fixtures'/'installer-acceptance'/('中文 空格 '+label+' '+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None
  z.extractall(base)
 release=base/f'QPM-Agent-{version}';home=base/'home';profile=home/'profiles/desktop';profile.mkdir(parents=True)
 other=base/'other-plugin';other.mkdir();(other/'package.json').write_text(json.dumps({'name':'qpm-acceptance-other','version':'1.0.0','dsh':{'bundle':{'patch':'./cordis.patch.yml'}}}),encoding='utf-8');(other/'cordis.patch.yml').write_text('[]',encoding='utf-8')
 manifest={'name':'dsh-profile-desktop','private':True,'dependencies':{'qpm-acceptance-other':'file:'+str(other)},'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app','qpm-acceptance-other']}}}
 (profile/'package.json').write_text(json.dumps(manifest),encoding='utf-8')
 prefix="# 中文说明必须保留\n- id: unrelated\n  config:\n    text: '中文说明'\n    inert: !!js 'throw new Error(\"must not evaluate\")'\n"
 patch=profile/'cordis.patch.yml';patch.write_text(prefix,encoding='utf-8-sig' if label=='ps51' else 'utf-8')
 def quote(value): return "'"+str(value).replace("'","''")+"'"
 def policies():
  p=subprocess.run([str(engine),'-NoProfile','-Command','Get-ExecutionPolicy -List | ConvertTo-Json -Compress'],env=shell_env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
  assert p.returncode==0,(p.stdout,p.stderr)
  return json.loads(p.stdout)
 policies_before=policies()
 # The old documented entry must really fail under Restricted. A successful
 # ByPass-only smoke test cannot establish that a normal Windows user can install.
 blocked=subprocess.run([str(engine),'-NoProfile','-NonInteractive','-ExecutionPolicy','Restricted','-File',str(release/'scripts/Install-Qpm.ps1'),'-PythonExecutable',str(python),'-DryRun'],env=shell_env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
 assert blocked.returncode!=0 and any(token in blocked.stderr for token in ('PSSecurityException','UnauthorizedAccess','SecurityError')),blocked.stderr
 # Simulate files extracted from a downloaded ZIP on NTFS. Child-process
 # policy must also handle MOTW without deleting it or lowering user policy.
 for file in [*release.glob('*.cmd'),*(release/'scripts').glob('*.ps1')]:
  pathlib.Path(str(file)+':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n',encoding='ascii')
 def invoke(script,extra=(),success=True):
  args=['-HarnessRoot',str(harness)]
  if script!='Check-Qpm.ps1':args+=['-Profile','desktop','-DshHome',str(home)]
  if script in ('Install-Qpm.ps1','Check-Qpm.ps1'):args+=['-PythonExecutable',str(python)]
  args+=list(extra)
  entry=release/script.replace('.ps1','.cmd')
  command='& '+quote(entry)+' '+ ' '.join(quote(a) for a in args)+'; $qpmCode=$LASTEXITCODE; if ((Get-ExecutionPolicy) -ne "Restricted") { throw "parent_execution_policy_changed" }; exit $qpmCode'
  cmd=[str(engine),'-NoProfile','-NonInteractive','-ExecutionPolicy','Restricted','-Command',command]
  p=subprocess.run(cmd,env=shell_env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,cwd=release)
  (base/(script+'.last.log')).write_text(p.stdout+p.stderr,encoding='utf-8')
  assert (p.returncode==0)==success,(label,script,p.stdout,p.stderr)
  return p
 invoke('Install-Qpm.ps1');assert '中文说明' in patch.read_text(encoding='utf-8-sig')
 invoke('Check-Qpm.ps1')
 # User settings saved in an ordinary YAML override, with no installation marker.
 settings={'comsolBin':'F:/Comsol/用户路径/comsol.exe','defaultBackend':'demo','enableComsol':False,'maxIterations':7,'maxSolverCalls':777,'lengthMm':9,'approvedExtensions':'adapter:'+('a'*64)}
 patch.write_text(prefix+'- id: qpm-runtime\n  config: '+json.dumps(settings,ensure_ascii=False)+'\n',encoding='utf-8')
 (release/'installation.json').unlink()
 invoke('Install-Qpm.ps1');text=patch.read_text(encoding='utf-8')
 for value in ('用户路径','777','adapter:','maxIterations: 7','lengthMm: 9'):assert value in text,(value,text)
 assert text.count('- id: qpm-runtime')==1
 invoke('Install-Qpm.ps1');assert patch.read_text(encoding='utf-8').count('- id: qpm-runtime')==1
 # A legacy full preset override must gain the packaged skill without losing
 # the user's persona/custom rows or QPM runtime configuration.
 legacy_plugins=[{'id':'persona','name':'@deepseek-ai/dsh-persona','config':{'prefix':'保留用户自定义提示词'}},
   {'id':'qpm-tools','name':'dsh-qpm-agent/tools'},{'id':'tool-jobs','name':'@deepseek-ai/dsh-tool-jobs'},
   {'id':'tool-ask-user','name':'@deepseek-ai/dsh-tool-ask-user'}]
 patch.write_text(patch.read_text(encoding='utf-8')+'- id: preset-qpm\n  config:\n    plugins: '+json.dumps(legacy_plugins,ensure_ascii=False)+'\n',encoding='utf-8')
 invoke('Install-Qpm.ps1');text=patch.read_text(encoding='utf-8')
 assert '保留用户自定义提示词' in text and 'dsh-qpm-agent/skill' in text and '@deepseek-ai/dsh-tool-skill' in text
 # Altered archive content must be rejected before any profile mutation.
 script=release/'scripts/Install-Qpm.ps1';original=script.read_bytes();before=patch.read_bytes()
 script.write_bytes(original+b'\n# tampered\n');p=invoke('Install-Qpm.ps1',success=False);assert 'release_checksum_mismatch' in p.stderr;assert patch.read_bytes()==before;script.write_bytes(original)
 # Missing scientific dependencies: real Python with site-packages disabled.
 fake=base/'python_no_site.cmd';fake.write_text('@echo off\n"'+str(python)+'" -S %*\n',encoding='ascii')
 cmd=[str(engine),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-Profile','desktop','-DshHome',str(home),'-PythonExecutable',str(fake)]
 p=subprocess.run(cmd,env=shell_env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,cwd=release)
 assert p.returncode!=0 and 'python_scientific_dependencies_missing' in p.stderr,(p.stdout,p.stderr);assert patch.read_bytes()==before
 invoke('Uninstall-Qpm.ps1');assert 'qpm-runtime' not in patch.read_text(encoding='utf-8')
 invoke('Uninstall-Qpm.ps1')
 # A late static-validation failure must restore a previously uninstalled state.
 before=patch.read_bytes();(home/'cordis.patch.yml').write_text('- id: qpm-runtime\n  disabled: true\n',encoding='utf-8')
 p=invoke('Install-Qpm.ps1',success=False);assert 'static_validation' in p.stderr and '恢复=restored' in p.stderr,(p.stdout,p.stderr)
 assert patch.read_bytes()==before
 current=json.loads((profile/'package.json').read_text(encoding='utf-8'));assert 'dsh-qpm-agent' not in current.get('dependencies',{});assert 'dsh-qpm-agent' not in current['dsh']['profile']['bundles']
 operations=sorted((release/'backups').glob('*/operation.json'),key=lambda p:p.stat().st_mtime)
 op=json.loads(operations[-1].read_text(encoding='utf-8'));assert op['recovery']=='restored'
 repair_command='& '+quote(release/'Repair-Qpm.cmd')+' -BackupDirectory '+quote(operations[-1].parent)+'; exit $LASTEXITCODE'
 repair=subprocess.run([str(engine),'-NoProfile','-NonInteractive','-ExecutionPolicy','Restricted','-Command',repair_command],env=shell_env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,cwd=release)
 assert repair.returncode==0,(repair.stdout,repair.stderr)
 (home/'cordis.patch.yml').unlink();invoke('Install-Qpm.ps1');invoke('Check-Qpm.ps1')
 # Compare-and-swap rejects a concurrently edited patch without overwriting it.
 def helper(action,**extra):
  request=base/'helper-request.json';request.write_text(json.dumps(dict(action=action,harnessRoot=str(harness),releaseRoot=str(release),profileDir=str(profile),dshHome=str(home),**extra)),encoding='utf-8')
  env={**os.environ,'ELECTRON_RUN_AS_NODE':'1'}
  return subprocess.run([str(harness/'DeepSeek Harness.exe'),'--expose-internals',str(release/'scripts/profile-helper.cjs'),str(request)],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
 snap=json.loads(helper('snapshot').stdout)['value'];patch.write_bytes(patch.read_bytes()+b'\n# concurrent edit\n');concurrent=patch.read_bytes()
 p=helper('apply',remove=True,expectedHash=snap['patchHash']);assert p.returncode!=0 and 'configuration_changed_concurrently' in p.stderr;assert patch.read_bytes()==concurrent
 # Invalid config is refused before package/profile writes; no credential-shaped source in error.
 before=patch.read_bytes();patch.write_text(prefix+'- id: qpm-runtime\n  config: {maxIterations: 0}\n',encoding='utf-8');bad=patch.read_bytes();p=invoke('Install-Qpm.ps1',success=False);assert patch.read_bytes()==bad;assert 'configuration_parse_or_schema_error' in p.stderr;patch.write_bytes(before)
 invoke('Uninstall-Qpm.ps1')
 current=json.loads((profile/'package.json').read_text(encoding='utf-8'));assert current['dependencies']['qpm-acceptance-other']==manifest['dependencies']['qpm-acceptance-other'];assert 'qpm-acceptance-other' in current['dsh']['profile']['bundles']
 assert policies()==policies_before,'persistent execution policy changed'
 reports.append({'engine':label,'desktop':True,'zip':str(archive),'zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'entry':'CMD from Restricted parent','persistent_policy_unchanged':True,'cases':['install','read-only check','BOM/no-BOM profile','Chinese/space paths','reinstall preserves user settings','legacy preset skill migration','missing installation metadata','idempotent uninstall','tampered package','missing dependencies','late failure rollback','explicit repair','retry','concurrent edit','invalid config','unrelated plugin preservation','direct PS1 blocked under Restricted','CMD launch under Restricted','downloaded-script marker','persistent policy unchanged'],'passed':True})
 print(label+': isolated desktop ZIP acceptance PASS',flush=True)
(root/'.fixtures/installer-acceptance-report.json').write_text(json.dumps(reports,indent=2,ensure_ascii=False),encoding='utf-8')
