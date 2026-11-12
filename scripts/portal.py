import argparse
from contextlib import contextmanager
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit


def _suffix(index):
    return '甲乙丙丁'[index] if index < 4 else str(index + 1)


class Portal(ThreadingHTTPServer):
    """A synthetic two-partition directory with one-time form state.

    Options reproduce source failures without touching the network:

    - ``conflict``: the neighbouring city reports different content for the
      shared site C1/S1.
    - ``fault_city``: the city declares one more site than it lists.
    - ``stale_after``: after N accepted POSTs the one-time state is expired
      once, so the next POST returns 409.
    - ``repeat``: advancing a page returns the same listing again.
    - ``no_city_switch``: the form omits the city submit button.
    - ``login``: GET returns a login form instead of a listing.
    - ``redirect``: accepted POSTs redirect to a path outside the authorized
      scope before answering.
    """

    def __init__(self, port=0, conflict=False, fault_city=None, stale_after=None,
                 login=False, redirect=False, repeat=False, no_city_switch=False,
                 delay=0.0, cities=None):
        self.sessions = {}
        self.requests = []
        self.lock = threading.Lock()
        self.conflict = conflict
        self.fault_city = fault_city
        self.stale_after = stale_after
        self.login = login
        self.redirect = redirect
        self.repeat = repeat
        self.no_city_switch = no_city_switch
        self.delay = delay
        self.cities = list(cities or ['A', 'B'])
        self.post_count = 0
        self.expired_once = False
        super().__init__(('127.0.0.1', port), Handler)

    @property
    def origin(self):
        return 'http://127.0.0.1:' + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, content, cookie=None, location=None):
        body = content.encode()
        if self.server.requests:
            self.server.requests[-1]['status'] = status
        if self.server.delay:
            time.sleep(self.server.delay)
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            if location:
                self.send_header('Location', location)
            if cookie:
                self.send_header('Set-Cookie', 'sid=' + cookie + '; Path=/; HttpOnly')
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The demo deliberately kills the client mid-response.
            pass

    def login_page(self):
        return '<form action="/login"><input type="password" name="password"></form>'

    def rows(self, city):
        index = self.server.cities.index(city)
        shared = '共享网点异值' if self.server.conflict and index > 0 else '共享网点'
        return [('S1', shared), ('S' + str(index + 2), '合成城' + _suffix(index))]

    def page(self, session):
        state = secrets.token_hex(8)
        session['state'] = state
        city, page = session['city'], session['page']
        rows = self.rows(city)
        identity, label = rows[page]
        total = len(rows) + (1 if city == self.server.fault_city else 0)
        options = ''.join('<option value="' + value + '"' + (' selected' if value == city else '') + '>' + value + '</option>' for value in self.server.cities)
        switch = '' if self.server.no_city_switch else \
            '<button name="action" value="city">切城</button>'
        return ('<form action="/directory" method="post">'
                '<input name="state" type="hidden" value="' + state + '">'
                '<input name="scope" value="retail"><input name="scope" value="service">'
                '<select name="city">' + options + '</select>'
                '<input name="ignored" disabled value="x">'
                + switch +
                '<button name="action" value="next">下一页</button>'
                '<button name="action" value="reserve">预约</button></form>'
                '<table data-city="' + city + '" data-total="' + str(total) + '" data-last="'
                + str(page == len(rows) - 1).lower() + '">'
                '<tr data-channel="C1" data-store="' + identity + '"><td>' + escape(label) + '</td></tr>'
                '</table>')

    def do_GET(self):
        with self.server.lock:
            self.server.requests.append({'method': 'GET', 'path': self.path, 'url': self.path})
            if self.path == '/__requests':
                self.reply(200, json.dumps(self.server.requests))
                return
            if urlsplit(self.path).path != '/directory':
                self.reply(404, 'unknown route')
                return
            if self.server.login:
                self.reply(200, self.login_page())
                return
            sid = secrets.token_hex(12)
            session = {'city': 'A', 'page': 0, 'posts': 0}
            self.server.sessions[sid] = session
            self.reply(200, self.page(session), sid)

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 65536:
            self.reply(413, 'large form')
            return
        form = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
        with self.server.lock:
            self.server.requests.append({'method': 'POST', 'path': self.path,
                                         'url': self.path, 'form': form})
            if self.path != '/directory':
                self.reply(404, 'unknown route')
                return
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            sid = cookie['sid'].value if 'sid' in cookie else ''
            session = self.server.sessions.get(sid)
            if session is None:
                self.reply(401, self.login_page())
                return
            if form.get('state') != [session['state']]:
                self.reply(409, 'expired state')
                return
            if self.server.stale_after is not None and not self.server.expired_once \
                    and self.server.post_count >= self.server.stale_after:
                # Invalidate the outstanding one-time state once.  The client
                # must fetch a fresh page; the stale form must not be replayed.
                self.server.expired_once = True
                session['state'] = 'expired-' + secrets.token_hex(4)
                self.reply(409, 'expired state')
                return
            if form.get('scope') != ['retail', 'service'] or 'ignored' in form:
                self.reply(400, 'invalid controls')
                return
            action = form.get('action')
            city_values = form.get('city')
            if action not in [['city'], ['next']] or not city_values \
                    or len(city_values) != 1 or city_values[0] not in self.server.cities:
                self.reply(403, 'not an authorized collection action')
                return
            city = city_values[0]
            if action == ['city']:
                session.update(city=city, page=0)
            elif city != session['city'] or session['page'] >= len(self.rows(city)) - 1:
                self.reply(409, 'invalid continuation')
                return
            elif not self.server.repeat:
                session['page'] += 1
            session['posts'] += 1
            self.server.post_count += 1
            if self.server.redirect:
                self.reply(302, '', location='/logout?next=/directory')
                return
            self.reply(200, self.page(session))


@contextmanager
def running(**kwargs):
    server = Portal(**kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--conflict', action='store_true')
    parser.add_argument('--fault-city', choices=['A', 'B'])
    parser.add_argument('--stale-after', type=int)
    parser.add_argument('--repeat', action='store_true')
    parser.add_argument('--no-city-switch', action='store_true')
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--redirect', action='store_true')
    parser.add_argument('--delay', type=float, default=0.0)
    parser.add_argument('--cities', default='A,B')
    args = parser.parse_args()
    with Portal(args.port, args.conflict, args.fault_city, args.stale_after,
                args.login, args.redirect, args.repeat, args.no_city_switch,
                args.delay, args.cities.split(',')) as server:
        print(server.origin, flush=True)
        server.serve_forever()
