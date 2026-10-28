// Приёмочные проверки пульта «Утро» в настоящем браузере (Playwright + Chromium).
// Данные — обезличенный tests/fixture-svodka.json, сервер отметок (/api/comments) подменён памятью теста.
// Запуск: node tests/acceptance.mjs [папка-для-скриншотов]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright');

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const SHOTS = process.argv[2] || '';
const base = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixture-svodka.json'), 'utf8'));
const iso = (d) => { const z = new Date(d.getTime() + 3 * 36e5); return z.toISOString().slice(0, 19) + '+03:00'; };
const todayRu = (() => { const z = new Date(Date.now() + 3 * 36e5); return String(z.getUTCDate()).padStart(2, '0') + '.' + String(z.getUTCMonth() + 1).padStart(2, '0'); })();
// «Сверено Утром сегодня»: checked + src + thread, как требует §14
const verify = (t) => Object.assign(t, {checked: todayRu + ' Утро и задачи', src: 'тест: банк и почта', thread: 'Утро'});
const fresh = () => { const d = structuredClone(base); d.updated = iso(new Date(Date.now() - 5 * 6e4)); const ids = new Set(d.summary.map((s) => s.id)); d.tasks.forEach((t) => { if (ids.has(t.id)) verify(t); }); return d; };

const server = http.createServer((req, res) => {
  const f = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]).replace(/^\/$/, '/index.html'));
  if (!f.startsWith(ROOT) || !fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'content-type': f.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/octet-stream'}); fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, r));
const URL0 = 'http://localhost:' + server.address().port + '/';

const results = [];
const check = (name, ok, fact) => { results.push({name, ok: !!ok, fact}); console.log((ok ? 'PASS ' : 'FAIL ') + name + ' — ' + fact); };

const browser = await chromium.launch();
// «Сервер отметок»: общий для всех устройств теста, как ветка inbox
let inbox = [], apiMode = 'ok';
async function device(data, opts = {}) {
  const ctx = await browser.newContext({viewport: opts.viewport || {width: 1280, height: 1000}, colorScheme: opts.scheme || 'light', isMobile: !!opts.mobile, hasTouch: !!opts.mobile});
  const p = await ctx.newPage(); p.errors = []; p.on('pageerror', (e) => p.errors.push(e.message));
  await p.route('**/data/svodka.json*', (r) => typeof data === 'number' ? r.fulfill({status: data, body: 'err'}) : r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)}));
  await p.route('**/api/comments*', async (r) => {
    if (apiMode === 'fail') return r.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({ok: false, error: 'Не задан GITHUB_TOKEN в настройках Vercel'})});
    if (r.request().method() === 'POST') { const b = JSON.parse(r.request().postData()); inbox.push(...b.items); return r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({ok: true, count: b.items.length})}); }
    return r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({ok: true, items: inbox.map((x) => ({taskId: x.taskId, action: x.action, at: x.at}))})});
  });
  return {ctx, p};
}
const go = async (p, hash = '#utro') => { await p.goto(URL0 + hash); await p.waitForTimeout(700); };
const rowIds = (p) => p.$$eval('.rows:first-of-type .rw', (x) => x.map((e) => e.dataset.id));
const ask = (p) => p.$$eval('section.sec', (ss) => { const s = ss.find((e) => /Нужно от вас/.test(e.querySelector('h2')?.textContent || '')); return s ? [...s.querySelectorAll('.rw')].map((e) => e.dataset.id) : []; });

