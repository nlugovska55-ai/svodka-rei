// Обязательная проверка навигации пульта перед публикацией (19.11.2026, Ольга: «Защита от повторной поломки»).
// Запускается в сборке Vercel (vercel.json → buildCommand): упала — выпуск не публикуется, на сайте остаётся прошлая версия.
// Что проверяет на ПК (1440×900) и телефоне (390×844), на настоящих data/svodka.json:
//  1) каждый обязательный пункт меню виден на главной: есть в разметке, не скрыт стилями, не перекрыт, в пределах экрана;
//  2) клик открывает свой раздел (адрес #раздел, вкладка выбрана), раздел не пустой и без ошибки, меню остаётся видимым;
//  3) после обновления страницы и при прямом открытии ссылки #раздел — тот же раздел с содержимым.
// Запуск: node tests/nav-gate.mjs [папка-сайта] [--mutate hide-buh|hide-garanty|break-garanty|empty-buh]
//   --mutate — самопроверка: портит копию пульта и ждёт, что проверка упадёт (показывает, что поломка блокирует выпуск).
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);

const args = process.argv.slice(2);
const mi = args.indexOf('--mutate'), MUTATE = mi >= 0 ? args.splice(mi, 2)[1] : '';
const ROOT = path.resolve(args[0] || path.join(path.dirname(new URL(import.meta.url).pathname), '..'));

// Обязательные разделы. phone: false — на телефоне пункт по замыслу убран из нижней панели (30.10), раздел открывается ссылками.
// 19.11 (Ольга, «свежий взгляд» п.5): в меню 5 пунктов; Клиенты Лены, Гаранты, Прогноз и Другие — переключателем внутри пункта (sub: родитель)
export const REQUIRED = [
  {tab: 'utro', label: 'Сегодня', phone: true},
  {tab: 'my', label: 'Клиенты', phone: true},
  {tab: 'lena', label: 'Клиенты Лены', phone: true, sub: 'my'},
  {tab: 'buh', label: 'Деньги', phone: true, must: /Бухгалтер|К оплате|Налог|1С/},
  {tab: 'garanty', label: 'Гаранты', phone: true, sub: 'buh', must: /гарант|таблиц[аы] Лены/i},
  {tab: 'money', label: 'Прогноз денег', phone: true, sub: 'buh'},
  {tab: 'all', label: 'Все дела', phone: true},
  {tab: 'other', label: 'Другие', phone: true, sub: 'all'},
  {tab: 'kontrol', label: 'Контроль', phone: false},
  {tab: 'grafik', label: 'Перечень дня', phone: true, link: true},
];

let html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
if (MUTATE === 'hide-buh') html = html.replace('</style>', '.tab[data-tab=buh]{visibility:hidden}</style>');
if (MUTATE === 'hide-garanty') html = html.replace('</style>', '@media (max-width:820px){.tab[data-tab=garanty]{display:none}}</style>');
if (MUTATE === 'break-garanty') html = html.replace("tab === 'garanty' ? garantyTab() :", '');
if (MUTATE === 'empty-buh') html = html.replace('tab === \'buh\' ? buhTab() :', "tab === 'buh' ? '' :");

const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]);
  if (u === '/' || u === '/index.html') { res.writeHead(200, {'content-type': 'text/html; charset=utf-8'}); return res.end(html); }
  if (u.startsWith('/api/')) { res.writeHead(200, {'content-type': 'application/json'}); return res.end(JSON.stringify({ok: true, items: [], nextCursor: null})); }
  const f = path.join(ROOT, u);
  if (!f.startsWith(ROOT) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'content-type': f.endsWith('.json') ? 'application/json' : 'application/octet-stream'}); fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const URL0 = `http://127.0.0.1:${server.address().port}/`;

// Браузер: в сборке Vercel — @sparticuz/chromium + playwright-core; локально — установленный playwright
async function launch() {
  try {
    const sp = (await import('@sparticuz/chromium')).default, {chromium} = await import('playwright-core');
    // --single-process: закрытие контекста гасит весь браузер (сборка 19.11 упала на newPage) — без него
    return await chromium.launch({executablePath: await sp.executablePath(), args: sp.args.filter((x) => !/^--(single-process|no-zygote)$/.test(x)), headless: true});
  } catch (e) {
    console.log('@sparticuz/chromium не запустился: ' + (e && e.stack || e) + ' | node ' + process.version + ' | VERCEL=' + (process.env.VERCEL || ''));
    const {chromium} = require('playwright');  // локально; в сборке Vercel его нет — тогда проверка падает и выпуск блокируется
    return chromium.launch();
  }
}

