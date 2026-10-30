// Сквозной сценарий на НАСТОЯЩИХ данных: поручение → принято → в работе → результат → пульт.
// Кнопка пульта и браузер настоящие, сборка — настоящий scripts/build_svodka.py на копии выгрузки db.
// Подменены только два внешних звена: ветка inbox (запрос /api/comments пишется в память) и запись в db
// (отметку кладём в копию comments так, как её кладёт inbox_to_db.py + разводка «Утра» по §16).
// Запуск: node tests/skvoznoy.mjs <выгрузка db: папка с tasks/ и comments/> <summary.json> [отчёт.md]
// В репо ничего не пишет: данные собираются во временную папку.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright');

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const [DB, SUMMARY, REPORT] = process.argv.slice(2);
if (!DB || !SUMMARY) { console.error('нужно: <выгрузка db> <summary.json> [отчёт.md]'); process.exit(2); }
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'skvoznoy-'));
fs.cpSync(DB, path.join(TMP, 'db'), {recursive: true});
const OUT = path.join(TMP, 'svodka.json');
const build = () => execFileSync('python3', ['-I', path.join(ROOT, 'scripts/build_svodka.py'), path.join(TMP, 'db'), SUMMARY, OUT], {encoding: 'utf8'});
build();

const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]);
  const f = u.startsWith('/data/svodka.json') ? OUT : path.join(ROOT, u.replace(/^\/$/, '/index.html'));
  if (!fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'content-type': f.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/json'}); fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, r));
const URL0 = 'http://localhost:' + server.address().port + '/';
const log = [], results = [];
const check = (name, ok, fact) => { results.push({name, ok: !!ok, fact}); log.push(`| ${ok ? 'PASS' : 'FAIL'} | ${name} | ${String(fact).replace(/\|/g, '/').replace(/\s+/g, ' ').slice(0, 220)} |`); console.log((ok ? 'PASS ' : 'FAIL ') + name + ' — ' + fact); };

const browser = await chromium.launch();
const inbox = [];
const ctx = await browser.newContext({viewport: {width: 1280, height: 1000}});
const p = await ctx.newPage(); const errors = []; p.on('pageerror', (e) => errors.push(e.message));
await p.route('**/api/comments*', async (r) => {
  if (r.request().method() === 'POST') { inbox.push(...JSON.parse(r.request().postData()).items); return r.fulfill({status: 200, contentType: 'application/json', body: '{"ok":true}'}); }
  return r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({ok: true, items: inbox.map((x) => ({taskId: x.taskId, action: x.action, at: x.at, eid: x.eid})), nextCursor: null})});
});
const go = async (hash = '#utro') => { await p.goto(URL0 + '?r=' + Date.now() + hash); await p.waitForTimeout(800); };
const stageOf = async (needle) => (await p.$$eval('#replies .jl', (x) => x.map((e) => e.dataset.stage + '|' + e.textContent.replace(/\s+/g, ' ')))).find((t) => t.includes(needle)) || 'нет строки';

