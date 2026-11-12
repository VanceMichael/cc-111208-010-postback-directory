from http.cookies import CookieError, SimpleCookie
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


class ScopeError(ValueError):
    """A request or redirect would leave the authorized origin/path scope."""


def capture(url, method='GET', data=None, headers=None, maximum=4194304):
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('The inspection transport accepts loopback HTTP only')
    if parsed.username or parsed.password:
        raise ValueError('Credentials in URLs are not accepted')
    request = Request(url, data=data, method=method, headers=headers or {})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(maximum + 1)
        if len(body) > maximum:
            raise ValueError('Inspection response exceeds byte limit')
        return response.status, dict(response.headers.items()), body


def check_scope(origin, target, base_path='/'):
    """Return the absolute authorized URL for ``target`` or raise ScopeError.

    The target must stay on the exact origin handed to the harvester and be
    the configured directory path.  Only http loopback origins are usable;
    credentials and other schemes are rejected before any I/O.
    """
    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme != 'http' or parsed_origin.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ScopeError('authorized origin must be loopback HTTP')
    if parsed_origin.username or parsed_origin.password:
        raise ScopeError('credentials in the origin are not accepted')
    absolute = urljoin(origin.rstrip('/') + base_path, target or '')
    parsed = urlsplit(absolute)
    if parsed.scheme != parsed_origin.scheme or parsed.netloc != parsed_origin.netloc:
        raise ScopeError('request leaves the authorized origin')
    if parsed.username or parsed.password:
        raise ScopeError('credentials in URLs are not accepted')
    if parsed.path.rstrip('/') != base_path.rstrip('/'):
        raise ScopeError('request leaves the authorized path scope')
    return absolute


def scope_violation(origin, target, base_path='/'):
    try:
        check_scope(origin, target, base_path)
    except ScopeError:
        return True
    return False


def _header_names(headers):
    return [(name, value) for name, value in headers.items()]


class Session:
    """Small cookie-aware client restricted to one directory path.

    Redirects are followed manually and only while they remain on the
    authorized origin and path.  Every response, including error responses,
    is returned to the caller so the flow can distinguish expired state,
    login pages and count mismatches instead of treating them as success.
    """

    def __init__(self, origin, base_path='/directory', maximum=4194304, timeout=3, redirect_limit=5):
        self.origin = origin.rstrip('/')
        self.base_path = base_path
        self.maximum = maximum
        self.timeout = timeout
        self.redirect_limit = redirect_limit
        self.cookies = {}
        self.requests = []
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _cookie_header(self):
        if not self.cookies:
            return None
        return '; '.join(name + '=' + value for name, value in self.cookies.items())

    def _absorb_cookies(self, headers):
        for name, value in headers:
            if name.lower() != 'set-cookie':
                continue
            cookie = SimpleCookie()
            try:
                cookie.load(value)
            except CookieError:
                continue
            for key, morsel in cookie.items():
                self.cookies[key] = morsel.value

    def _send(self, method, url, data=None, headers=None):
        merged = dict(headers or {})
        cookie = self._cookie_header()
        if cookie and 'Cookie' not in merged:
            merged['Cookie'] = cookie
        body = urlencode(data).encode() if isinstance(data, (list, tuple)) else data
        request = Request(url, data=body, method=method, headers=merged)
        self.requests.append({'method': method, 'url': url,
                              'headers': {name: value for name, value in merged.items()
                                          if name.lower() != 'cookie'},
                              'body': body.decode(errors='replace') if body else ''})
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read(self.maximum + 1)
            if len(payload) > self.maximum:
                raise ValueError('response exceeds byte limit')
            result_headers = _header_names(response.headers)
            self._absorb_cookies(result_headers)
            return response.status, result_headers, payload, response.headers.get('Location')

    def request(self, method, target, data=None):
        method = method.upper()
        if method not in ('GET', 'POST'):
            raise ValueError('only read-only directory requests are allowed')
        url = check_scope(self.origin, target, self.base_path)
        redirects = 0
        while True:
            headers = {'Content-Type': 'application/x-www-form-urlencoded'} if method == 'POST' else {}
            status, response_headers, payload, location = self._send(method, url, data, headers)
            if status not in (301, 302, 303, 307, 308) or location is None:
                return {'status': status, 'headers': response_headers,
                        'body': payload, 'url': url, 'redirects': redirects,
                        'origin': self.origin, 'base_path': self.base_path}
            redirects += 1
            if redirects > self.redirect_limit:
                raise ScopeError('too many redirects')
            url = check_scope(self.origin, location, self.base_path)
            if status in (301, 302, 303):
                method, data = 'GET', None

    def get(self, target=None):
        return self.request('GET', target or self.base_path)

    def post(self, target, pairs):
        return self.request('POST', target, pairs)