const fails = [], log = [];
const check = (vp, name, ok, fact) => { const s = `${ok ? 'PASS' : 'FAIL'} [${vp}] ${name} — ${fact}`; log.push(s); console.log(s); if (!ok) fails.push(s); };

// Пункт виден на экране: есть, ненулевой размер, в пределах окна, не прозрачный/не скрытый, точка в центре — сам пункт
const visible = (p, it) => p.evaluate(({tab, link, sub}) => {
  const el = document.querySelector(link ? `a.hlink[href="#${tab}"]` : sub ? `nav.subnav .subt[data-tab="${tab}"]` : `nav.tabs .tab[data-tab="${tab}"]`);
  if (!el) return 'нет в разметке';
  const r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return `нулевой размер ${Math.round(r.width)}×${Math.round(r.height)}`;
  if (r.right <= 0 || r.bottom <= 0 || r.left >= innerWidth || r.top >= innerHeight) return 'за пределами экрана';
  for (let e = el; e; e = e.parentElement) { const cs = getComputedStyle(e); if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.1) return `скрыт стилем (${e.tagName.toLowerCase()}.${e.className}: ${cs.display === 'none' ? 'display:none' : cs.visibility === 'hidden' ? 'visibility:hidden' : 'opacity'})`; }
  const x = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
  const top = document.elementFromPoint(x, y);
  if (!top || !(top === el || el.contains(top))) return 'перекрыт другим элементом: ' + (top ? top.tagName.toLowerCase() + '.' + top.className : 'ничего');
  return '';
}, it);

// Раздел открыт и не пустой
const opened = (p, it) => p.evaluate(({tab, must}) => {
  const v = document.querySelector('#view'), txt = (v && v.innerText || '').trim();
  if (document.body.dataset.tab !== tab) return `открыт раздел «${document.body.dataset.tab || 'нет'}», а не «${tab}»`;
  if (location.hash !== '#' + tab) return `адрес ${location.hash || '(пусто)'} вместо #${tab}`;
  if (/Раздел не показан|Данные не загрузились/.test(txt)) return 'ошибка раздела: ' + txt.slice(0, 120).replace(/\s+/g, ' ');
  if (txt.length < 60 || v.querySelector('.skel')) return `пустая страница (${txt.length} знаков)`;
  if (must && !new RegExp(must[0], must[1]).test(txt)) return 'нет содержимого раздела (' + must[0] + ')';
  return '';
}, {tab: it.tab, must: it.must ? [it.must.source, it.must.flags] : null});