try {
  const D = fresh();
  const verified = D.summary.filter((s) => s.section === 'Нужно от вас').map((s) => s.id);

  // 1. Только проверенное «Утром», не больше 5 за раз, остальное следующей порцией
  {
    const d = fresh(); const mineIds = d.tasks.filter((t) => /^Ольг/.test(t.next) && !verified.includes(t.id)).map((t) => t.id).slice(0, 5);
    d.tasks.forEach((t) => { if (mineIds.includes(t.id)) verify(t); });
    d.summary = d.summary.filter((s) => s.section !== 'Нужно от вас').concat([...new Set(mineIds.concat(verified))].slice(0, 7).map((id, i) => ({id, section: 'Нужно от вас', flag: '', text: 'Тема: проверенное дело ' + (i + 1)})));
    const {ctx, p} = await device(d); await go(p);
    const first = await ask(p); const btn = await p.textContent('[data-portion]').catch(() => '');
    await p.click('[data-portion]'); await p.waitForTimeout(200); const second = await ask(p);
    const unv = d.tasks.filter((t) => /^Ольг/.test(t.next) && !mineIds.includes(t.id) && !verified.includes(t.id)).map((t) => t.id);
    check('Не больше 5 проверенных дел, остальные — следующей порцией', first.length === 5 && second.length === 2 && /Следующие 2/.test(btn), `первая порция ${first.length}, кнопка «${btn.trim()}», вторая порция ${second.length}`);
    check('Непроверенные дела Ольги не попадают в «Нужно от вас»', ![...first, ...second].some((id) => unv.includes(id)), `непроверенных с шагом «Ольга» в данных ${unv.length}, в списке 0`);
    await ctx.close();
  }

  // 2. Сохранение после перезагрузки и на другом устройстве; отмена не теряет
  {
    inbox = []; apiMode = 'ok';
    const A = await device(D); await go(A.p);
    const id0 = (await ask(A.p))[0];
    await A.p.click(`.rw[data-id="${id0}"] .chk`); await A.p.waitForTimeout(300);
    if (SHOTS) await A.p.screenshot({path: path.join(SHOTS, 'd-toast-otmena.png')});
    await A.p.waitForTimeout(5600);  // окно отмены 5 с, потом отправка
    const sent = inbox.filter((x) => x.taskId === id0 && x.action === 'done').length;
    await A.p.reload(); await A.p.waitForTimeout(700);
    const afterReload = await ask(A.p);
    check('Отметка «сделано» уходит на сервер после окна отмены', sent === 1, `в inbox записей по делу: ${sent}`);
    check('Отметка сохраняется после перезагрузки', !afterReload.includes(id0), `дело ${afterReload.includes(id0) ? 'снова видно' : 'скрыто, помечено «ждёт синхронизации»'}`);
    const B = await device(D); await go(B.p);
    const onB = await ask(B.p);
    await go(B.p, '#all'); await B.p.click('[data-who="marked"]'); await B.p.waitForTimeout(200);
    const markedB = await B.p.$$eval('.rw', (x) => x.map((e) => e.dataset.id));
    check('Отметка видна на другом устройстве (чистый браузер)', !onB.includes(id0) && markedB.includes(id0), `на втором устройстве дело скрыто: ${!onB.includes(id0)}, в «Отмечено мной»: ${markedB.includes(id0)}`);
    // вернуть с устройства B — на A после перезагрузки дело снова в списке
    await B.p.click(`.rw[data-id="${id0}"] [data-act="reopen"]`); await B.p.waitForTimeout(5800);
    await A.p.evaluate(() => localStorage.clear()); await A.p.reload(); await A.p.waitForTimeout(700);
    check('«Вернуть» с другого устройства возвращает дело', (await ask(A.p)).includes(id0), `после reopen дело в списке устройства A: ${(await ask(A.p)).includes(id0)}`);
    // отмена: в окне 5 с ничего не уходит, дело на месте
    const before = inbox.length; const id1 = (await ask(A.p))[1];
    await A.p.click(`.rw[data-id="${id1}"] .chk`); await A.p.waitForTimeout(300); await A.p.click('#tu'); await A.p.waitForTimeout(5800);
    const back = (await ask(A.p)).includes(id1);
    await A.p.reload(); await A.p.waitForTimeout(700);
    check('«Отменить» не теряет дело и ничего не отправляет', back && (await ask(A.p)).includes(id1) && inbox.length === before, `дело на месте: ${back}, после перезагрузки: ${(await ask(A.p)).includes(id1)}, новых записей в inbox: ${inbox.length - before}`);
    await A.ctx.close(); await B.ctx.close();
  }

  // 3. Ошибка сервера отметок — нет ложного успеха, отметка не пропадает
  {
    inbox = []; apiMode = 'fail';
    const {ctx, p} = await device(D); await go(p);
    const id0 = (await ask(p))[0];
    await p.click(`.rw[data-id="${id0}"] .chk`); await p.waitForTimeout(5800);
    const toast = await p.textContent('#tt');
    await p.reload(); await p.waitForTimeout(700);
    const banner = await p.textContent('.warnline').catch(() => '');
    const fresh2 = await p.textContent('#fresh');
    check('Сбой отправки: нет «Отправлено», есть «Не дошло»', /Не дошло/.test(toast) && !/Отправлено/.test(toast), `тост: «${toast.trim()}»`);
    check('Неотправленная отметка сохранена и видна', /не отправлен/.test(banner), `плашка: «${banner.trim().slice(0, 90)}»`);
    check('Нет синхронизации — так и написано в шапке', /не загрузились/.test(fresh2), `шапка: «${fresh2.trim().slice(0, 120)}»`);
    if (SHOTS) await p.screenshot({path: path.join(SHOTS, 'd-sboy-otpravki.png')});
    apiMode = 'ok'; await ctx.close();
  }

  // 4. Деньги: одна сумма не считается дважды
  {
    const d = fresh(); const came = new Set(d.tasks.filter((t) => t.money === 'пришло').map((t) => t.client)); const c = d.tasks.find((t) => t.money === 'ждём' && t.amount && !came.has(t.client));
    d.tasks.push({...c, id: 'dup-came', money: 'пришло', title: c.title + ' (пришло)'});
    const {ctx, p} = await device(d); await go(p);
    const v = await p.$$eval('.stg', (x) => x.map((e) => [e.querySelector('.k').textContent, e.querySelector('.v').textContent]));
    const hint = await p.textContent('.strip').catch(() => '');
        const shown = v.find((x) => /Ждём оплату/.test(x[0]));
    const base0 = d.tasks.filter((t) => t.id !== 'dup-came');
    const {ctx: c0, p: p0} = await device({...d, tasks: base0}); await go(p0);
    const v0 = (await p0.$$eval('.stg', (x) => x.map((e) => e.querySelector('.k').textContent))).find((x) => /Ждём оплату/.test(x)); await c0.close();
    const n0 = +v0.split('· ')[1], n1 = +shown[0].split('· ')[1];
    check('Нет двойного учёта денег («ждём» = «пришло» у того же клиента)', n1 === n0 - 1 && hint.includes(c.client), `до дубля «Ждём оплату» ${n0} дел, с дублем ${n1}; пояснение называет клиента: ${hint.includes(c.client)}`);
    await ctx.close();
  }

  // 5. Нет данных / ошибка / устаревшие данные — без ложного нуля и ложного «разобрано»
  {
    const E = await device(500); await go(E.p);
    const t = await E.p.textContent('#view');
    check('Ошибка загрузки данных — сообщение, без нулей', /Данные не загрузились/.test(t) && !/0 ₽|Утро разобрано/.test(t), `экран: «${t.trim().slice(0, 80)}»`);
    if (SHOTS) await E.p.screenshot({path: path.join(SHOTS, 'd-oshibka-dannyh.png')});
    await E.ctx.close();
    const d = fresh(); d.tasks.forEach((t) => { t.money = ''; }); d.summary = [];
    const N = await device(d); await go(N.p);
    const h = await N.p.textContent('h1');
    check('Нет сумм и нет списка «Утра» — так и написано', /Сумм в пути в данных нет/.test(h) && /ещё не собран|ждут сверки/.test(h) && !/0 ₽|разобрано/.test(h), `заголовок: «${h.trim()}»`);
    await N.ctx.close();
    const o = fresh(); o.updated = '2026-01-05T09:30:00+03:00';
    const S = await device(o); await go(S.p);
    const w = await S.p.textContent('.warnline').catch(() => ''); const h2 = await S.p.textContent('h1');
    check('Старые данные — предупреждение со временем актуальности', /сегодня сводку ещё не выкладывали/.test(w) && !/разобрано/.test(h2), `плашка: «${w.trim()}»`);
    await S.ctx.close();
  }

  // 6. Время актуальности у очереди и «Автоматика» без фиктивных статусов
  {
    const {ctx, p} = await device(D); await go(p);
    const c = await p.textContent('section.sec .sh .c');
    const fr = await p.textContent('#fresh');
        check('У списка «Нужно от вас» указано, кем и когда проверено', /сверенное сегодня/.test(c) && /\d\d:\d\d/.test(c) && /Сборка \d\d:\d\d МСК/.test(fr), `«${c.trim()}»; шапка «${fr.trim().slice(0, 60)}»`);
    const autoTxt = await p.innerText('#view');
    check('«Автоматика» без строк — «нет данных проверки», без «всё работает»', /Автоматика: нет данных проверки/.test(autoTxt) && !/всё работает|0 сбоев/i.test(autoTxt), /нет данных проверки/.test(autoTxt) ? 'серая строка «Автоматика: нет данных проверки»' : 'строки нет');
    await ctx.close();
    const d = fresh(); d.summary.push({id: 'a1', section: 'Автоматика', flag: 'ok', text: 'Пинатель отработал', checked: '2026-10-28T13:40:00Z'}, {id: 'a2', section: 'Автоматика', flag: 'срочно', text: 'Утро: выкладка 17:00 не прошла', checked: '2026-10-28T14:05:00Z'});
    const A = await device(d); await go(A.p);
    const at = await A.p.textContent('body');
    const order = at.indexOf('выкладка 17:00 не прошла') < at.indexOf('Пинатель отработал');
    check('«Автоматика»: время проверки UTC → МСК, «срочно» наверху', /Пинатель отработал · проверено 28\.10 16:40/.test(at) && order, `16:40 МСК из 13:40 UTC: ${/проверено 28\.10 16:40/.test(at)}, «срочно» первым: ${order}`);
    await A.ctx.close();
  }

  // 6b. Без сверки за сегодня — не дело Ольги; деньги делятся на сверенные и нет; устаревание по расписанию
  {
    const d = fresh(); d.tasks.forEach((t) => { delete t.checked; });
    const {ctx, p} = await device(d); await go(p);
    const a = await ask(p), h = await p.textContent('h1'), b = await p.textContent('.buckets');
    check('Несверенные «готово Ольге» не выдаются за проверенные', a.length === 0 && /Ждут сверки/.test(b) && !/Утро разобрано/.test(h), `в «Нужно от вас» ${a.length}, заголовок «${h.trim().slice(0, 60)}»`);
    const st = await p.textContent('.stages');
    check('Деньги без сегодняшней сверки помечены', /сегодня не сверено/.test(st) && /сегодня не сверено/.test(h), `заголовок: «${h.trim().slice(0, 120)}»`);
    await ctx.close();
    const d2 = fresh(); const came2 = new Set(d2.tasks.filter((t) => t.money === 'пришло').map((t) => t.client)); const w = d2.tasks.find((t) => t.money === 'ждём' && t.amount && !came2.has(t.client)); verify(w);
    const B = await device(d2); await go(B.p);
    const h2 = await B.p.textContent('h1');
    check('Сверенная сумма показана отдельно от несверенной', /сверено сегодня/.test(h2) && /без сегодняшней сверки/.test(h2), `«${h2.trim().slice(0, 140)}»`);
    await B.ctx.close();
    const d3 = fresh(); d3.updated = iso(new Date(Date.now() - 26 * 36e5));
    const S = await device(d3); await go(S.p);
    const fr = await S.p.textContent('#fresh'), dot = await S.p.$eval('#fresh .dot', (e) => e.getAttribute('style'));
    check('Устаревшие данные: не зелёная точка и слово «Устарело»', /Устарело/.test(fr) && /warn/.test(dot), `шапка «${fr.trim().slice(0, 90)}»`);
    await S.ctx.close();
  }

  // 7. Скриншоты ПК и телефона на обезличенных данных + ошибки JS
  if (SHOTS) {
    const P = await device(D); await go(P.p); await P.p.screenshot({path: path.join(SHOTS, 'pk-utro.png'), fullPage: true});
    await go(P.p, '#money'); await P.p.screenshot({path: path.join(SHOTS, 'pk-dengi.png'), fullPage: true});
    await go(P.p, '#all'); await P.p.screenshot({path: path.join(SHOTS, 'pk-vse-dela.png')});
    const M = await device(D, {viewport: {width: 390, height: 844}, mobile: true}); await go(M.p); await M.p.screenshot({path: path.join(SHOTS, 'telefon-utro.png')});
    await M.p.click('[data-focus]'); await M.p.waitForTimeout(400); await M.p.screenshot({path: path.join(SHOTS, 'telefon-razbor.png')});
    const Dk = await device(D, {viewport: {width: 390, height: 844}, mobile: true, scheme: 'dark'}); await go(Dk.p, '#money'); await Dk.p.screenshot({path: path.join(SHOTS, 'telefon-dengi-temnaya.png')});
    const errs = [...P.p.errors, ...M.p.errors, ...Dk.p.errors];
    check('Ошибок JavaScript на страницах нет', !errs.length, errs.length ? errs.join('; ') : '0 ошибок');
    const ov = await M.p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    check('Телефон 390 px: нет прокрутки вбок', ov <= 0, `лишняя ширина ${ov} px`);
  }
} finally {
  await browser.close(); server.close();
  const pass = results.filter((r) => r.ok).length;
  console.log(`\nИтог: ${pass} из ${results.length} проверок пройдено`);
  fs.writeFileSync(path.join(ROOT, 'tests/last-run.json'), JSON.stringify({at: new Date().toISOString(), pass, total: results.length, results}, null, 1));
  process.exitCode = pass === results.length ? 0 : 1;
}
