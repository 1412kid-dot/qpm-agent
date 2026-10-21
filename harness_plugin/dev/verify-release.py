"""Verify released bytes, installed-resource layout and source/build parity offline."""
import pathlib,json,zipfile,hashlib,tarfile,io,os
root=pathlib.Path(__file__).resolve().parents[1]
core_root=pathlib.Path(os.environ.get('QPM_CORE_ROOT',root.parent)).resolve()
version=json.loads((root/'package.json').read_text(encoding='utf-8'))['version']
archive=root/f'dist/QPM-Agent-{version}-Windows-Harness-0.2.0-rc.2.zip'
prefix=f'QPM-Agent-{version}/'
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert all(n.startswith(prefix) and '..' not in pathlib.PurePosixPath(n).parts for n in z.namelist())
    checks=json.loads(z.read(prefix+'dist/SHA256SUMS.json'))
    for name,digest in checks.items():
        assert hashlib.sha256(z.read(prefix+name)).hexdigest()==digest,name
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,'source changed after release: '+name
    for name in z.namelist():
        if name.endswith('.ps1'): assert z.read(name).startswith(b'\xef\xbb\xbf'),name
        assert '/.fixtures/' not in name and '/runtime/' not in name and '/backups/' not in name
    for operation in ('Install','Check','Uninstall','Repair'):
        text=z.read(prefix+operation+'-Qpm.cmd').decode('ascii')
        assert '\r\n' in text and '\n' not in text.replace('\r\n',''),operation
        assert '%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' in text and '\x0b' not in text and '\x0c' not in text
        assert '-NoProfile -ExecutionPolicy Bypass -File' in text and '%~dp0scripts\\'+operation+'-Qpm.ps1' in text
        assert 'Set-ExecutionPolicy' not in text and 'exit /b %qpmExit%' in text
        assert 'setlocal DisableDelayedExpansion' in text and 'set "PSModulePath="' in text
    metadata=json.loads(z.read(prefix+'release.json'))
    with tarfile.open(fileobj=io.BytesIO(z.read(prefix+f'dist/dsh-qpm-agent-{version}.tgz')),mode='r:gz') as tar:
        names=tar.getnames()
        assert 'package/lib/skill.js' in names and 'package/skills/qpm-design/SKILL.md' in names
        for name in ('lib/index.js','lib/tools.js','lib/skill.js','cordis.patch.yml','skills/qpm-design/task.schema.json'):
            assert tar.extractfile('package/'+name).read()==z.read(prefix+name)
    with zipfile.ZipFile(io.BytesIO(z.read(prefix+f'dist/qpm_simulation-{metadata["python_version"]}-py3-none-any.whl'))) as wheel:
        assert wheel.read('qpm_simulation/cache_compatibility.json')==(core_root/'qpm_simulation/cache_compatibility.json').read_bytes()
        catalog=json.loads(wheel.read('qpm_simulation/validation_catalog.json'))
        guidance=json.loads(z.read(prefix+'skills/qpm-design/capabilities.json'))
        assert guidance['validation']['release']==catalog['release']
        assert 'model_semantics' not in guidance['validation'] and 'evidence' not in guidance['validation']
        assert guidance['validation']['physics']['representative_acceptance_passed'] is True
        assert '后续版本' in guidance['validation']['future_scope']
        assert guidance['package_version']==metadata['python_version']
        assert catalog['model_semantics']['accepted'] is False
        assert catalog['autonomous_research']['real_accepted'] is False
        assert catalog['physics']['representative_acceptance_passed'] is True
        for file in (core_root/'qpm_simulation').glob('*.py'):
            assert wheel.read('qpm_simulation/'+file.name)==file.read_bytes(),'stale wheel: '+file.name
    for source in (root/'src').glob('*.ts'):
        built=z.read(prefix+'lib/'+source.stem+'.js')
        if source.stem=='client': assert source.read_bytes() in built
        else: assert source.read_bytes()==built
    assert not any(any(x in name for x in ('SEMANTIC_REVIEW_DEV10','VALIDATION_CATALOG','PHYSICS_ACCEPTANCE_20261020','KNOWN_ISSUES')) for name in z.namelist())
print(json.dumps({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'checksums':len(checks),'BOM':True,'tgz_layout':True,'wheel_source_parity':True,'source_build_parity':True,'solver_started':False},indent=2))