for (const [vp, size] of [['ПК 1440', {width: 1440, height: 900}], ['телефон 390', {width: 390, height: 844}]]) {
  const browser = await launch();  // свой браузер на каждый экран
  const phone = size.width < 820, req = REQUIRED.filter((x) => !phone || x.phone);
  const ctx = await browser.newContext({viewport: size, isMobile: phone, hasTouch: phone});
  // внешние шрифты и логотип не грузим: без сети сборки событие load не наступает (GitHub Actions 19.11), на проверку меню они не влияют
  await ctx.route((u) => !u.href.startsWith(URL0), (r) => r.abort());
  const p = await ctx.newPage(), errs = []; p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(URL0, {waitUntil: 'domcontentloaded'}); await p.waitForFunction(() => document.body.dataset.tab, null, {timeout: 15000}).catch(() => {});
  for (const it of req.filter((x) => !x.sub)) { const v = await visible(p, it); check(vp, `«${it.label}» виден на главной`, !v, v || 'виден'); }
  for (const it of req) {
    const sel = it.link ? `a.hlink[href="#${it.tab}"]` : it.sub ? `nav.subnav .subt[data-tab="${it.tab}"]` : `nav.tabs .tab[data-tab="${it.tab}"]`;
    await p.goto(URL0 + '#' + (it.sub || 'utro'), {waitUntil: 'domcontentloaded'}); await p.waitForTimeout(250);
    if (it.sub) { const v = await visible(p, it); check(vp, `«${it.label}» виден в переключателе раздела`, !v, v || 'виден'); }
    try { await p.click(sel, {timeout: 3000}); } catch (e) { check(vp, `«${it.label}»: переход из меню`, false, 'не нажимается: ' + e.message.split('\n')[0]); continue; }
    await p.waitForTimeout(350);
    let o = await opened(p, it); check(vp, `«${it.label}»: переход из меню`, !o, o || 'раздел открыт, есть содержимое');
    const mv = (await Promise.all(req.filter((x) => !x.sub).map(async (x) => [x.label, await visible(p, x)]))).filter(([, r]) => r);
    check(vp, `«${it.label}»: меню видно после перехода`, !mv.length, mv.length ? mv.map(([l, r]) => l + ': ' + r).join('; ') : 'все пункты видны');
    await p.reload({waitUntil: 'domcontentloaded'}); await p.waitForTimeout(600);
    o = await opened(p, it); check(vp, `«${it.label}»: после обновления страницы`, !o, o || 'тот же раздел с содержимым');
    const p2 = await ctx.newPage(); p2.on('pageerror', (e) => errs.push(e.message));
    await p2.goto(URL0 + '#' + it.tab, {waitUntil: 'domcontentloaded'}); await p2.waitForTimeout(600);
    o = await opened(p2, it); check(vp, `«${it.label}»: прямая ссылка #${it.tab}`, !o, o || 'открывается');
    await p2.close();
  }
  // 19.11 (Ольга, Шторм п.3): дело, сверенное с источником за 3 дня, не помечается «не сверено»
  if (!phone) {
    const SV = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'svodka.json'), 'utf8'));
    const today = (SV.updated || '').slice(0, 10), d3 = new Date(Date.parse(today) - 3 * 864e5).toISOString().slice(0, 10);
    const freshIds = (SV.tasks || []).filter((t) => t.section === 'Мои клиенты' && String(t.sourceCheckedAt || '').slice(0, 10) >= d3).map((t) => t.id);
    await p.goto(URL0 + '#my', {waitUntil: 'domcontentloaded'}); await p.waitForTimeout(500);
    const bad = await p.evaluate((ids) => ids.filter((id) => [...document.querySelectorAll(`[data-id="${id}"]`)].some((el) => /не сверено|сверено \d+ дн назад/.test(el.textContent))), freshIds);
    check(vp, `«Мои клиенты»: сверенные дела без метки «не сверено» (${freshIds.length})`, !bad.length, bad.length ? 'с меткой: ' + bad.join(', ') : 'верно');
    // 19.11 (Шторм A1): «ждём денег» одной подписью на Клиентах Лены, Гарантах, Контроле и Бухгалтерии
    const labels = [];
    for (const t of ['lena', 'garanty', 'kontrol', 'buh']) { await p.goto(URL0 + '#' + t, {waitUntil: 'domcontentloaded'}); await p.waitForTimeout(400);
      labels.push(await p.evaluate(() => (/Ждём денег: [\d\s ]+₽, из них подтверждено клиентом [\d\s ]+₽(?: · ещё по делам, не в таблице Лены: [\d\s ]+₽)?/.exec(document.body.innerText) || [''])[0])); }
    check(vp, '«Ждём денег» одинаково на 4 вкладках', labels.every((l) => l && l === labels[0]), labels.join(' | ') || 'подписи нет');
  }
  check(vp, 'ошибок JavaScript нет', !errs.length, errs.length ? errs.slice(0, 3).join(' | ') : 'нет');
  await ctx.close(); await browser.close();
}
server.close();
const out = {at: new Date().toISOString(), mutate: MUTATE || null, passed: log.length - fails.length, failed: fails.length, fails};
try { fs.writeFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), 'nav-gate-last.json'), JSON.stringify({...out, log}, null, 1)); } catch (e) {}
console.log(fails.length ? `\nПРОВЕРКА НАВИГАЦИИ НЕ ПРОЙДЕНА: ${fails.length} из ${log.length}. Выпуск заблокирован.` : `\nПроверка навигации пройдена: ${log.length} из ${log.length}.`);
process.exit(fails.length ? 1 : 0);
