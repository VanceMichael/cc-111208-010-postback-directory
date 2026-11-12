"""Persistent harvest state: originals, city progress and published sites.

Originals are content addressed through :class:`servicepostback.journal.Journal`.
City progress, the per-page index and site occurrences live in the same SQLite
database, so a crash cannot leave a half-published city: a city only becomes
``complete`` after its last page and declared total have both been checked.
"""

from datetime import datetime, timezone
from pathlib import Path
from .journal import Journal


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize(value):
    return ' '.join((value or '').split())


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.journal = Journal(self.root)
        self.database = self.journal.database
        self.database.execute('PRAGMA foreign_keys = ON')
        with self.database:
            self.database.executescript('''
                CREATE TABLE IF NOT EXISTS harvest_pages (
                    city TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    url TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (city, page_no, kind)
                );
                CREATE TABLE IF NOT EXISTS city_progress (
                    city TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    expected_total INTEGER,
                    collected INTEGER NOT NULL DEFAULT 0,
                    pages INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS occurrences (
                    channel TEXT NOT NULL,
                    store TEXT NOT NULL,
                    city TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (channel, store, city, page_no)
                );
                CREATE TABLE IF NOT EXISTS sites (
                    channel TEXT NOT NULL,
                    store TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL,
                    reason TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (channel, store)
                );
            ''')

    def close(self):
        self.journal.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- originals -------------------------------------------------------
    def save_page(self, city, page_no, url, payload, kind='directory'):
        """Record the original for one page slot.

        Originals stay content addressed, while the page index is keyed by
        (city, page_no, kind).  Re-walking an interrupted city replaces its
        slots instead of accumulating rows; a completed city is never re-walked.
        """
        saved = self.journal.save(payload, url)
        with self.database:
            self.database.execute(
                'INSERT INTO harvest_pages (city, page_no, kind, digest, url, size, saved_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(city, page_no, kind) DO UPDATE SET '
                'digest = excluded.digest, url = excluded.url, size = excluded.size, '
                'saved_at = excluded.saved_at',
                (city, page_no, kind, saved['digest'], url, saved['size'], _now()))
        return saved['digest']

    def page(self, city, page_no, kind='directory'):
        row = self.database.execute(
            'SELECT city, page_no, kind, digest, url, size, saved_at '
            'FROM harvest_pages WHERE city = ? AND page_no = ? AND kind = ?',
            (city, page_no, kind)).fetchone()
        if row is None:
            return None
        keys = ('city', 'page_no', 'kind', 'digest', 'url', 'size', 'saved_at')
        return dict(zip(keys, row))

    def pages(self, city=None):
        query = ('SELECT city, page_no, kind, digest, url, size, saved_at '
                 'FROM harvest_pages')
        parameters = ()
        if city is not None:
            query += ' WHERE city = ?'
            parameters = (city,)
        query += ' ORDER BY city, page_no'
        keys = ('city', 'page_no', 'kind', 'digest', 'url', 'size', 'saved_at')
        return [dict(zip(keys, row)) for row in self.database.execute(query, parameters)]

    # -- city progress ---------------------------------------------------
    def city_status(self, city):
        row = self.database.execute('SELECT status FROM city_progress WHERE city = ?',
                                    (city,)).fetchone()
        return row[0] if row else None

    def city(self, city):
        row = self.database.execute(
            'SELECT city, status, expected_total, collected, pages, reason, updated_at '
            'FROM city_progress WHERE city = ?', (city,)).fetchone()
        if row is None:
            return None
        keys = ('city', 'status', 'expected_total', 'collected', 'pages', 'reason', 'updated_at')
        return dict(zip(keys, row))

    def progress(self):
        keys = ('city', 'status', 'expected_total', 'collected', 'pages', 'reason', 'updated_at')
        return [dict(zip(keys, row)) for row in self.database.execute(
            'SELECT ' + ', '.join(keys) + ' FROM city_progress ORDER BY city')]

    def begin_city(self, city, expected_total=None):
        """Start or restart a city from a fresh page.

        A completed city is immutable.  An unfinished city discards its
        observations (they are not publishable) but keeps the originals already
        saved, keyed by page slot so a re-walk cannot duplicate index rows.
        """
        if self.city_status(city) == 'complete':
            return False
        with self.database:
            self.database.execute('DELETE FROM occurrences WHERE city = ?', (city,))
            self.database.execute(
                'INSERT INTO city_progress (city, status, expected_total, collected, pages, reason, updated_at) '
                'VALUES (?, ?, ?, 0, 0, NULL, ?) '
                'ON CONFLICT(city) DO UPDATE SET status = excluded.status, '
                'expected_total = COALESCE(excluded.expected_total, city_progress.expected_total), '
                'collected = 0, pages = 0, reason = NULL, updated_at = excluded.updated_at',
                (city, 'in_progress', expected_total, _now()))
        self._recompute_sites()
        return True

    def record_page(self, city, page_no, total, rows, digest):
        with self.database:
            self.database.execute('DELETE FROM occurrences WHERE city = ? AND page_no = ?',
                                  (city, page_no))
            for row in rows:
                self.database.execute(
                    'INSERT INTO occurrences VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (row['channel'], row['store'], city, page_no, _normalize(row['name']),
                     digest, _now()))
            collected = self.database.execute(
                'SELECT COUNT(*) FROM (SELECT DISTINCT channel, store FROM occurrences WHERE city = ?)',
                (city,)).fetchone()[0]
            pages = self.database.execute(
                'SELECT COUNT(DISTINCT page_no) FROM occurrences WHERE city = ?',
                (city,)).fetchone()[0]
            self.database.execute(
                'UPDATE city_progress SET expected_total = ?, collected = ?, pages = ?, updated_at = ? '
                'WHERE city = ?', (total, collected, pages, _now(), city))
        self._recompute_sites()
        return collected

    def complete_city(self, city, expected_total, collected):
        with self.database:
            self.database.execute(
                'UPDATE city_progress SET status = ?, expected_total = ?, collected = ?, '
                'reason = NULL, updated_at = ? WHERE city = ?',
                ('complete', expected_total, collected, _now(), city))
        self._recompute_sites()

    def fail_city(self, city, reason):
        with self.database:
            self.database.execute(
                'UPDATE city_progress SET status = ?, reason = ?, updated_at = ? WHERE city = ?',
                ('failed', reason, _now(), city))
        self._recompute_sites()

    # -- publication -----------------------------------------------------
    def _recompute_sites(self):
        """Recompute per-site publication from stored occurrences.

        A site is published only when every city where it was observed has been
        proven complete; an incomplete or failed city keeps its sites ``held``.
        The same identity observed in several complete cities with identical
        content merges into one site.  Different content is ``conflict``: both
        occurrences and their source pages stay available, but the site is not
        published.
        """
        rows = self.database.execute(
            'SELECT channel, store, city, page_no, name, digest FROM occurrences '
            'ORDER BY channel, store, city, page_no').fetchall()
        grouped = {}
        for channel, store, city, page_no, name, digest in rows:
            grouped.setdefault((channel, store), []).append(
                {'city': city, 'page_no': page_no, 'name': name, 'digest': digest})
        statuses = {row[0]: row[1] for row in self.database.execute(
            'SELECT city, status FROM city_progress')}
        with self.database:
            self.database.execute('DELETE FROM sites')
            for (channel, store), items in grouped.items():
                names = sorted({item['name'] for item in items})
                waiting = sorted({item['city'] for item in items
                                  if statuses.get(item['city']) != 'complete'})
                if waiting:
                    status = 'held'
                    reason = 'waiting for ' + ', '.join(waiting)
                elif len(names) > 1:
                    status = 'conflict'
                    reason = 'conflicting content for %s/%s: %s' % (
                        channel, store,
                        ' | '.join(item['city'] + '=' + item['name'] + '@' + item['digest']
                                   for item in items))
                else:
                    status, reason = 'published', None
                self.database.execute(
                    'INSERT INTO sites VALUES (?, ?, ?, ?, ?, ?)',
                    (channel, store, names[0], status, reason, _now()))

    def sites(self, status=None):
        query = 'SELECT channel, store, name, status, reason, updated_at FROM sites'
        parameters = ()
        if status:
            query += ' WHERE status = ?'
            parameters = (status,)
        keys = ('channel', 'store', 'name', 'status', 'reason', 'updated_at')
        return [dict(zip(keys, row)) for row in self.database.execute(
            query + ' ORDER BY channel, store', parameters)]

    def site_sources(self, channel, store):
        rows = self.database.execute(
            "SELECT o.city, o.page_no, o.name, o.digest, o.seen_at, "
            "COALESCE(p.url, ''), COALESCE(c.status, 'unknown') "
            'FROM occurrences o '
            'LEFT JOIN harvest_pages p ON p.city = o.city AND p.page_no = o.page_no '
            "AND p.kind = 'directory' "
            'LEFT JOIN city_progress c ON c.city = o.city '
            'WHERE o.channel = ? AND o.store = ? '
            'ORDER BY o.city, o.page_no', (channel, store)).fetchall()
        keys = ('city', 'page_no', 'name', 'digest', 'seen_at', 'url', 'city_status')
        return [dict(zip(keys, row)) for row in rows]

    def counts(self):
        return {
            'pages': self.database.execute('SELECT COUNT(*) FROM harvest_pages').fetchone()[0],
            'occurrences': self.database.execute('SELECT COUNT(*) FROM occurrences').fetchone()[0],
            'sites': self.database.execute('SELECT COUNT(*) FROM sites').fetchone()[0],
            'complete_cities': self.database.execute(
                'SELECT COUNT(*) FROM city_progress WHERE status = ?', ('complete',)).fetchone()[0],
        }
