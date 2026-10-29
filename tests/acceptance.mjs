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
let inbox = [], apiMode = 'ok'; const batches = new Set();
async function device(data, opts = {}) {
  const ctx = await browser.newContext({viewport: opts.viewport || {width: 1280, height: 1000}, colorScheme: opts.scheme || 'light', isMobile: !!opts.mobile, hasTouch: !!opts.mobile});
  const p = await ctx.newPage(); p.errors = []; p.on('pageerror', (e) => p.errors.push(e.message));
  if (opts.clock) await p.clock.install({time: opts.clock});
  p.dataHits = 0;
  await p.route('**/data/svodka.json*', (r) => { p.dataHits++; const v = typeof data === 'function' ? data() : data; return typeof v === 'number' ? r.fulfill({status: v, body: 'err'}) : r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(v)}); });
  await p.route('**/api/comments*', async (r) => {
    if (apiMode === 'fail') return r.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({ok: false, error: 'Не задан GITHUB_TOKEN в настройках Vercel'})});
    if (r.request().method() === 'POST') { const b = JSON.parse(r.request().postData()); if (!batches.has(b.batchId)) { batches.add(b.batchId); inbox.push(...b.items); } return r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({ok: true, count: b.items.length})}); }
    // страницы по 2 отметки, чтобы проверить, что пульт дочитывает все страницы
    const cur = +(new URL(r.request().url()).searchParams.get('cursor') || 0), page = inbox.slice(cur, cur + 2);
    return r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({ok: true, items: page.map((x) => ({taskId: x.taskId, action: x.action, at: x.at, eid: x.eid})), nextCursor: cur + 2 < inbox.length ? String(cur + 2) : null})});
  });
  return {ctx, p};
}
// «Я выполнила» — только через подтверждение смысла
const markDone = async (p, id) => { await p.click(`.rw[data-id="${id}"] [data-act="done"]`); await p.waitForSelector('#cf-yes'); await p.click('#cf-yes'); };
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
    await markDone(A.p, id0); await A.p.waitForTimeout(300);
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
    await markDone(A.p, id1); await A.p.waitForTimeout(300); await A.p.click('#tu'); await A.p.waitForTimeout(5800);
    const back = (await ask(A.p)).includes(id1);
    await A.p.reload(); await A.p.waitForTimeout(700);
    check('«Отменить» не теряет дело и ничего не отправляет', back && (await ask(A.p)).includes(id1) && inbox.length === before, `дело на месте: ${back}, после перезагрузки: ${(await ask(A.p)).includes(id1)}, новых записей в inbox: ${inbox.length - before}`);
    await A.ctx.close(); await B.ctx.close();
  }

  // 2b. Codex (3): сборка позже отметки не подтверждает её; подтверждает только id события из разбора
  {
    inbox = []; apiMode = 'ok';
    const A = await device(D); await go(A.p);
    const id0 = (await ask(A.p))[0];
    await markDone(A.p, id0); await A.p.waitForTimeout(5800);
    const ev = inbox.find((x) => x.taskId === id0);
    const later = fresh(); later.updated = iso(new Date(Date.now() + 60e3));  // «сборка 18:00» после «done 17:59», db не менялась
    const B = await device(later); await go(B.p);
    const hiddenB = !(await ask(B.p)).includes(id0);
    check('Сборка позже отметки из неизменённой db не возвращает дело', !!ev?.eid && hiddenB, `id события ${ev?.eid || 'нет'}; на втором устройстве дело скрыто: ${hiddenB}`);
    const acked = fresh(); acked.updated = later.updated; acked.appliedEvents = [ev.eid];
    const C = await device(acked); await go(C.p);
    const shownC = (await ask(C.p)).includes(id0);
    await A.p.reload(); await A.p.waitForTimeout(700);
    await A.p.route('**/data/svodka.json*', (r) => r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(acked)}));
    await A.p.reload(); await A.p.waitForTimeout(700);
    const pendA = await A.p.evaluate((id) => !!JSON.parse(localStorage.getItem('svodka-pending') || '{}')[id], id0);
    check('Отметка снимается только по подтверждённому id события (appliedEvents)', shownC && !pendA, `после подтверждения устройство C видит состояние db: ${shownC}; на A отметка снята: ${!pendA}`);
    await A.ctx.close(); await B.ctx.close(); await C.ctx.close();
  }

  // 3. Ошибка сервера отметок — нет ложного успеха, отметка не пропадает
  {
    inbox = []; apiMode = 'fail';
    const {ctx, p} = await device(D); await go(p);
    const id0 = (await ask(p))[0];
    await markDone(p, id0); await p.waitForTimeout(5800);
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
    c.invoice = 'счёт-77'; d.tasks.push({...c, id: 'dup-came', money: 'пришло', title: c.title + ' (пришло)', invoice: 'счёт-77'});
    const {ctx, p} = await device(d); await go(p);
    const v = await p.$$eval('.stg', (x) => x.map((e) => [e.querySelector('.k').textContent, e.querySelector('.v').textContent]));
    const hint = await p.textContent('.strip').catch(() => '');
        const shown = v.find((x) => /Ждём оплату/.test(x[0]));
    const base0 = d.tasks.filter((t) => t.id !== 'dup-came');
    const {ctx: c0, p: p0} = await device({...d, tasks: base0}); await go(p0);
    const v0 = (await p0.$$eval('.stg', (x) => x.map((e) => e.querySelector('.k').textContent))).find((x) => /Ждём оплату/.test(x)); await c0.close();
    const n0 = +v0.split('· ')[1], n1 = +shown[0].split('· ')[1];
    check('Нет двойного учёта денег (одна операция по явному id счёта)', n1 === n0 - 1 && hint.includes(c.client), `до дубля «Ждём оплату» ${n0} дел, с дублем ${n1}; пояснение называет клиента: ${hint.includes(c.client)}`);
    await ctx.close();
  }

  // 4b. Codex (1): без явного id суммы не вычитаются
  {
    const d = fresh(); const base = d.tasks.find((t) => t.money === 'ждём' && t.amount);
    d.tasks = d.tasks.filter((t) => !['пришло', 'ждём'].includes(t.money));
    d.tasks.push({...base, id: 'alfa', client: 'ООО Альфа', money: 'пришло', amount: 10000, invoice: ''}, {...base, id: 'beta', client: 'ООО Бета', money: 'ждём', amount: 10000, invoice: ''});
    const {ctx, p} = await device(d); await go(p);
    const stg = await p.$$eval('.stg', (x) => x.map((e) => e.querySelector('.k').textContent));
    const n = +(stg.find((x) => /Ждём оплату/.test(x)) || '· 0').split('· ')[1];
    check('ООО Альфа «пришло» 10 000 и ООО Бета «ждём» 10 000 — ожидаемых 1, не 0', n === 1, `«Ждём оплату» ${n}`);
    await ctx.close();
    const d2 = structuredClone(d); d2.tasks.find((t) => t.id === 'alfa').client = 'ООО Бета';
    const B = await device(d2); await go(B.p);
    const stg2 = await B.p.$$eval('.stg', (x) => x.map((e) => e.querySelector('.k').textContent));
    const n2 = +(stg2.find((x) => /Ждём оплату/.test(x)) || '· 0').split('· ')[1], hint = await B.p.textContent('.strip');
    check('Тот же клиент и сумма без id — обе суммы остаются, пометка «возможный дубль»', n2 === 1 && /Возможный дубль/.test(hint) && !/Не считаю дважды/.test(hint), `«Ждём оплату» ${n2}; пометка: ${/Возможный дубль/.test(hint)}`);
    await B.ctx.close();
  }

  // 4d. Справочная (погашенная) сумма: amountKind=reference не попадает в метки и итоги
  {
    const d = fresh(); const s0 = d.summary.find((x) => x.section === 'Нужно от вас'); const tk = d.tasks.find((t) => t.id === s0.id);
    const base = await device(structuredClone(d)); await go(base.p); const h0 = await base.p.textContent('h1'); await base.ctx.close();
    Object.assign(tk, {amount: 176103.19, amountKind: 'reference', amountNote: 'погашено, справочно', money: '', pay: 0});
    const {ctx, p} = await device(d); await go(p);
    const rowAm = await p.$eval(`.rw[data-id="${tk.id}"] .am`, (e) => e.textContent.trim()).catch(() => '?');
    const h1 = await p.textContent('h1');
    await p.click(`.rw[data-id="${tk.id}"] [data-open]`); await p.waitForTimeout(400); const dr = await p.textContent('#db');
    check('Справочная сумма (amountKind=reference) не в метке строки и не в итогах', rowAm === '' && h1 === h0 && /справочно 176/.test(dr), `метка строки «${rowAm}», заголовок не изменился: ${h1 === h0}, в карточке «справочно»: ${/справочно 176/.test(dr)}`);
    await ctx.close();
  }

  // 4c. Codex (2): дата сверки с годом и ISO; прошлое и будущее не «сегодня»
  {
    const z = new Date(Date.now() + 3 * 36e5), Y = z.getUTCFullYear(), dd = String(z.getUTCDate()).padStart(2, '0'), mm = String(z.getUTCMonth() + 1).padStart(2, '0');
    const tm = new Date(Date.now() + 27 * 36e5), tdd = String(tm.getUTCDate()).padStart(2, '0'), tmm = String(tm.getUTCMonth() + 1).padStart(2, '0');
    const cases = [[`${dd}.${mm}.${Y - 1} Утро`, false], [`${dd}.${mm}.${Y} Утро`, true], [`${Y}-${mm}-${dd}`, true], [new Date().toISOString(), true], [`${Y - 1}-${mm}-${dd}T10:00:00Z`, false], [`${tdd}.${tmm}.${tm.getUTCFullYear()} Утро`, false], [`${dd}.${mm} Утро`, true], ['вчера', false]];
    const res = [];
    for (const [c, want] of cases) {
      const d = fresh(); const s0 = d.summary.find((s) => s.section === 'Нужно от вас'); const tk = d.tasks.find((t) => t.id === s0.id);
      d.summary = d.summary.filter((s) => s.section !== 'Нужно от вас' || s.id === s0.id); delete s0.checked; tk.checked = c;
      const {ctx, p} = await device(d); await go(p); const got = (await ask(p)).length > 0; await ctx.close();
      res.push(`${c.slice(0, 22)}→${got ? 'сегодня' : 'нет'}${got === want ? '' : ' (ОШИБКА)'}`);
    }
    check('checked прошлого года, будущего и мусор — не «сегодня»; с годом и ISO — да', !res.some((x) => /ОШИБКА/.test(x)), res.join('; '));
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

  // 8. Автообновление: каждые 10 минут, черновик и окно отмены не сбрасываются, московская дата пересчитывается
  {
    inbox = []; apiMode = 'ok';
    let cur = fresh();
    const z = new Date(Date.now() + 3 * 36e5), start = new Date(Date.UTC(z.getUTCFullYear(), z.getUTCMonth(), z.getUTCDate(), 15, 0));  // 18:00 МСК сегодня
    const {ctx, p} = await device(() => cur, {clock: start}); await go(p);
    const before = p.dataHits, id0 = (await ask(p))[0];
    const nd = fresh(); nd.updated = iso(new Date(start.getTime() + 5 * 6e4)); const t0 = nd.tasks.find((t) => t.id === id0); t0.status = 'done'; nd.tasks = nd.tasks.filter((t) => t.id !== id0); cur = nd;
    await p.clock.fastForward('10:05'); await p.waitForTimeout(800);
    const after = await ask(p);
    check('Вкладка сама перечитывает данные через 10 минут', p.dataHits > before && !after.includes(id0), `загрузок данных: было ${before}, стало ${p.dataHits}; новое состояние на экране: ${!after.includes(id0)}`);
    // черновик в карточке
    await p.click(`.rw [data-open]`); await p.waitForTimeout(400); await p.fill('#aiText', 'черновик Ольги');
    const hits = p.dataHits; await p.clock.fastForward('10:05'); await p.waitForTimeout(800);
    const draft = await p.inputValue('#aiText').catch(() => ''), drawerOn = await p.$eval('#drawer', (e) => e.classList.contains('on'));
    check('Автообновление не сбрасывает открытую карточку и черновик', draft === 'черновик Ольги' && drawerOn && p.dataHits > hits, `черновик «${draft}», карточка открыта: ${drawerOn}, загрузка была: ${p.dataHits > hits}`);
    await p.fill('#aiText', ''); await p.keyboard.press('Escape'); await p.waitForTimeout(300);
    // окно «Отменить»
    const id1 = (await ask(p))[0], sentBefore = inbox.length;
    await markDone(p, id1); await p.waitForTimeout(200);
    await p.evaluate(() => window.__svodkaRefresh()); await p.waitForTimeout(500);
    await p.click('#tu'); await p.clock.fastForward('00:06'); await p.waitForTimeout(500);
    check('Обновление во время окна «Отменить» не мешает отмене', (await ask(p)).includes(id1) && inbox.length === sentBefore, `дело на месте: ${(await ask(p)).includes(id1)}, отправлено лишнего: ${inbox.length - sentBefore}`);
    await ctx.close();
    // смена московской даты у оставленной вкладки
    const late = new Date(Date.UTC(z.getUTCFullYear(), z.getUTCMonth(), z.getUTCDate(), 20, 58));  // 23:58 МСК
    const dd = fresh(); dd.updated = iso(new Date(late.getTime() - 6 * 36e5));
    const R2 = await device(dd, {clock: late}); await go(R2.p);
    const w0 = await R2.p.textContent('.warnline').catch(() => '');
    await R2.p.clock.fastForward('03:00'); await R2.p.waitForTimeout(800);
    const w1 = await R2.p.textContent('.warnline').catch(() => '');
    check('В полночь по Москве вкладка пересчитывает дату и предупреждает об устаревании', !/не выкладывали/.test(w0) && /не выкладывали/.test(w1), `до полуночи: «${w0.trim().slice(0, 40)}», после: «${w1.trim().slice(0, 70)}»`);
    await R2.ctx.close();
  }

  // 9. Новая сборка без подтверждений: на устройстве, где отмечали, и на чистом — одно и то же
  {
    inbox = []; apiMode = 'ok';
    const A = await device(D); await go(A.p);
    const [i0, i1] = await ask(A.p);
    await markDone(A.p, i0); await A.p.waitForTimeout(5800);
    const nb = fresh(); nb.updated = iso(new Date(Date.now() + 60e3));
    await A.p.unroute('**/data/svodka.json*'); await A.p.route('**/data/svodka.json*', (r) => r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(nb)}));
    await go(A.p, '#utro'); await A.p.reload(); await A.p.waitForTimeout(800);
    const B = await device(nb); await go(B.p);
    const a = await ask(A.p), b = await ask(B.p);
    await go(A.p, '#all'); await go(B.p, '#all');
    const ca = await A.p.$$eval('.rw', (x) => x.length), cb = await B.p.$$eval('.rw', (x) => x.length);
    check('После новой сборки A и чистый B показывают одинаково', JSON.stringify(a) === JSON.stringify(b) && ca === cb && !a.includes(i0) && a.includes(i1), `«Нужно от вас»: A ${a.length}, B ${b.length}; «Все дела»: A ${ca}, B ${cb}`);
    await A.ctx.close(); await B.ctx.close();
  }

  // 10. Codex 19:44 / Ольга: «Поручить Claude» — отдельный тип; дело не закрывается, видно ожидание, после применения ведёт Claude
  {
    inbox = []; apiMode = 'ok';
    const A = await device(D); await go(A.p);
    const id0 = (await ask(A.p))[0];
    await A.p.click(`.rw[data-id="${id0}"] [data-act="claude"]`); await A.p.waitForSelector('#shText');
    if (SHOTS) await A.p.screenshot({path: path.join(SHOTS, 'd-poruchit-claude.png')});
    await A.p.fill('#shText', 'сверить акт'); await A.p.click('#cf-yes'); await A.p.waitForTimeout(5800);
    const ev = inbox.find((x) => x.taskId === id0);
    await go(A.p, '#all'); await A.p.fill('#q', ''); const tag = await A.p.$eval(`.rw[data-id="${id0}"]`, (e) => e.textContent).catch(() => 'нет строки');
    await go(A.p, '#utro'); const jr = await A.p.textContent('#journal').catch(() => '');
    check('Поручить Claude: в inbox action=claude, дело не закрыто и видно «ожидает разбора»', ev?.action === 'claude' && !!ev.eid && /ожидает разбора/.test(tag) && !(await ask(A.p)).includes(id0),
      `действие «${ev?.action}», строка: «${tag.replace(/\s+/g, ' ').trim().slice(0, 90)}»`);
    check('Журнал: поручение «принято, ждёт разбора»', /Поручено Claude/.test(jr) && /принято, ждёт разбора/.test(jr), `«${jr.replace(/\s+/g, ' ').trim().slice(0, 120)}»`);
    const ap = fresh(); const tk = ap.tasks.find((t) => t.id === id0); Object.assign(tk, {status: 'open', waiting: 'Claude', delegated: ev.eid}); ap.appliedEvents = [ev.eid];
    await A.p.unroute('**/data/svodka.json*'); await A.p.route('**/data/svodka.json*', (r) => r.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(ap)}));
    await A.p.reload(); await A.p.waitForTimeout(800);
    const jr2 = await A.p.textContent('#journal').catch(() => '');
    await go(A.p, '#all'); await A.p.click('[data-who="Claude"]'); await A.p.waitForTimeout(200);
    const row2 = await A.p.$eval(`.rw[data-id="${id0}"]`, (e) => e.textContent).catch(() => 'нет строки');
    check('После применения в db: дело открыто, ведёт Claude, журнал «применено»', /в работе у Claude/.test(row2) && /применено: в работе у Claude/.test(jr2) && !(await ask(A.p)).includes(id0),
      `в фильтре «Claude»: «${row2.replace(/\s+/g, ' ').trim().slice(0, 80)}»; журнал: ${/применено/.test(jr2)}`);
    await A.ctx.close();
  }
  {
    inbox = []; apiMode = 'ok';
    const A = await device(D); await go(A.p);
    const id0 = (await ask(A.p))[0];
    await A.p.click(`.rw[data-id="${id0}"] [data-act="done"]`); await A.p.waitForSelector('#cf-yes');
    const q1 = await A.p.textContent('.sh-in');
    if (SHOTS) await A.p.screenshot({path: path.join(SHOTS, 'd-podtverzhdenie.png')});
    await A.p.click('[data-sh="no"]'); await A.p.waitForTimeout(5800);
    const jl = await A.p.$$eval('#journal .jl', (x) => x.length).catch(() => 0);
    check('«Я выполнила» спрашивает подтверждение; «Отмена» — ни одного события', /Вы сами выполнили/.test(q1) && /Поручить Claude/.test(q1) && inbox.length === 0 && (await ask(A.p)).includes(id0) && jl === 0,
      `вопрос: «${q1.trim().slice(0, 40)}…», событий в inbox ${inbox.length}, записей в журнале ${jl}`);
    await markDone(A.p, id0); await A.p.waitForTimeout(300);
    await A.p.click('#journal [data-undo]'); await A.p.waitForTimeout(5800);
    check('Отмена из журнала до отправки: событий нет, дело на месте', inbox.length === 0 && (await ask(A.p)).includes(id0), `событий ${inbox.length}`);
    await markDone(A.p, id0); await A.p.waitForTimeout(5800);
    const sent = inbox.length; await A.p.click('#journal [data-undo]'); await A.p.waitForTimeout(5800);
    const kinds = inbox.map((x) => x.action).join(', ');
    check('«Вернуть» из журнала после отправки: одно событие reopen, дело снова в списке', sent === 1 && inbox.length === 2 && inbox[1].action === 'reopen' && (await ask(A.p)).includes(id0), `в inbox: ${kinds}`);
    await A.ctx.close();
  }

  // 11. Раздел «Бухгалтерия»: план ≠ факт ≠ справочно, чего нет — названо, нули не выдумываются
  {
    const z = new Date(Date.now() + 3 * 36e5), ymd = (n) => new Date(z.getTime() + n * 864e5).toISOString().slice(0, 10);
    const d = fresh();
    d.spisaniya = {built: 'тест', note: '', rows: [
      {date: ymd(0), who: 'РЭИ', what: 'ЕНП УСН', amount: 28314, state: 'поступило на ЕНС, подтверждено ФНС', src: 'ФНС Операции ЕНП', paid: true, paidSrc: 'fakty', kind: 'plan', confirmedBy: 'ФНС', paidOn: ymd(-3), plan: ymd(0)},
      {date: ymd(0), who: 'ИП', what: 'ЕНП ИП', amount: 101095.42, state: 'не подтверждено', src: 'тест', paid: true, paidSrc: 'fakty', kind: 'plan', confirmedBy: '', bankCheck: 'в Точке платежа нет'},
      {date: ymd(3), who: 'РЭИ', what: 'Бухуслуги', amount: 20000, state: 'после счёта', src: 'тест', paid: false, paidSrc: '', kind: 'plan'},
      {date: ymd(3), who: 'РЭИ', what: 'Ориентир ПО', amount: 10000, state: 'ориентир', src: 'тест', paid: false, paidSrc: '', kind: 'reference'},
      {date: ymd(14), who: 'РЭИ', what: 'Аренда', amount: 6000, state: 'уточнить', src: 'тест', paid: false, paidSrc: '', kind: 'plan'},
      {date: ymd(2), who: 'РЭИ', what: 'Доставка', amount: null, amountRange: [1000, 6000], state: 'ориентир', src: 'тест', paid: false, kind: 'reference'},
      {date: ymd(2), who: 'РЭИ', what: 'Телефония', amount: 0, zeroMeans: 'списания нет', state: 'переплата', src: 'тест', paid: false, kind: 'reference'}]};
    d.buh_plan = {built: 'тест', balance: {known: false, why: 'выгрузок нет, остаток не выводим'}, regular: [{name: 'Аренда офиса', org: 'Луг', period: 'ежемесячно', amount: 78268, amountKnown: true, note: 'оплачено по январь', kind: 'plan', dataAsOf: '16.10.2026'}]};
    d.buh_sources = [{name: 'Реестр списаний (Бухгалтерия)', ok: true, note: 'от тест'}, {name: 'Выписки банков (Альфа, Точка)', ok: false, note: 'в сводку не загружаются: остатков и фактических списаний по банку нет'}];
    d.buh_svodka = {file: 'buh-svodka-test.md', title: 'Бухгалтерия: сводка', sections: [{title: 'В работе', lines: ['строка плана']}]};
    const {ctx, p} = await device(d); await go(p, '#buh');
    const v7 = await p.innerText('#view');
    await p.click('[data-cal="30"]'); await p.waitForTimeout(200); const v30 = await p.innerText('#view');
    check('Бухгалтерия: без выписок — «Факт по банку не получен», остаток не считается', /✕ Выписки банков/.test(v7) && /Факт по банку\s*не получен/.test(v7) && /не считаю/.test(v7), 'строки «✕ Выписки банков», «Факт по банку не получен», «Остаток… не считаю»');
    check('Списания: к оплате / подтверждено ФНС (с датой поступления и планом) / со слов Ольги / справочно — раздельно', /К оплате · 26\s000\s₽/.test(v7) && /Оплачено, подтверждено ФНС · 28\s314/.test(v7) && /поступило \d\d\.\d\d, план был/.test(v7) && /Со слов Ольги, банком и ФНС не подтверждено · 101\s095,42/.test(v7) && /в Точке платежа нет/.test(v7) && /Справочно, в итоги не входит/.test(v7), (v7.match(/К оплате · [^\n]*/) || ['нет'])[0]);
    check('Неизвестная сумма — диапазон, настоящий ноль — «списания нет», не «0 ₽»; остаток с причиной', /1\s000–6\s000 ₽, по счёту/.test(v7) && /списания нет/.test(v7) && !/\b0 ₽/.test(v7) && /Аренда офиса/.test(v7), 'диапазон, «списания нет», регулярные платежи и причина без остатка');
    check('Календарь 7/30 дней: аренда через 14 дней только в 30', !/Аренда/.test(v7.split('План-факт')[0]) && /Аренда/.test(v30.split('План-факт')[0]), 'аренда в 7 дн: нет, в 30 дн: да');
    if (SHOTS) await p.screenshot({path: path.join(SHOTS, 'pk-buhgalteriya.png'), fullPage: true});
    await ctx.close();
    const sn = structuredClone(d); sn.buh_plan.balance = {known: true, note: 'только два счёта', accounts: [{account: 'Альфа РЭИ *7370', date: '29.10.2026', amount: 443420.32, src: 'выписка Альфы'}]};
    const SN = await device(sn); await go(SN.p, '#buh'); const vs = await SN.p.innerText('#view'); await SN.ctx.close();
    check('Остаток показан как снимок на дату, прогноза нет', /Остаток Альфа РЭИ \*7370\s*443\s420,32\s₽\s*снимок на 29\.10\.2026, не текущий остаток/.test(vs) && /Прогноз остатка\s*не считаю/.test(vs), (vs.match(/Остаток Альфа[^\n]*\n[^\n]*\n[^\n]*/) || ['нет'])[0].replace(/\n/g, ' | '));
    check('Без снимка остатка — «не считаю» с причиной', /выгрузок нет, остаток не выводим/.test(v7), 'причина показана');
    const bk = structuredClone(d); bk.bank = {built: 'тест', note: '', days: [{account: 'Альфа РЭИ *7370', date: ymd(0), open: 320421.48, in: 260000, out: 137001.16, close: 443420.32, partial: '', inItems: [{who: 'УКС', amount: 200000, basis: 'п/п 4866'}], outItems: [], src: 'выписка'}]};
    bk.buh_sources = [{name: 'Выписки банков', ok: true, partial: true, note: 'только Альфа РЭИ *7370 за 28.10. Прочие счета и дни не охвачены'}];
    const ttk = bk.tasks.find((t) => t.money === 'ждём подпись' && t.amount) || bk.tasks.find((t) => t.amount); Object.assign(ttk, {money: 'ждём подпись', due: ymd(1).split('-').reverse().join('.'), status: 'open'});
    const BKp = await device(bk); await go(BKp.p, '#buh'); const vb = await BKp.p.innerText('#view'); await BKp.ctx.close();
    check('Банк: обороты только по охваченному счёту и дню, без «полного факта»', /Факт по банку · Альфа РЭИ \*7370\s*\+260\s000\s₽\s*списано 137\s001,16\s₽ · только за \d\d\.\d\d, не полный факт месяца/.test(vb) && /Банк: обороты по прочитанным выпискам/.test(vb) && /конец 443\s420,32/.test(vb) && !/Факт по банку\s*не получен/.test(vb), (vb.match(/Факт по банку · [^\n]*\n[^\n]*\n[^\n]*/) || ['нет'])[0].replace(/\n/g, ' | '));
    check('Срок по подписи/счёту — «срок контроля», не «ждём поступление»', /срок: контроль подписи/.test(vb) && /срок контроля задачи, не дата оплаты/.test(vb) && !/ждём поступление/.test(vb), 'метка «срок: контроль подписи»');
    const n = fresh(); delete n.spisaniya; delete n.buh_sources; delete n.buh_svodka;
    const N = await device(n); await go(N.p, '#buh'); const vn = await N.p.innerText('#view');
    check('Без реестра списаний: «не получен», нулей нет', /Реестр списаний не получен/.test(vn) && /не получен/.test(vn) && !/\b0 ₽/.test(vn), `«${(vn.match(/Реестр списаний не получен[^\n]*/) || ['нет'])[0]}»`);
    await N.ctx.close();
    const M = await device(d, {viewport: {width: 390, height: 844}, mobile: true}); await go(M.p, '#buh');
    const ov = await M.p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    const rh = await M.p.$eval('.rw', (e) => e.getBoundingClientRect().height).catch(() => 0);
    check('Телефон 390 px, «Бухгалтерия»: нет прокрутки вбок, строка с двумя кнопками не выше 90 px', ov <= 0 && rh > 0 && rh <= 90, `лишняя ширина ${ov} px, высота строки ${Math.round(rh)} px`);
    if (SHOTS) await M.p.screenshot({path: path.join(SHOTS, 'telefon-buhgalteriya.png'), fullPage: true});
    await go(M.p, '#utro'); const bt = await M.p.$$eval('.rw .abs', (x) => x.map((e) => [...e.querySelectorAll('button')].map((b) => b.textContent.trim()).join(' | ')));
    const ov2 = await M.p.evaluate(() => document.documentElement.scrollWidth - innerWidth), rh2 = await M.p.$eval('.rw', (e) => e.getBoundingClientRect().height).catch(() => 0);
    check('Кнопки с глаголом: «Я выполнила» и «Поручить Claude», 390 px без прокрутки вбок', bt.length > 0 && bt.every((x) => x === 'Я выполнила | Поручить Claude') && ov2 <= 0 && rh2 <= 96, `кнопки «${bt[0]}», лишняя ширина ${ov2} px, высота строки ${Math.round(rh2)} px`);
    check('Ошибок JavaScript в «Бухгалтерии» нет', !M.p.errors.length && !N.p.errors.length, [...M.p.errors, ...N.p.errors].join('; ') || '0 ошибок');
    await M.ctx.close();
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
