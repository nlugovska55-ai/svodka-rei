// Сборка Vercel: после обязательной проверки навигации (tests/nav-gate.mjs) копирует в public/ только файлы сайта.
// Функции api/ и middleware.js Vercel берёт из корня проекта, их копировать не нужно.
import fs from 'node:fs';
import path from 'node:path';
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..'), OUT = path.join(ROOT, 'public');
fs.rmSync(OUT, {recursive: true, force: true});
for (const f of ['index.html', 'old.html', 'robots.txt', 'data/svodka.json']) {
  fs.mkdirSync(path.dirname(path.join(OUT, f)), {recursive: true});
  fs.copyFileSync(path.join(ROOT, f), path.join(OUT, f));
}
console.log('public/: ' + fs.readdirSync(OUT).join(', '));
