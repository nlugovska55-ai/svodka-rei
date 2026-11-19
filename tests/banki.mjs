// Свежесть данных банков на главной (19.11.2026, приёмка пульта): два примера на настоящей сборке data/svodka.json.
//  A. Устаревшие данные — как утром 19.11: выписка Альфы 18.11 15:55, за 19.11 сверки нет → «Поступления за сегодня не сверены».
//  B. Успешная сверка за сегодня (журнал: source Браузер:Альфа ok upto сегодня 10:20, операций 0) → «Поступлений за 19.11 нет».
//  C. Старая сборка без D.banki → тоже «не сверены» по времени файла выписки.
// Запуск: node tests/banki.mjs [папка-для-скриншотов]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..'), SHOTS = process.argv[2] || '';
const base = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/svodka.json'), 'utf8'));
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]), f = path.join(ROOT, u === '/' ? '/index.html' : u);
  if (u.startsWith('/api/')) { res.writeHead(200, {'content-type': 'application/json'}); return res.end('{"ok":true,"items":[],"nextCursor":null}'); }
  if (!fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'content-type': f.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/json'}); fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, r));
const URL0 = `http://localhost:${server.address().port}/`;
const NOW = new Date('2026-11-19T10:45:00+03:00');
const fails = [];
const check = (n, ok, fact) => { console.log((ok ? 'PASS ' : 'FAIL ') + n + ' — ' + fact); if (!ok) fails.push(n); };
const browser = await chromium.launch();
async function show(name, data) {
  const ctx = await browser.newContext({viewport: {width: 1440, height: 1000}, deviceScaleFactor: SHOTS ? 2 : 1});
  const p = await ctx.newPage(); await p.clock.install({time: NOW});
  await p.route('**/data/svodka.json*', (r) => r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)}));
  await p.goto(URL0 + '#utro'); await p.waitForSelector('#banki, .bk', {timeout: 10000});
  const txt = await p.$eval('.s-money', (e) => e.innerText);
  if (SHOTS) await (await p.$('.s-money')).screenshot({path: path.join(SHOTS, `banki-${name}.png`)});
  await ctx.close(); return txt;
}
const A = structuredClone(base);
let t = await show('A-ustarevshie', A);
check('A: Альфа — «Поступления за сегодня не сверены»', /Альфа · Поступления за сегодня не сверены/.test(t), t.match(/Альфа[^\n]*/)?.[0]);
check('A: дата последней успешной сверки 18.11 15:55', /последняя успешная сверка: 18\.11 15:55/.test(t), (t.match(/последняя успешная сверка[^\n]*/g) || []).join(' | '));
check('A: нет ложного «поступлений нет» за 19.11', !/(Поступлений за 19\.11 нет|За 19\.11 поступлений в ней нет)/.test(t), 'строки нет');
check('A: три факта раздельно с источниками', /Выписка[\s\S]*Уведомление банка[\s\S]*Сверка с порталом/.test(t) && /kontrol\/vypiski|выписка Альфы/.test(t) && /журнал запусков/.test(t) && /income\.json/.test(t), 'выписка, уведомление, портал');
const B = structuredClone(base);
B.banki.banks[0].lastOk = {upto: '2026-11-19 10:20', at: '2026-11-19T10:20:30+03:00', items: 0, run: 'signal-тест', src: 'kontrol/runs/2026-11-19.jsonl'}; B.banki.banks[0].block = null;
t = await show('B-sverka-segodnya', B);
check('B: Альфа — «Поступлений за 19.11 нет», сверено сегодня 10:20', /Альфа · Поступлений за 19\.11 нет/.test(t) && /сверено сегодня 10:20/.test(t), t.match(/Альфа[^\n]*\n[^\n]*/)?.[0].replace(/\n/, ' / '));
check('B: Точка без сверки за сегодня — «не сверены»', /Точка · Поступления за сегодня не сверены/.test(t), t.match(/Точка[^\n]*/)?.[0]);
const C = structuredClone(base); delete C.banki;
t = await show('C-bez-banki', C);
check('C: сборка без banki — «не сверены» по файлу выписки 18.11 15:55', /Альфа · Поступления за сегодня не сверены/.test(t) && /18\.11 15:55/.test(t), t.match(/Альфа[^\n]*/)?.[0]);
await browser.close(); server.close();
console.log(fails.length ? `НЕ ПРОЙДЕНО: ${fails.length}` : 'Все проверки свежести пройдены'); process.exit(fails.length ? 1 : 0);
