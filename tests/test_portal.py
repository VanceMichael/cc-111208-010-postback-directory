import unittest
from urllib.parse import urlencode
from scripts.portal import running
from servicepostback.parser import inspect
from servicepostback.transport import capture


class PortalTests(unittest.TestCase):
    def controls(self, body, city, action):
        form = inspect(body)['forms'][0]
        state = next(item['attributes']['value'] for item in form['controls'] if item['attributes'].get('name') == 'state')
        return urlencode([('state', state), ('scope', 'retail'), ('scope', 'service'), ('city', city), ('action', action)]).encode()

    def test_two_cities_and_one_use_state(self):
        with running() as server:
            status, headers, page = capture(server.origin + '/directory')
            cookie = headers['Set-Cookie'].split(';')[0]
            body = self.controls(page, 'A', 'next')
            headers = {'Cookie': cookie}
            status, response, page2 = capture(server.origin + '/directory', 'POST', body, headers)
            self.assertEqual(status, 200)
            self.assertIn(b'data-last="true"', page2)
            self.assertEqual(capture(server.origin + '/directory', 'POST', body, headers)[0], 409)
            changed = capture(server.origin + '/directory', 'POST', self.controls(page2, 'B', 'city'), headers)
            self.assertEqual(changed[0], 200)
            self.assertIn(b'data-city="B"', changed[2])
            self.assertIn(b'data-last="false"', changed[2])

    def test_unapproved_action_does_not_advance_session(self):
        with running() as server:
            status, response, page = capture(server.origin + '/directory')
            headers = {'Cookie': response['Set-Cookie'].split(';')[0]}
            rejected = capture(server.origin + '/directory', 'POST', self.controls(page, 'A', 'reserve'), headers)
            self.assertEqual(rejected[0], 403)
            accepted = capture(server.origin + '/directory', 'POST', self.controls(page, 'A', 'next'), headers)
            self.assertEqual(accepted[0], 200)

    def test_count_fault_is_observable(self):
        with running(fault_city='A') as server:
            self.assertIn(b'data-total="3"', capture(server.origin + '/directory')[2])
