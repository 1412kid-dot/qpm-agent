const {spawnSync}=require('node:child_process');const path=require('node:path');
const r=spawnSync(process.env.QPM_BUILD_PYTHON||'python',[path.join(__dirname,'pack.py')],{stdio:'inherit',windowsHide:true});process.exit(r.status??1);
