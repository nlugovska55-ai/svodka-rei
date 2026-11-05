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
    for (const t of ['utro', 'kontrol', 'money', 'buh', 'all']) { await go(p, '#' + t);
      const m = await p.evaluate(() => ({ov: document.documentElement.scrollWidth - innerWidth, fs: getComputedStyle(document.body).fontSize, sel: document.querySelector('.tab[aria-selected="true"]')?.dataset.tab}));
      out.push([t, m]); if (SHOTS) await p.screenshot({path: path.join(SHOTS, `${w}-${t}.png`), fullPage: true}); }
    check(`${w} px: 4 вкладки открываются, прокрутки вбок нет, текст 16px`, out.every(([t, m]) => m.ov <= 0 && m.fs === '16px' && m.sel === t) && !p.errors.length, out.map(([t, m]) => `${t}: ${m.ov}px/${m.fs}`).join(', ') + (p.errors.length ? '; ошибки ' + p.errors.join('; ') : ''));
    await p.close();
  }
  const p = await page(1366, 900);
  // 2. Счётчик команды ведёт к тем же делам; фильтр «Лена» во «Всех делах»
  await go(p, '#kontrol');
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
  // на живых данных сверенных решений Ольги может не быть (честно пусто) — для проверки диалога добавляем одно сверенное дело
  const todayRu = new Intl.DateTimeFormat('ru-RU', {timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit'}).format(new Date());
  const withMine = JSON.parse(fs.readFileSync(REAL, 'utf8')); withMine.tasks.push({id: 'kt-dlg', section: 'Мои клиенты', client: 'Тест', title: 'Проверьте тестовое дело', next: 'Ольге: проверить', due: '', waiting: 'я', urgency: 'сегодня', checked: todayRu + ' 00:01', src: 'тест: банк ' + todayRu, thread: 'Утро', sourceCheckedAt: new Date(Date.now() - 6e4).toISOString(), sourceEvidence: 'тест: журнал чтения'});
  const dlgFile = path.join(CASES, 'dlg.json'); fs.writeFileSync(dlgFile, JSON.stringify(withMine)); DATA = dlgFile;
  await go(p, '#kontrol'); const btn = await p.$('[data-act="done"]');
  const before = await p.evaluate(() => localStorage.getItem('svodka-pending') + '|' + localStorage.getItem('svodka-outbox'));
  const id = btn ? await btn.evaluate((e) => e.closest('.rw').dataset.id) : '';
  if (btn) { await btn.click(); await p.waitForSelector('#sheet:not([hidden])'); await p.click('[data-sh="no"]'); await p.waitForTimeout(5800); }
  const after = await p.evaluate(() => localStorage.getItem('svodka-pending') + '|' + localStorage.getItem('svodka-outbox'));
  check('«Отмена» в диалоге: ни отправки, ни отметки, дело на месте', btn && posts === 0 && before === after && !!(await p.$(`.rw[data-id="${id}"]`)), `отправок ${posts}, хранилище ${before === after ? 'не изменилось' : 'изменилось'}`);
  DATA = REAL;
  // 05.11 (Ольга: «нерабочий, всё старое и в куче»): «Сегодня от Ольги» — все дела waiting Ольга/я, несверенные с меткой;
  // дубли (тот же dedupe / заголовок+ветка) — одна строка; без движения > 14 дней и archive от сборщика — в «Архив»
  { const R = JSON.parse(fs.readFileSync(REAL, 'utf8')), ymd = (n) => new Date(Date.now() + n * 864e5 + 3 * 36e5).toISOString().slice(0, 10), ru = (y) => y.slice(8, 10) + '.' + y.slice(5, 7) + '.' + y.slice(0, 4);
    const base = {client: 'Тест', amount: 0, next: 'Шаг', thread: 'Утро', due: ru(ymd(1)), lastMove: ru(ymd(0)), added: ru(ymd(0))};
    R.tasks = [{...base, id: 'kt-o1', title: 'Подписать акт Тест', waiting: 'Ольга', amount: 12345, link: 'https://diadoc.kontur.ru/x', sourceCheckedAt: new Date().toISOString(), sourceEvidence: 'Диадок'},
      {...base, id: 'kt-o2', title: 'Ответить Тесту', waiting: 'я', next: 'Ольге ответить в 1С'},
      {...base, id: 'kt-t1', title: 'Сверить Тест', waiting: 'Лена', dedupe: 'D1'}, {...base, id: 'kt-t2', title: 'Сверить Тест (копия)', waiting: 'Лена', dedupe: 'D1'},
      {...base, id: 'kt-t3', title: 'Тот же заголовок', waiting: 'Claude', origin: 'X'}, {...base, id: 'kt-t4', title: 'Тот же  заголовок!', waiting: 'Claude', origin: 'X'},
      {...base, id: 'kt-a1', title: 'Старое дело', waiting: 'клиент', lastMove: ru(ymd(-20)), added: ru(ymd(-30)), checked: ''},
      {...base, id: 'kt-a2', title: 'Закрыто сборщиком', waiting: 'Лена', archive: true, archiveReason: 'закрыто 04.11'}];
    R.summary = []; const f = path.join(CASES, 'utro.json'); fs.writeFileSync(f, JSON.stringify(R)); DATA = f; await go(p, '#utro');
    const ids = (sel) => p.$$eval(sel + ' .rw', (x) => x.map((e) => e.dataset.id));
    const o = await ids('.s-do'), op = await ids('.s-open'), ar = await p.$$eval('.s-arch .rw', (x) => x.map((e) => e.dataset.id + ':' + e.textContent.replace(/\s+/g, ' ')));
    const o1 = await p.innerText('.s-do .rw[data-id="kt-o1"]'), o2 = await p.innerText('.s-do .rw[data-id="kt-o2"]');
    check('«Сегодня от Ольги»: все дела waiting Ольга/я, несверенное с меткой, сумма/место/срок/ссылка', o.join() === 'kt-o1,kt-o2' && /12\s345/.test(o1) && /Диадок/.test(o1) && /завтра/.test(o1) && !/не сверено/.test(o1) && /не сверено сегодня/.test(o2) && /1С/.test(o2) && !!(await p.$('.s-do .rw[data-id="kt-o1"] a[href^="https://diadoc"]')), `${o.join()} | ${o1.replace(/\s+/g, ' ')} | ${o2.replace(/\s+/g, ' ')}`);
    check('Открытые: дубли по dedupe и заголовку+ветке схлопнуты, у строки шаг и исполнитель', op.join() === 'kt-t1,kt-t3' && /Шаг: Шаг/.test(await p.innerText('.s-open')), op.join());
    check('Архив: без движения > 14 дней и archive сборщика', ar.length === 2 && ar.some((x) => /^kt-a1:.*без движения 20 дн/.test(x)) && ar.some((x) => /^kt-a2:.*закрыто 04\.11/.test(x)), ar.join(' | ').slice(0, 200));
    // 05.11 (Ольга: «в части карточек одни команды, в части другие»): одинаковые команды в строках всех вкладок и в карточке дела
    const sets = new Set(); for (const tb of ['utro', 'kontrol', 'all']) { await go(p, '#' + tb); (await p.$$eval('.rw', (x) => x.map((e) => [...e.querySelectorAll('[data-act],[data-cmt]')].map((q) => q.textContent.trim()).join('/')))).forEach((k) => sets.add(k)); }
    await go(p, '#utro'); await p.click('.s-open .rw .st'); await p.waitForTimeout(400); const dr = await p.$$eval('[data-dact]', (x) => x.map((q) => q.textContent.trim()).join('/'));
    check('Одинаковые команды во всех строках и в карточке: Я выполнила / Поручить Claude / Комментарий', sets.size === 1 && [...sets][0] === 'Я выполнила/Поручить Claude/Комментарий' && dr.startsWith('Я выполнила/Поручить Claude/Комментарий/'), [...sets].join(' | ') + ' · карточка: ' + dr);
    await p.keyboard.press('Escape');
    DATA = REAL; }
  // 4b. Находки Codex по 130ce26 на настоящих данных
  await go(p, '#money'); const bank = await p.innerText('#bank'), wks = await p.innerText('#weeks'), nd = await p.$('#nodate') ? await p.innerText('#nodate') : '';
  check('Банк: день по уведомлениям подписан источником и временем, не «по выписке»', /по уведомлениям до \d\d:\d\d, не выписка/.test(bank) && /на \d\d\.\d\d \d\d:\d\d, по уведомлениям/.test(bank) && !/сборка 29\.10\.2026 13:00/.test(bank), (bank.match(/Источник по дням[^\n]*/) || [''])[0]);
  check('Недели: пусто = «нет записей со сроком», списание 32,25 с копейками и названием', /нет записей со сроком/.test(wks) && !/\n\s*нет\s*\n/.test(wks) && /32,25/.test(wks), (wks.match(/[^\n]*32,25[^\n]*/) || ['нет 32,25'])[0]);
  check('Гаранты без срока: отдельный блок, действие «уточнить даты» и список без дат', /Действие: уточнить даты 8 обещаний/.test(nd) && /800\s000/.test(nd) && (nd.match(/G-\d{4}/g) || []).length === 8, 'строк с ID: ' + (nd.match(/G-\d{4}/g) || []).length + ' | ' + nd.split('\n').slice(0, 2).join(' | '));
  await go(p, '#kontrol'); const chg = await p.innerText('#changes'), au = await p.evaluate(() => [...document.querySelectorAll('.sec')].find((x) => /Автоматика/.test(x.querySelector('h2')?.textContent || ''))?.innerText || '');
  check('«Что изменилось»: отправка актов не закрыта оплатой', !/Отправить по ЭДО[^\n]*оплачены/.test(chg) && (/Отправить по ЭДО[^\n]*отправлены/.test(chg) || !/Отправить по ЭДО/.test(chg)), (chg.match(/[^\n]*Отправить по ЭДО[^\n]*/) || ['строки нет'])[0]);
  check('Автоматика: «не проверено» не считается успешной проверкой', !/10 из 10\s*$/m.test(au) && /не проверено/.test(au.split('\n')[0] + au.split('\n')[1]), au.split('\n').slice(0, 2).join(' | '));
  // 4c. Единый файл Лены: карточка говорит про один файл и задачу сверки с подпунктами; цифр зарплаты на пульте нет
  await go(p, '#kontrol'); const lc = await p.innerText('#garanty'), whole = await p.innerText('body');
  check('Карточка Лены: единый файл, задача сверки «закрыто N из M», зарплатных цифр нет', /единый/.test(lc) && /подпунктов закрыто \d+ из \d+/.test(lc) && !/22\s600|31\s380|72\s825/.test(whole), (lc.match(/Сверка[^\n]*/) || ['нет строки «Сверка»'])[0]);
  // 4d. Неполный контроль не выдаётся за «ничего срочного»: число непроверенных и действие, которое уходит Claude
  await go(p, '#kontrol');
  const gp = await p.evaluate(() => ({gap: document.querySelector('#decide .gap')?.innerText || '', calm: /Ничего срочного/.test(document.body.innerText), h1: document.querySelector('h1').innerText}));
  const sent0 = posts; if (gp.gap) { await p.click('[data-gapclaude]'); await p.waitForTimeout(5500); }
  check('Непроверенные дела: блок «срочность не подтверждена» с числом, кнопка поручает Claude сверку, «ничего срочного» нет',
    /не подтверждена: сегодня не сверено с источником \d+ из \d+/.test(gp.gap) && /Контроль неполный/.test(gp.h1) && !gp.calm && posts > sent0, `${gp.h1} | ${gp.gap.slice(0, 120)} | отправок ${posts - sent0}`);
  // 5. Правка Лены в таблице → сверка → пульт
  DATA = path.join(CASES, 'ok.json'); await go(p, '#kontrol');
  const card = await p.innerText('#garanty');
  await go(p, '#money'); const wk = await p.innerText('#weeks');
  check('Правка Лены (555 000, «Гарант», срок ' + due + ') видна в карточке и в неделе срока', /555\s000/.test(card) && /успешно/.test(card) && /Гарант \(Лена\)\s*555\s000/.test(wk), (card.match(/Ждём по гарантам[^\n]*\n?[^\n]*/) || [''])[0]);
  // 6. Сбой обмена: на пульте последний снимок и красная пометка
  DATA = path.join(CASES, 'fail.json'); await go(p, '#kontrol');
  const fc = await p.innerText('#garanty');
  check('Сбой обмена: «сбой», показан последний успешный снимок, данные не пропали', /сбой/.test(fc) && /последний успешный снимок/.test(fc) && /555\s000/.test(fc), fc.split('\n').slice(0, 4).join(' | '));
  // 7. Источника нет — не зелёное и не нули
  DATA = path.join(CASES, 'none.json'); await go(p, '#kontrol');
  const nc = await p.innerText('#garanty'), green = await p.$$eval('#garanty .plus', (x) => x.length);
  await go(p, '#money'); const nw = await p.innerText('#weeks');
  check('Нет снимка: «источник не подключён», без зелёного и нулей; недели — «не подключено»', /источник не подключён/.test(nc) && !green && !/\b0 ₽/.test(nc) && /не подключено/.test(nw), nc.split('\n').slice(0, 2).join(' | '));
  check('Ошибок JavaScript нет', !p.errors.length, p.errors.join('; ') || '0');
} finally { await b.close(); srv.close(); fs.rmSync(CASES, {recursive: true, force: true}); }
const ok = res.filter(Boolean).length; console.log(`\nИтог: ${ok} из ${res.length}`); process.exit(ok === res.length ? 0 : 1);
