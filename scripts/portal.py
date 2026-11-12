import argparse
from contextlib import contextmanager
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from urllib.parse import parse_qs, urlsplit


class Portal(ThreadingHTTPServer):
    def __init__(self, port=0, conflict=False, fault_city=None):
        self.sessions = {}
        self.requests = []
        self.lock = threading.Lock()
        self.conflict = conflict
        self.fault_city = fault_city
        super().__init__(('127.0.0.1', port), Handler)

    @property
    def origin(self):
        return 'http://127.0.0.1:' + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, content, cookie=None):
        body = content.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if cookie:
            self.send_header('Set-Cookie', 'sid=' + cookie + '; Path=/; HttpOnly')
        self.end_headers()
        self.wfile.write(body)

    def page(self, session):
        state = secrets.token_hex(8)
        session['state'] = state
        city, page = session['city'], session['page']
        shared = '共享网点异值' if self.server.conflict and city == 'B' else '共享网点'
        rows = [('S1', shared), ('S2', '合成城甲')] if city == 'A' else [('S1', shared), ('S3', '合成城乙')]
        identity, label = rows[page]
        total = 3 if city == self.server.fault_city else 2
        options = ''.join('<option value="' + value + '"' + (' selected' if value == city else '') + '>' + value + '</option>' for value in ['A', 'B'])
        return '<form action="/directory" method="post"><input name="state" type="hidden" value="' + state + '"><input name="scope" value="retail"><input name="scope" value="service"><select name="city">' + options + '</select><input name="ignored" disabled value="x"><button name="action" value="city">切城</button><button name="action" value="next">下一页</button><button name="action" value="reserve">预约</button></form><table data-city="' + city + '" data-total="' + str(total) + '" data-last="' + str(page == 1).lower() + '"><tr data-channel="C1" data-store="' + identity + '"><td>' + escape(label) + '</td></tr></table>'

    def do_GET(self):
        with self.server.lock:
            self.server.requests.append({'method': 'GET', 'path': self.path})
            if self.path == '/__requests':
                self.reply(200, json.dumps(self.server.requests))
                return
            if urlsplit(self.path).path != '/directory':
                self.reply(404, 'unknown route')
                return
            sid = secrets.token_hex(12)
            session = {'city': 'A', 'page': 0}
            self.server.sessions[sid] = session
            self.reply(200, self.page(session), sid)

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 65536:
            self.reply(413, 'large form')
            return
        form = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
        with self.server.lock:
            self.server.requests.append({'method': 'POST', 'path': self.path, 'form': form})
            if self.path != '/directory':
                self.reply(404, 'unknown route')
                return
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            sid = cookie['sid'].value if 'sid' in cookie else ''
            session = self.server.sessions.get(sid)
            if session is None:
                self.reply(401, '<form action="/login"><input type="password" name="password"></form>')
                return
            if form.get('state') != [session['state']]:
                self.reply(409, 'expired state')
                return
            if form.get('scope') != ['retail', 'service'] or 'ignored' in form:
                self.reply(400, 'invalid controls')
                return
            action = form.get('action')
            if action not in [['city'], ['next']] or form.get('city') not in [['A'], ['B']]:
                self.reply(403, 'not an authorized collection action')
                return
            city = form['city'][0]
            if action == ['city']:
                session.update(city=city, page=0)
            elif city != session['city'] or session['page'] == 1:
                self.reply(409, 'invalid continuation')
                return
            else:
                session['page'] += 1
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
    args = parser.parse_args()
    with Portal(args.port, args.conflict, args.fault_city) as server:
        print(server.origin, flush=True)
        server.serve_forever()
