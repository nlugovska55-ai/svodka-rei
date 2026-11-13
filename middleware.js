// Закрывает весь сайт паролем через простую страницу входа. Вход запоминается на 30 дней (cookie).
// Пароль задаёт Ольга в Vercel: Settings → Environment Variables → DASH_PASSWORD.
// Пароль принимается и набранный не в той раскладке (ru ↔ en), и с лишними пробелами по краям.
export const config = { matcher: '/:path*' };

const COOKIE = 'svodka_auth';
const MONTH = 60 * 60 * 24 * 30;
const EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?";
const RU = 'ёйцукенгшщзхъфывапролджэячсмитьбю.ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,';
const swap = (s, from, to) => [...s].map((c) => { const i = from.indexOf(c); return i < 0 ? c : to[i]; }).join('');

async function token(password) {
  const data = new TextEncoder().encode('svodka-rei:' + password);
  const hash = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function loginPage(error) {
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>Сводка дня РЭИ — вход</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f5f1;color:#1f1e1b;font:16px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){body{background:#1a1917;color:#ecebe6}form{background:#23221f!important;border-color:#38362f!important}input{background:#1a1917!important;color:#ecebe6!important;border-color:#55524a!important}}
form{background:#fff;border:1px solid #e3e0d8;border-radius:12px;padding:24px;display:grid;gap:12px;width:min(340px,calc(100vw - 32px))}
h1{font:400 24px Georgia,serif;margin:0}label{font-size:14px}input{font:inherit;padding:10px;border:1px solid #c9c4b8;border-radius:8px}
button{font:inherit;padding:10px;border:0;border-radius:8px;background:#1f1e1b;color:#fff;cursor:pointer}.err{color:#b3261e;font-size:14px;margin:0}.row{display:flex;gap:6px;align-items:center;font-size:14px}</style></head>
<body><form method="post" action="/__login"><h1>Сводка дня</h1>${error ? '<p class="err">Пароль не подошёл. Проверьте и попробуйте ещё раз.</p>' : ''}
<label for="p">Пароль</label><input id="p" name="password" type="text" autocomplete="current-password" autofocus required>
<button type="submit">Войти</button><p class="row">Вход запомнится на 30 дней на этом компьютере.</p></form></body></html>`;
  return new Response(html, { status: 401, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } });
}

export default async function middleware(request) {
  const password = (process.env.DASH_PASSWORD || '').trim();
  if (!password) {
    return new Response('Пароль не задан: добавьте DASH_PASSWORD в настройках проекта Vercel.', {
      status: 503,
      headers: { 'content-type': 'text/plain; charset=utf-8' },
    });
  }
  const good = await token(password);
  const url = new URL(request.url);
  // Webhook Telegram проверяет свой секретный заголовок сам (api/tg.js)
  if (url.pathname === '/api/tg' && request.method === 'POST') return;

  if (url.pathname === '/__login' && request.method === 'POST') {
    const form = await request.formData();
    const given = String(form.get('password') || '').trim();
    const variants = [given, swap(given, RU, EN), swap(given, EN, RU)];
    for (const v of variants) {
      if ((await token(v)) === good) {
        return new Response(null, {
          status: 303,
          headers: {
            location: '/',
            'set-cookie': `${COOKIE}=${good}; Path=/; Max-Age=${MONTH}; HttpOnly; Secure; SameSite=Lax`,
          },
        });
      }
    }
    return loginPage(true);
  }

  const cookies = request.headers.get('cookie') || '';
  const have = cookies.split(/;\s*/).find((c) => c.startsWith(COOKIE + '='));
  if (have && have.slice(COOKIE.length + 1) === good) return;
  return loginPage(false);
}
