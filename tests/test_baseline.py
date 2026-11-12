import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from servicepostback.parser import inspect
from servicepostback.journal import Journal
from servicepostback.transport import capture
from scripts.materialize import main as materialize
from scripts.source_server import running


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        materialize()

    def test_capture_journal_is_content_addressed(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            try:
                first = journal.save(b'synthetic', 'source-1')
                second = journal.save(b'synthetic', 'source-1')
                self.assertEqual(first, second)
                self.assertEqual(Path(first['path']).read_bytes(), b'synthetic')
                self.assertEqual(journal.database.execute('SELECT COUNT(*) FROM captures').fetchone()[0], 1)
            finally:
                journal.close()

    def test_remote_transport_is_rejected_before_io(self):
        with self.assertRaises(ValueError):
            capture('https://example.invalid/private')

    def test_cli_help(self):
        result = subprocess.run([sys.executable, '-m', 'servicepostback', '--help'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'inspect', result.stdout)

    def test_local_source_and_request_log(self):
        with running() as server:
            status, headers, payload = capture(server.origin + '/directory', 'GET', headers={})
            self.assertEqual(status, 200)
            self.assertGreater(len(payload), 0)
            self.assertEqual(server.requests[0]['method'], 'GET')

    def test_unknown_source_route_is_not_success(self):
        with running() as server:
            self.assertEqual(capture(server.origin + '/not-present')[0], 404)

    def test_controls_keep_duplicate_names(self):
        result = inspect(Path('fixtures/city-a.html').read_bytes())
        form = result['forms'][0]
        self.assertEqual(form['method'], 'POST')
        self.assertEqual(sum(item['attributes'].get('name') == 'scope' for item in form['controls']), 2)

    def test_expired_form_cannot_turn_into_next_page(self):
        from urllib.parse import urlencode
        with running() as server:
            body = urlencode([('state','one'),('scope','retail'),('scope','service'),('city','A'),('action','next')]).encode()
            headers = {'Cookie': 'sid=local', 'Content-Type': 'application/x-www-form-urlencoded'}
            self.assertEqual(capture(server.origin + '/directory', 'POST', body, headers)[0], 200)
            self.assertEqual(capture(server.origin + '/directory', 'POST', body, headers)[0], 409)

    def test_reserve_is_not_a_collection_action(self):
        with running() as server:
            self.assertEqual(capture(server.origin + '/directory', 'POST', b'action=reserve', {'Cookie': 'sid=local'})[0], 409)


if __name__ == '__main__':
    unittest.main()