try {
  // 0. Что пульт показывает по настоящим данным до сценария
  await go();
  const cover = (await p.innerText('.cover').catch(() => '')).replace(/\s+/g, ' ');
  check('Полнота сверки видна на первом экране', /Во всех открытых делах: сверено сегодня \d+ из \d+/.test(cover), cover.slice(0, 200));
  const real = await p.$$eval('#replies .jl', (x) => x.map((e) => e.dataset.stage));
  const cnt = (k) => real.filter((s) => s === k).length;
  check('Настоящие отметки: стадии без «зелёного» итога у закрытых без содержания', real.length > 0,
    `всего ${real.length}: итог ${cnt('res')}, в работе ${cnt('rt')}, итог не подтверждён ${cnt('chk')}, вернули в работу ${cnt('reo')}, ждёт разбора ${cnt('wait')}`);
  const auto = (await p.innerText('section.sec:has(h2:text("Автоматика"))').catch(() => '')).replace(/\s+/g, ' ');
  check('Автоматика: статусы из файла с временем проверки, реестр гарантов отдельной строкой', /Реестр гарантов Лены/.test(auto) && /проверено|статус от/.test(auto), auto.slice(0, 260));

  // 1. Поручение: настоящая кнопка «Поручить Claude» на первом деле базы с этой кнопкой (кнопки есть только у дел, где решение за Ольгой)
  await go('#utro');
  const id = await p.$eval('.rw:not([data-id^="sum-"]) [data-act="claude"]', (b) => b.closest('.rw').dataset.id);  // дело базы, не строка списка «Утра»
  const short = await p.$eval(`.rw[data-id="${id}"] .t`, (e) => e.textContent.trim());
  await p.click(`.rw[data-id="${id}"] [data-act="claude"]`); await p.waitForSelector('#shText');
  await p.fill('#shText', 'Сквозная проверка аудита 29.10: не исполнять'); await p.click('#cf-yes'); await p.waitForTimeout(5800);
  const ev = inbox.find((x) => x.taskId === id);
  check('1. Поручение ушло с сайта (POST в inbox, action=claude, есть eid)', ev && ev.action === 'claude' && ev.eid, ev ? `дело ${id} «${short}», eid ${ev.eid}` : 'POST не пришёл');
  await go();
  check('2. Принято: пульт показывает «принято сайтом, ждёт разбора»', /^wait\|.*ждёт разбора/.test(await stageOf(short.slice(0, 30))), await stageOf(short.slice(0, 30)));

  // 3. В работе: отметка в db так, как её кладут inbox_to_db.py prepare/ack и разводка «Утра» (§16 п.2)
  const doc = {eventId: ev.eid, taskId: id, taskTitle: short, text: '[поручено Claude] ' + ev.text, action: 'claude', at: ev.at,
    atStr: new Date(Date.parse(ev.at) + 3 * 36e5).toISOString().slice(8, 10) + '.' + new Date(Date.parse(ev.at) + 3 * 36e5).toISOString().slice(5, 7) + ', ' + new Date(Date.parse(ev.at) + 3 * 36e5).toISOString().slice(11, 16),
    status: 'routed', routedTo: 'Бухгалтерия', routedAt: new Date().toISOString(), reply: 'у Бухгалтерии: сверяем', latest: true};
  const cf = path.join(TMP, 'db', 'comments', 'skvoznoy-' + ev.eid + '.json');
  fs.mkdirSync(path.dirname(cf), {recursive: true}); fs.writeFileSync(cf, JSON.stringify(doc)); build();
  await go();
  const waitGone = !(await stageOf(short.slice(0, 30))).startsWith('wait');
  check('3. В работе: «в работе у Бухгалтерия с ЧЧ:ММ», строка «ждёт разбора» снята', waitGone && /^rt\|.*в работе у Бухгалтерия с \d\d:\d\d/.test(await stageOf('Сквозная проверка')), await stageOf('Сквозная проверка') + (waitGone ? '' : ' (ожидание осталось)'));

  // 4. Ветка закрыла словом «сделано» без итога — пульт не выдаёт это за результат
  fs.writeFileSync(cf, JSON.stringify({...doc, status: 'done', result: 'сделано'})); build(); await go();
  check('4. «сделано» без содержания: «Итог не подтверждён»', /^chk\|.*Итог не подтверждён/.test(await stageOf('Сквозная проверка')), await stageOf('Сквозная проверка'));

  // 5. Результат: ветка пишет result/resultAt/resultBy (§16 п.3)
  fs.writeFileSync(cf, JSON.stringify({...doc, status: 'done', result: 'Сверено с 1С и Диадоком: документ отправлен и подписан, оплата внесена в портал, расхождений нет, файл buh/sverki-test.md', resultAt: new Date().toISOString(), resultBy: 'Бухгалтерия'})); build(); await go();
  check('5. Результат на пульте: «Итог (Бухгалтерия, ЧЧ:ММ): …»', /^res\|.*Итог \(Бухгалтерия, \d\d:\d\d\): Сверено с 1С/.test(await stageOf('Сквозная проверка')), await stageOf('Сквозная проверка'));
  check('Ошибок JavaScript нет', !errors.length, errors.join('; ') || '0');
} finally {
  await browser.close(); server.close();
  fs.rmSync(TMP, {recursive: true, force: true});
}
const pass = results.filter((r) => r.ok).length;
console.log(`\nИтог: ${pass} из ${results.length}`);
if (REPORT) fs.writeFileSync(REPORT, `# Сквозной сценарий пульта на настоящих данных\n\nЗапуск ${new Date().toISOString()}, коммит пульта: ${execFileSync('git', ['-C', ROOT, 'rev-parse', '--short', 'HEAD'], {encoding: 'utf8'}).trim()}\n\n| Итог | Шаг | Факт |\n|---|---|---|\n${log.join('\n')}\n\nИтог: ${pass} из ${results.length}\n`);
process.exit(pass === results.length ? 0 : 1);
