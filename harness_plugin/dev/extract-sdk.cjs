// Read the installed ASAR without launching Electron. Test fixture only.
const fs = require('node:fs'), path = require('node:path');
const asar = process.argv[2] || path.join(process.env.QPM_HARNESS_ROOT||'E:/Deepseek Harness','resources/app.asar');
const target = path.resolve(__dirname, '../.fixtures/runtime');
const fd = fs.openSync(asar, 'r'), prefix = Buffer.alloc(16);
fs.readSync(fd, prefix, 0, 16, 0);
const bytes = Buffer.alloc(prefix.readUInt32LE(12)); fs.readSync(fd, bytes, 0, bytes.length, 16);
const header = JSON.parse(bytes), base = 8 + prefix.readUInt32LE(4);
let count = 0;
function walk(node, parts = []) {
  for (const [name, entry] of Object.entries(node.files || {})) {
    const next = [...parts, name], relative = next.join('/');
    if (entry.files) walk(entry, next);
    else if (relative.startsWith('dsh/') && !entry.link) {
      const out = path.resolve(target, relative.slice(4));
      if (!out.startsWith(target + path.sep)) throw Error('Unsafe archive path');
      fs.mkdirSync(path.dirname(out), { recursive: true });
      if (entry.unpacked) fs.copyFileSync(path.join(asar + '.unpacked', relative), out);
      else { const buffer = Buffer.alloc(entry.size); fs.readSync(fd, buffer, 0, buffer.length, base + Number(entry.offset)); fs.writeFileSync(out, buffer); }
      count++;
    }
  }
}
walk(header); fs.closeSync(fd); console.log(JSON.stringify({target, files: count}));
