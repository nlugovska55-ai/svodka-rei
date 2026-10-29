// Приёмка пульта 29.10 (две контрольные точки): 1366 и 390 px по 4 вкладкам без прокрутки вбок, фильтр дел,
// счётчик ведёт к тем же данным, источник по раскрытию, отмена диалога без изменений, правка Лены → сверка → пульт,
// сбой обмена сохраняет последний снимок, отсутствие источника не зелёное.
// Запуск: node tests/pult-kt.mjs <svodka.json с настоящими данными> [папка для снимков]
import http from 'node:http'; import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
import {execFileSync} from 'node:child_process'; import {createRequire} from 'node:module';
const require = createRequire(import.meta.url); const {chromium} = require('playwright');
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const [REAL = path.join(ROOT, 'data/svodka.json'), SHOTS] = process.argv.slice(2);
const CASES = fs.mkdtempSync(path.join(os.tmpdir(), 'pult-kt-'));
const {due} = JSON.parse(execFileSync('python3', [path.join(ROOT, 'tests/lena_cases.py'), CASES], {encoding: 'utf8'}).trim().split('\n').pop());
let DATA = REAL, posts = 0;
const srv = http.createServer((q, r) => { const u = decodeURIComponent(q.url.split('?')[0]);
  if (u.startsWith('/api/comments')) { if (q.method === 'POST') posts++; r.writeHead(200, {'content-type': 'application/json'}); return r.end('{"ok":true,"items":[],"nextCursor":null}'); }
  const f = u.startsWith('/data/svodka.json') ? DATA : path.join(ROOT, u === '/' ? 'index.html' : u);
  if (!fs.existsSync(f)) { r.writeHead(404); return r.end(); } r.writeHead(200, {'content-type': f.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/json'}); fs.createReadStream(f).pipe(r); });
await new Promise((ok) => srv.listen(0, ok)); const URL0 = 'http://localhost:' + srv.address().port + '/';
const res = []; const check = (n, ok, fact) => { res.push(!!ok); console.log((ok ? 'PASS ' : 'FAIL ') + n + ' — ' + String(fact).replace(/\s+/g, ' ').slice(0, 220)); };
const b = await chromium.launch();
const page = async (w, h) => { const p = await b.newPage({viewport: {width: w, height: h}}); p.errors = []; p.on('pageerror', (e) => p.errors.push(e.message));
  await p.route('https://fonts.googleapis.com/**', (x) => x.abort()); await p.route('https://rus-ei.ru/**', (x) => x.abort()); return p; };
const go = async (p, hash) => { await p.goto(URL0 + '?r=' + Date.now() + hash); await p.waitForTimeout(700); };
try {
  // 1. 1366 и 390: 4 вкладки, без прокрутки вбок, основной текст 16px
  for (const [w, h] of [[1366, 900], [390, 844]]) {
    const p = await page(w, h), out = [];
    for (const t of ['utro', 'money', 'buh', 'all']) { await go(p, '#' + t);
      const m = await p.evaluate(() => ({ov: document.documentElement.scrollWidth - innerWidth, fs: getComputedStyle(document.body).fontSize, sel: document.querySelector('.tab[aria-selected="true"]')?.dataset.tab}));
      out.push([t, m]); if (SHOTS) await p.screenshot({path: path.join(SHOTS, `${w}-${t}.png`), fullPage: true}); }
    check(`${w} px: 4 вкладки открываются, прокрутки вбок нет, текст 16px`, out.every(([t, m]) => m.ov <= 0 && m.fs === '16px' && m.sel === t) && !p.errors.length, out.map(([t, m]) => `${t}: ${m.ov}px/${m.fs}`).join(', ') + (p.errors.length ? '; ошибки ' + p.errors.join('; ') : ''));
    await p.close();
  }
  const p = await page(1366, 900);
  // 2. Счётчик команды ведёт к тем же делам; фильтр «Лена» во «Всех делах»
  await go(p, '#utro');
  const lenaN = +(await p.textContent('#team [data-who="Лена"] b'));
  await p.click('#team [data-who="Лена"]'); await p.waitForTimeout(300);
  const h2 = await p.textContent('#view .sh h2'); const rowsWho = await p.$$eval('#view .rw .who-t', (x) => [...new Set(x.map((e) => e.textContent))]);
  check('Счётчик «ведёт Лена» = отфильтрованный список во «Всех делах»', parseInt(h2) === lenaN && rowsWho.every((x) => x === 'Лена'), `карточка ${lenaN}, список «${h2}», метки ${rowsWho.join('/')}`);
  await p.click('.chip[data-who="all"]'); await p.waitForTimeout(200); const allN = parseInt(await p.textContent('#view .sh h2'));
  await p.fill('#q', 'гарант'); await p.waitForTimeout(300); const qN = parseInt(await p.textContent('#view .sh h2'));
  check('Поиск фильтрует дела', qN > 0 && qN < allN, `все ${allN}, по «гарант» ${qN}`);
  // 3. Источник по раскрытию
  await go(p, '#money'); const det = await p.$('details.src');
  const hid = det ? await det.evaluate((e) => e.open) : null; if (det) { await det.$eval('summary', (s) => s.click()); await p.waitForTimeout(100); }
  const shownSrc = det ? await det.evaluate((e) => e.open && e.querySelector('div').innerText.trim()) : '';
  check('Источник скрыт и открывается по нажатию', det && hid === false && !!shownSrc, `«${shownSrc}»`);
  // 4. Отмена диалога ничего не меняет
  await go(p, '#utro'); const btn = await p.$('[data-act="done"]');
  const before = await p.evaluate(() => localStorage.getItem('svodka-pending') + '|' + localStorage.getItem('svodka-outbox'));
  const id = btn ? await btn.evaluate((e) => e.closest('.rw').dataset.id) : '';
  if (btn) { await btn.click(); await p.waitForSelector('#sheet:not([hidden])'); await p.click('[data-sh="no"]'); await p.waitForTimeout(5800); }
  const after = await p.evaluate(() => localStorage.getItem('svodka-pending') + '|' + localStorage.getItem('svodka-outbox'));
  check('«Отмена» в диалоге: ни отправки, ни отметки, дело на месте', btn && posts === 0 && before === after && !!(await p.$(`.rw[data-id="${id}"]`)), `отправок ${posts}, хранилище ${before === after ? 'не изменилось' : 'изменилось'}`);
  // 4b. Находки Codex по 130ce26 на настоящих данных
  await go(p, '#money'); const bank = await p.innerText('#bank'), wks = await p.innerText('#weeks'), nd = await p.$('#nodate') ? await p.innerText('#nodate') : '';
  check('Банк: день по уведомлениям подписан источником и временем, не «по выписке»', /по уведомлениям до 16:47, не выписка/.test(bank) && /на 29\.10 16:47, по уведомлениям/.test(bank) && !/сборка 29\.10\.2026 13:00/.test(bank), (bank.match(/Источник по дням[^\n]*/) || [''])[0]);
  check('Недели: пусто = «нет записей со сроком», списание 32,25 с копейками и названием', /нет записей со сроком/.test(wks) && !/\n\s*нет\s*\n/.test(wks) && /32,25/.test(wks), (wks.match(/[^\n]*32,25[^\n]*/) || ['нет 32,25'])[0]);
  check('Гаранты без срока: отдельный блок, действие «уточнить даты» и список без дат', /Действие: уточнить даты 8 обещаний/.test(nd) && /800\s000/.test(nd) && (nd.match(/G-\d{4}/g) || []).length === 8, 'строк с ID: ' + (nd.match(/G-\d{4}/g) || []).length + ' | ' + nd.split('\n').slice(0, 2).join(' | '));
  await go(p, '#utro'); const chg = await p.innerText('#changes'), au = await p.evaluate(() => [...document.querySelectorAll('.sec')].find((x) => /Автоматика/.test(x.querySelector('h2')?.textContent || ''))?.innerText || '');
  check('«Что изменилось»: отправка актов не закрыта оплатой', !/Отправить по ЭДО[^\n]*оплачены/.test(chg) && (/Отправить по ЭДО[^\n]*отправлены/.test(chg) || !/Отправить по ЭДО/.test(chg)), (chg.match(/[^\n]*Отправить по ЭДО[^\n]*/) || ['строки нет'])[0]);
  check('Автоматика: «не проверено» не считается успешной проверкой', !/10 из 10\s*$/m.test(au) && /не проверено/.test(au.split('\n')[0] + au.split('\n')[1]), au.split('\n').slice(0, 2).join(' | '));
  // 4c. Единый файл Лены: карточка говорит про один файл и задачу сверки с подпунктами; цифр зарплаты на пульте нет
  await go(p, '#utro'); const lc = await p.innerText('#garanty'), whole = await p.innerText('body');
  check('Карточка Лены: единый файл, задача сверки «закрыто 1 из 8», зарплатных цифр нет', /единый/.test(lc) && /подпунктов закрыто 1 из 8/.test(lc) && !/22\s600|31\s380|72\s825/.test(whole), (lc.match(/Сверка[^\n]*/) || ['нет строки «Сверка»'])[0]);
  // 5. Правка Лены в таблице → сверка → пульт
  DATA = path.join(CASES, 'ok.json'); await go(p, '#utro');
  const card = await p.innerText('#garanty');
  await go(p, '#money'); const wk = await p.innerText('#weeks');
  check('Правка Лены (555 000, «Гарант», срок ' + due + ') видна в карточке и в неделе срока', /555\s000/.test(card) && /успешно/.test(card) && /Гарант \(Лена\)\s*555\s000/.test(wk), (card.match(/Ждём по гарантам[^\n]*\n?[^\n]*/) || [''])[0]);
  // 6. Сбой обмена: на пульте последний снимок и красная пометка
  DATA = path.join(CASES, 'fail.json'); await go(p, '#utro');
  const fc = await p.innerText('#garanty');
  check('Сбой обмена: «сбой», показан последний успешный снимок, данные не пропали', /сбой/.test(fc) && /последний успешный снимок/.test(fc) && /555\s000/.test(fc), fc.split('\n').slice(0, 4).join(' | '));
  // 7. Источника нет — не зелёное и не нули
  DATA = path.join(CASES, 'none.json'); await go(p, '#utro');
  const nc = await p.innerText('#garanty'), green = await p.$$eval('#garanty .plus', (x) => x.length);
  await go(p, '#money'); const nw = await p.innerText('#weeks');
  check('Нет снимка: «источник не подключён», без зелёного и нулей; недели — «не подключено»', /источник не подключён/.test(nc) && !green && !/\b0 ₽/.test(nc) && /не подключено/.test(nw), nc.split('\n').slice(0, 2).join(' | '));
  check('Ошибок JavaScript нет', !p.errors.length, p.errors.join('; ') || '0');
} finally { await b.close(); srv.close(); fs.rmSync(CASES, {recursive: true, force: true}); }
const ok = res.filter(Boolean).length; console.log(`\nИтог: ${ok} из ${res.length}`); process.exit(ok === res.length ? 0 : 1);
