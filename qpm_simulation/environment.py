from pathlib import Path
import sys,importlib.metadata as metadata,json

def doctor(config=None):
    paths=json.loads(Path(config).read_text(encoding='utf-8')) if config else {}
    packages={}
    for name in ('numpy','scipy','pandas','matplotlib','MPh','JPype1'):
        try: packages[name]=metadata.version(name)
        except metadata.PackageNotFoundError: packages[name]=None
    return {'python':sys.version,'executable':sys.executable,'packages':packages,'paths':{
        k:{'path':v,'exists':Path(v).exists()} for k,v in paths.items()},'solver_started':False,'license_checked':False}
