"""Authorized directory harvest driven by the forms the portal returns.

The harvester never builds URLs for city or page transitions and never calls
site-private endpoints.  Every request is a GET of the authorized directory
path or a POST of the successful controls of the form just received, with the
submit button that is actually activated.  One-time state is therefore never
reused across pages or cities: after a city switch, or after an expired-state
response, the flow starts again from a freshly fetched page.
"""

from .forms import parse_listing, successful_controls
from .store import Store
from .transport import ScopeError, Session


ALLOWED_ACTIONS = ('city', 'next')
CITY_ACTION = 'city'
NEXT_ACTION = 'next'
MAX_PAGES = 1000


class HarvestFailure(Exception):
    pass


class RetryCity(Exception):
    """The current city may be walked again from a freshly fetched page."""


def _read_page(response, expected_city=None):
    """Validate a directory response and attach its raw body to the page."""
    if response['status'] != 200:
        if response['status'] == 409:
            raise RetryCity('source rejected the one-time form state (HTTP 409)')
        if response['status'] == 401:
            raise HarvestFailure('source requires login (HTTP 401)')
        if response['status'] == 403:
            raise HarvestFailure('source rejected the action (HTTP 403)')
        raise HarvestFailure('unexpected HTTP status %d' % response['status'])
    try:
        page = parse_listing(response['body'], response['origin'], response['base_path'])
    except ValueError as error:
        raise HarvestFailure(str(error))
    if page['login']:
        raise HarvestFailure('source returned a login form')
    if page['table'] is None:
        raise HarvestFailure('response is not a directory listing')
    if expected_city is not None and page['table']['city'] != expected_city:
        raise HarvestFailure('listing city %r does not match requested city %r'
                             % (page['table']['city'], expected_city))
    if page['table']['total'] is None or page['table']['total'] < 0:
        raise HarvestFailure('listing does not declare a total')
    page['body'] = response['body']
    page['url'] = response['url']
    return page


def _post(session, page, city, action):
    if action not in ALLOWED_ACTIONS:
        raise HarvestFailure('action %r is not an authorized collection action' % action)
    if action not in page['table']['action_values']:
        raise HarvestFailure('page does not offer the %r action' % action)
    if page['form']['method'] != 'POST':
        raise HarvestFailure('directory form is not a POST form')
    pairs = successful_controls(page['form'], selected_city=city, action_value=action)
    if action == CITY_ACTION or any(name == 'city' for name, value in pairs):
        # A configured partition must actually be submitted; a silent default
        # would query the wrong city.
        if not any(name == 'city' and value == city for name, value in pairs):
            raise HarvestFailure('city %r is not a selectable option' % city)
    try:
        response = session.post(page['table']['target'], pairs)
    except ScopeError as error:
        raise HarvestFailure(str(error))
    return _read_page(response, expected_city=city)

def _walk_city(session, store, city):
    """Walk one city from a fresh page, returning the collected site count."""
    try:
        fresh = _read_page(session.get())
    except ScopeError as error:
        raise HarvestFailure(str(error))
    if city not in fresh['table']['city_options']:
        raise HarvestFailure('city %r is not configured by the source form' % city)
    if fresh['table']['city'] == city:
        page = fresh
    else:
        page = _post(session, fresh, city, CITY_ACTION)
        if page['table']['city'] != city:
            raise HarvestFailure('city switch did not enter %r' % city)

    declared_total = page['table']['total']
    store.begin_city(city, expected_total=declared_total)
    seen_signatures = set()
    seen_identities = set()
    page_no = 0
    while True:
        page_no += 1
        if page_no > MAX_PAGES:
            raise HarvestFailure('pagination exceeded %d pages' % MAX_PAGES)
        table = page['table']
        if table['total'] != declared_total:
            raise HarvestFailure('declared total changed while walking %r' % city)
        signature = (table['last'],
                     tuple((row['channel'], row['store'], row['name']) for row in table['rows']))
        if signature in seen_signatures:
            raise HarvestFailure('duplicate listing page for city %r' % city)
        seen_signatures.add(signature)
        for row in table['rows']:
            identity = (row['channel'], row['store'])
            if identity in seen_identities:
                raise HarvestFailure('duplicate site %s/%s while walking %r'
                                     % (identity[0], identity[1], city))
            seen_identities.add(identity)
        digest = store.save_page(city, page_no, page['url'], page['body'])
        collected = store.record_page(city, page_no, table['total'], table['rows'], digest)
        if table['last']:
            if collected != table['total']:
                raise HarvestFailure('city %r declares %d sites but lists %d'
                                     % (city, table['total'], collected))
            store.complete_city(city, table['total'], collected)
            return collected
        page = _post(session, page, city, NEXT_ACTION)


def _failed(store, city, reason, attempt):
    progress = store.city(city)
    store.fail_city(city, reason)
    return {'city': city, 'status': 'failed', 'reason': reason,
            'collected': progress['collected'] if progress else 0,
            'expected_total': progress['expected_total'] if progress else None,
            'attempts': attempt}


def harvest(origin, root, cities=None, attempts=2):
    """Collect all configured cities, returning a JSON-friendly summary.

    A city is only published after its final page and declared total agree.
    Incomplete or failed cities keep their originals but publish no results.
    A city already proven complete is not walked again, even when its one-time
    state no longer exists.
    """
    store = Store(root)
    try:
        session = Session(origin)
        try:
            first = _read_page(session.get())
        except (HarvestFailure, ScopeError, ValueError) as error:
            return {'ok': False, 'error': str(error), 'cities': [],
                    'progress': store.progress(), 'sites': store.sites(),
                    'counts': store.counts()}
        configured = list(first['table']['city_options'])
        if not configured:
            return {'ok': False, 'error': 'source form declares no cities', 'cities': [],
                    'progress': store.progress(), 'sites': store.sites(),
                    'counts': store.counts()}
        requested = list(cities or configured)
        unknown = [city for city in requested if city not in configured]
        if unknown:
            return {'ok': False,
                    'error': 'city %s is not configured by the source form' % unknown[0],
                    'cities': [{'city': value, 'status': 'not_started'} for value in requested],
                    'progress': store.progress(), 'sites': store.sites(),
                    'counts': store.counts()}

        results = []
        for city in requested:
            previous = store.city(city)
            if previous and previous['status'] == 'complete':
                results.append({'city': city, 'status': 'complete', 'reused': True,
                                'collected': previous['collected'],
                                'expected_total': previous['expected_total']})
                continue
            reason = 'not attempted'
            appended = False
            for attempt in range(1, max(1, attempts) + 1):
                try:
                    collected = _walk_city(session, store, city)
                except RetryCity as failure:
                    # The one-time state is gone.  Discard the unfinished
                    # observation set and fetch a fresh page; already saved
                    # originals stay content addressed.
                    reason = str(failure)
                    store.begin_city(city)
                    session = Session(origin)
                    continue
                except HarvestFailure as failure:
                    reason = str(failure)
                    break
                else:
                    results.append({'city': city, 'status': 'complete',
                                    'collected': collected,
                                    'expected_total': store.city(city)['expected_total'],
                                    'attempts': attempt})
                    appended = True
                    break
            if not appended:
                results.append(_failed(store, city, reason, attempt))
        complete = all(item['status'] == 'complete' for item in results)
        return {'ok': complete, 'cities': results,
                'progress': store.progress(), 'sites': store.sites(),
                'counts': store.counts()}
    finally:
        store.close()
