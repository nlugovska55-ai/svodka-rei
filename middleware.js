// Закрывает весь сайт паролем (Basic Auth). Пароль задаёт Ольга в Vercel:
// Settings → Environment Variables → DASH_PASSWORD. Имя пользователя любое.
export const config = { matcher: '/:path*' };

export default function middleware(request) {
  const password = process.env.DASH_PASSWORD;
  if (!password) {
    return new Response('Пароль не задан: добавьте DASH_PASSWORD в настройках проекта Vercel.', {
      status: 503,
      headers: { 'content-type': 'text/plain; charset=utf-8' },
    });
  }
  const auth = request.headers.get('authorization') || '';
  if (auth.startsWith('Basic ')) {
    let decoded = '';
    try { decoded = new TextDecoder().decode(Uint8Array.from(atob(auth.slice(6)), (c) => c.charCodeAt(0))); } catch (e) { decoded = ''; }
    const given = decoded.slice(decoded.indexOf(':') + 1);
    if (given === password) return;
  }
  return new Response('Нужен пароль', {
    status: 401,
    headers: {
      'www-authenticate': 'Basic realm="Svodka RЕI", charset="UTF-8"',
      'content-type': 'text/plain; charset=utf-8',
    },
  });
}
