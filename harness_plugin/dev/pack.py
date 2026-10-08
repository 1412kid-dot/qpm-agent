import tarfile, pathlib, json, zipfile, hashlib
root=pathlib.Path(__file__).resolve().parents[1];dist=root/'dist';dist.mkdir(exist_ok=True)
version=json.loads((root/'package.json').read_text(encoding='utf-8-sig'))['version']
with tarfile.open(dist/f'dsh-qpm-agent-{version}.tgz','w:gz') as tar:
    for name in ['package.json','cordis.patch.yml','README.md','COMPATIBILITY.md','LICENSE','lib','skills']:
        tar.add(root/name,arcname='package/'+name)
print('plugin archive built')
