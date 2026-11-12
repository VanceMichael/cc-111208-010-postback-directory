"""End-to-end tests for the authorized directory harvest.

The synthetic portal in scripts/portal.py is the only source used here; no
real site, login or reservation endpoint is contacted.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs

from scripts.portal import running
from servicepostback.forms import parse_listing, successful_controls
from servicepostback.harvest import harvest
from servicepostback.store import Store
from servicepostback.transport import Session


def read_store(directory):
    with Store(directory) as store:
        return {'progress': store.progress(), 'sites': store.sites(),
                'pages': store.pages(), 'counts': store.counts()}


class HarvestTests(unittest.TestCase):
    def test_walks_both_cities_through_authorized_controls(self):
        with running() as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state)
            self.assertTrue(result['ok'])
            self.assertEqual([item['city'] for item in result['cities']], ['A', 'B'])
            self.assertEqual([item['status'] for item in result['cities']],
                             ['complete', 'complete'])
            self.assertEqual(result['counts']['pages'], 4)
            self.assertEqual(result['counts']['occurrences'], 4)
            # Shared C1/S1 appears in both cities but is one published site.
            published = [site for site in result['sites'] if site['status'] == 'published']
            self.assertEqual(sorted((site['channel'], site['store']) for site in published),
                             [('C1', 'S1'), ('C1', 'S2'), ('C1', 'S3')])
            with Store(state) as store:
                sources = store.site_sources('C1', 'S1')
            self.assertEqual([source['city'] for source in sources], ['A', 'B'])

            for request in server.requests:
                if request['method'] != 'POST':
                    continue
                form = request['form']
                # Duplicate scope values survive; disabled controls never do.
                self.assertEqual(form['scope'], ['retail', 'service'])
                self.assertNotIn('ignored', form)
                self.assertIn(form['action'], (['next'], ['city']))

    def test_each_post_uses_the_state_returned_by_the_previous_page(self):
        with running() as server, tempfile.TemporaryDirectory() as state:
            harvest(server.origin, state)
            posts = [request for request in server.requests if request['method'] == 'POST']
            states = [request['form']['state'][0] for request in posts]
            self.assertEqual(len(states), len(set(states)),
                             'one-time state was reused across requests')
            self.assertEqual(posts[0]['form']['city'], ['A'])
            self.assertEqual(posts[1]['form']['city'], ['B'])

    def test_restart_reuses_complete_cities_without_adding_originals(self):
        with running() as server, tempfile.TemporaryDirectory() as state:
            first = harvest(server.origin, state)
            self.assertTrue(first['ok'])
            # One next POST for A plus one city and one next POST for B; each
            # city's first page comes from a GET.
            self.assertEqual(len([r for r in server.requests if r['method'] == 'POST']), 3)
            second = harvest(server.origin, state)
            self.assertTrue(second['ok'])
            self.assertEqual(second['counts'], first['counts'])
            self.assertTrue(all(item.get('reused') for item in second['cities']))
            self.assertEqual(len([r for r in server.requests if r['method'] == 'POST']), 3)

    def test_expired_state_restarts_current_city_without_duplicate_progress(self):
        with running(stale_after=1) as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state, attempts=3)
            self.assertTrue(result['ok'], result)
            city_b = next(item for item in result['cities'] if item['city'] == 'B')
            self.assertGreater(city_b['attempts'], 1)
            self.assertEqual(city_b['collected'], 2)
            self.assertEqual(result['counts']['pages'], 4)
            self.assertEqual(result['counts']['occurrences'], 4)
            for request in server.requests:
                if request['method'] == 'POST':
                    self.assertNotIn('expired-', request['form']['state'][0],
                                     'a stale one-time state was replayed')

    def test_count_mismatch_is_a_failure_and_holds_sites(self):
        with running(fault_city='A') as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state)
            self.assertFalse(result['ok'])
            city_a = next(item for item in result['cities'] if item['city'] == 'A')
            self.assertEqual(city_a['status'], 'failed')
            self.assertIn('declares 3 sites but lists 2', city_a['reason'])
            # A's sites (including the cross-city shared identity) are held;
            # B's city-local site may publish because B was proven complete.
            shared = next(site for site in result['sites'] if site['store'] == 'S1')
            self.assertEqual(shared['status'], 'held')
            self.assertIn('A', shared['reason'])
            city_a_only = next(site for site in result['sites'] if site['store'] == 'S2')
            self.assertEqual(city_a_only['status'], 'held')
            city_b_only = next(site for site in result['sites'] if site['store'] == 'S3')
            self.assertEqual(city_b_only['status'], 'published')
            progress = {item['city']: item for item in result['progress']}
            self.assertEqual(progress['A']['status'], 'failed')
            self.assertEqual(progress['B']['status'], 'complete')

    def test_duplicate_page_does_not_count_as_completion(self):
        with running(repeat=True) as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state)
            self.assertFalse(result['ok'])
            for city in result['cities']:
                self.assertEqual(city['status'], 'failed')
                self.assertIn('duplicate listing page', city['reason'])

    def test_login_page_is_not_a_completed_city(self):
        with running(login=True) as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state)
            self.assertFalse(result['ok'])
            self.assertIn('login', result['error'])
            self.assertEqual(result['counts']['pages'], 0)

    def test_redirect_outside_scope_is_refused(self):
        with running(redirect=True) as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state)
            self.assertFalse(result['ok'])
            self.assertIn('path scope', result['cities'][0]['reason'])
            self.assertFalse([request for request in server.requests
                              if 'logout' in request['path']])

    def test_reserve_button_is_never_submitted(self):
        with running() as server, tempfile.TemporaryDirectory() as state:
            harvest(server.origin, state)
            for request in server.requests:
                if request['method'] == 'POST':
                    self.assertNotEqual(request['form'].get('action'), ['reserve'])

    def test_conflicting_neighbour_content_is_kept_with_sources(self):
        with running(conflict=True) as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state)
            self.assertTrue(result['ok'])
            shared = next(site for site in result['sites'] if site['store'] == 'S1')
            self.assertEqual(shared['status'], 'conflict')
            self.assertIn('共享网点异值', shared['reason'])
            with Store(state) as store:
                sources = store.site_sources('C1', 'S1')
                pages = [store.page(source['city'], source['page_no'])
                         for source in sources]
            self.assertEqual([source['city'] for source in sources], ['A', 'B'])
            self.assertEqual(len({source['digest'] for source in sources}), 2)
            for source, page in zip(sources, pages):
                self.assertEqual(page['digest'], source['digest'])
                self.assertTrue((Path(state) / 'blobs' / source['digest']).exists())

    def test_missing_city_switch_control_is_not_guessed(self):
        with running(no_city_switch=True) as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state, attempts=1)
            self.assertFalse(result['ok'])
            city_b = next(item for item in result['cities'] if item['city'] == 'B')
            self.assertEqual(city_b['status'], 'failed')
            self.assertIn("does not offer the 'city' action", city_b['reason'])
            # No URL or hidden-interface guess was made for city B.
            self.assertFalse([request for request in server.requests
                              if request['method'] == 'POST'
                              and request['form']['city'] == ['B']])

    def test_unknown_city_is_refused_without_requests(self):
        with running() as server, tempfile.TemporaryDirectory() as state:
            result = harvest(server.origin, state, cities=['A', 'Z'])
            self.assertFalse(result['ok'])
            self.assertIn('not configured', result['error'])
            self.assertEqual([request for request in server.requests
                              if request['method'] == 'POST'], [])

    def test_scope_rejects_other_origin_and_paths(self):
        with running() as server:
            session = Session(server.origin)
            session.get()
            with self.assertRaises(ValueError):
                session.get('http://127.0.0.1:%d/directory' % (server.server_port + 1))
            with self.assertRaises(ValueError):
                session.get('/login')
            with self.assertRaises(ValueError):
                session.request('DELETE', '/directory')
            with self.assertRaises(ValueError):
                Session('https://example.invalid').get()

    def test_fixture_replay_source_session_and_expiry(self):
        from scripts.source_server import running as replay
        with replay() as server, tempfile.TemporaryDirectory() as state:
            # The replay route serves page one then page two for city A.  It
            # offers no city switch control, so B is refused rather than
            # guessed, and the state is not reused for a second next page.
            result = harvest(server.origin, state, attempts=1)
            self.assertFalse(result['ok'])
            city_a = next(item for item in result['cities'] if item['city'] == 'A')
            self.assertEqual(city_a['status'], 'complete')
            city_b = next(item for item in result['cities'] if item['city'] == 'B')
            self.assertEqual(city_b['status'], 'failed')
            self.assertIn('city', city_b['reason'])
            self.assertEqual(result['counts']['pages'], 2)
            # A published its two sites; nothing waits on B.
            published = [site for site in result['sites'] if site['status'] == 'published']
            self.assertEqual(sorted(site['store'] for site in published), ['S1', 'S2'])
            # The session cookie was carried on the POST and duplicate scope
            # values plus only the activated button were sent.
            post = [request for request in server.requests
                    if request['method'] == 'POST'][0]
            self.assertEqual(post['headers'].get('Cookie'), 'sid=local')
            self.assertEqual(parse_qs(post['body']), {
                'state': ['one'], 'scope': ['retail', 'service'],
                'city': ['A'], 'action': ['next']})

    def test_cli_exit_status_reflects_incomplete_harvest(self):
        with running(fault_city='B') as server, tempfile.TemporaryDirectory() as state:
            command = [sys.executable, '-m', 'servicepostback', 'harvest',
                       server.origin + '/directory', '--state', state]
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertIn('failed', failed.stdout)
            query = [sys.executable, '-m', 'servicepostback', 'progress', '--state', state]
            progress = subprocess.run(query, capture_output=True, text=True)
            self.assertEqual(progress.returncode, 0)
            self.assertIn('"city": "B"', progress.stdout)
        with running() as server, tempfile.TemporaryDirectory() as state:
            passed = subprocess.run(
                [sys.executable, '-m', 'servicepostback', 'harvest',
                 server.origin + '/directory', '--state', state],
                capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0)
            sources = subprocess.run(
                [sys.executable, '-m', 'servicepostback', 'site', '--state', state,
                 '--channel', 'C1', '--store', 'S1'], capture_output=True, text=True)
            self.assertEqual(sources.returncode, 0)
            self.assertIn('"city": "A"', sources.stdout)
            self.assertIn('"city": "B"', sources.stdout)

    def test_interrupted_process_resumes_current_city_without_new_originals(self):
        """A real child process is terminated, not merely an exception raised."""
        with tempfile.TemporaryDirectory() as state:
            with running(delay=0.05) as server:
                process = subprocess.Popen(
                    [sys.executable, '-m', 'servicepostback', 'harvest',
                     server.origin + '/directory', '--state', state],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                deadline = time.time() + 15
                try:
                    while time.time() < deadline:
                        posts = [request for request in server.requests
                                 if request['method'] == 'POST']
                        if len(posts) >= 2:
                            break
                        time.sleep(0.01)
                    self.assertGreaterEqual(len([r for r in server.requests
                                                 if r['method'] == 'POST']), 2)
                finally:
                    process.terminate()
                    process.communicate(timeout=10)
            counts_after_crash = read_store(state)['counts']
            self.assertGreater(counts_after_crash['pages'], 0)
            self.assertEqual(counts_after_crash['complete_cities'], 1)
            with running() as server:
                result = harvest(server.origin, state)
                self.assertTrue(result['ok'])
                reused = [item['city'] for item in result['cities'] if item.get('reused')]
                self.assertIn('A', reused)
                final = read_store(state)['counts']
                self.assertEqual(final['pages'], 4)
                self.assertEqual(final['occurrences'], 4)
                self.assertEqual(final['complete_cities'], 2)

    def test_restart_redoes_unproven_city_without_duplicate_original_pages(self):
        """An interrupted city is re-walked but its saved originals do not grow."""
        with tempfile.TemporaryDirectory() as state:
            # B's first accepted POST then an expired state makes B fail after
            # one recorded page; A completes first.
            with running(stale_after=2) as server:
                first = harvest(server.origin, state, attempts=1)
            self.assertTrue(first['cities'][0]['status'] == 'complete')
            self.assertEqual(first['cities'][1]['status'], 'failed')
            self.assertEqual(first['counts']['complete_cities'], 1)
            pages_before = {(page['city'], page['page_no']): page['digest']
                            for page in read_store(state)['pages']}
            self.assertIn(('B', 1), pages_before)
            with running() as server:
                second = harvest(server.origin, state)
                self.assertTrue(second['ok'])
                self.assertTrue(second['cities'][1]['status'] == 'complete')
                self.assertFalse(second['cities'][1].get('reused'))
            after = read_store(state)
            self.assertEqual(after['counts']['pages'], 4,
                             're-walking B must not add duplicate page slots')
            self.assertEqual(after['counts']['occurrences'], 4)
            self.assertEqual(after['counts']['complete_cities'], 2)


class FormSubsetTests(unittest.TestCase):
    def test_disabled_and_duplicate_names_are_kept_in_browser_order(self):
        page = parse_listing(Path('fixtures/city-a.html').read_bytes(),
                             'http://127.0.0.1:9', '/directory')
        pairs = successful_controls(page['form'], selected_city='A', action_value='next')
        values = dict((name, value) for name, value in pairs)
        self.assertEqual(values['state'], 'one')
        self.assertNotIn('ignored', values)
        self.assertEqual(values['city'], 'A')
        self.assertEqual(values['action'], 'next')
        self.assertEqual([value for name, value in pairs if name == 'scope'],
                         ['retail', 'service'])
        self.assertFalse(any(name == 'city' for name, value in
                             successful_controls(page['form'], selected_city='Z',
                                                 action_value='city')))

    def test_selected_option_checkbox_textarea_and_default_radio(self):
        html = (b'<form method="post" action="/directory"><input name="state" value="s">'
                b'<select name="city"><option value="A">A</option>'
                b'<option value="B" selected>B</option></select>'
                b'<textarea name="notes">hello</textarea>'
                b'<input type="checkbox" name="opt" value="1">'
                b'<input type="checkbox" name="opt" value="2" checked>'
                b'<input type="radio" name="kind" value="one">'
                b'<input type="radio" name="kind" value="two">'
                b'<button name="action" value="next">next</button></form>'
                b'<table data-city="B" data-total="1" data-last="true">'
                b'<tr data-channel="C1" data-store="S9"><td>x</td></tr></table>')
        page = parse_listing(html, 'http://127.0.0.1:9', '/directory')
        pairs = successful_controls(page['form'], action_value='next')
        self.assertEqual([value for name, value in pairs if name == 'city'], ['B'])
        self.assertEqual([value for name, value in pairs if name == 'notes'], ['hello'])
        self.assertEqual([value for name, value in pairs if name == 'opt'], ['2'])
        self.assertEqual([value for name, value in pairs if name == 'kind'], ['one'])
        self.assertEqual([value for name, value in pairs if name == 'action'], ['next'])

    def test_activate_one_button_at_a_time(self):
        page = parse_listing(Path('fixtures/city-a.html').read_bytes(),
                             'http://127.0.0.1:9', '/directory')
        # Only the activated button is submitted; no button when none matches.
        next_pairs = successful_controls(page['form'], selected_city='A', action_value='next')
        self.assertEqual([value for name, value in next_pairs if name == 'action'], ['next'])
        none_pairs = successful_controls(page['form'], selected_city='A', action_value=None)
        self.assertNotIn('action', [name for name, value in none_pairs])
        # The harvest allow-list refuses reserve even if a page offers it.
        from servicepostback.harvest import _post
        with self.assertRaises(Exception):
            _post(None, {'table': {'action_values': ['reserve']},
                         'form': page['form']}, 'A', 'reserve')


if __name__ == '__main__':
    unittest.main()
