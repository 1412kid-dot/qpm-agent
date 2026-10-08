"""Read-only audit of the original files recorded before adaptation."""
from pathlib import Path
import hashlib,json

def verify():
    manifest=json.loads((Path(__file__).resolve().parent/'original_hashes.json').read_text(encoding='utf-8'))
    changed=[]; missing=[]
    for name,expected in manifest['original_files'].items():
        path=Path(name)
        if not path.exists(): missing.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=expected: changed.append(name)
    return {'recorded_files':len(manifest['original_files']),'changed':changed,'missing':missing,
            'all_unchanged':not changed and not missing,'solver_started':False}

if __name__=='__main__':
    result=verify(); print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['all_unchanged'] else 1)
