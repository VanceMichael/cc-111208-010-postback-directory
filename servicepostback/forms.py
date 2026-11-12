"""HTML subset used by the synthetic directory portal.

The inspector in :mod:`servicepostback.parser` is a raw view of controls.
This module builds the browser-facing model needed to submit a form: selects
own their options, controls that cannot be successful are omitted, and
duplicate names keep every value in document order.
"""

from html.parser import HTMLParser


def normalize(value):
    return ' '.join((value or '').split())


def _attributes(attrs):
    return {name: (value if value is not None else '') for name, value in attrs}


class DirectoryPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.tables = []
        self._form = None
        self._table = None
        self._row = None
        self._cell = None
        self._cell_text = []
        self._select = None
        self._option = None
        self._option_text = []
        self._textarea = None
        self._textarea_text = []
        self._button = None
        self._button_text = []

    def handle_starttag(self, tag, attrs):
        attributes = _attributes(attrs)
        if tag == 'form':
            self._form = {'action': attributes.get('action', ''),
                          'method': attributes.get('method', 'get').upper(),
                          'controls': []}
            self.forms.append(self._form)
            return
        if tag == 'table':
            self._table = {'attributes': attributes, 'rows': []}
            self.tables.append(self._table)
            return
        if tag == 'tr' and self._table is not None:
            self._row = {'attributes': attributes, 'name': '', 'cells': []}
            self._table['rows'].append(self._row)
            return
        if tag == 'td' and self._row is not None:
            self._cell_text = []
            return
        if self._form is None:
            return
        if tag == 'select':
            control = {'tag': tag, 'attributes': attributes, 'options': []}
            self._form['controls'].append(control)
            self._select = control
        elif tag == 'option' and self._select is not None:
            self._option = _attributes(attrs)
            self._option_text = []
        elif tag == 'textarea':
            self._textarea = {'tag': tag, 'attributes': attributes, 'text': ''}
            self._form['controls'].append(self._textarea)
            self._textarea_text = []
        elif tag == 'button':
            self._button = {'tag': tag, 'attributes': attributes, 'text': ''}
            self._form['controls'].append(self._button)
            self._button_text = []
        elif tag == 'input':
            self._form['controls'].append({'tag': tag, 'attributes': attributes})

    def handle_endtag(self, tag):
        if tag == 'form':
            self._form = None
            self._select = None
        elif tag == 'table':
            self._table = None
            self._row = None
        elif tag == 'tr':
            self._row = None
        elif tag == 'td' and self._row is not None:
            text = normalize(''.join(self._cell_text))
            self._row['cells'].append(text)
            if not self._row['name'] and text:
                self._row['name'] = text
            self._cell_text = []
        elif tag == 'select':
            self._select = None
        elif tag == 'option' and self._select is not None and self._option is not None:
            self._select['options'].append({'attributes': self._option,
                                            'text': normalize(''.join(self._option_text))})
            self._option = None
            self._option_text = []
        elif tag == 'textarea' and self._textarea is not None:
            self._textarea['text'] = ''.join(self._textarea_text)
            self._textarea = None
            self._textarea_text = []
        elif tag == 'button' and self._button is not None:
            self._button['text'] = normalize(''.join(self._button_text))
            self._button = None
            self._button_text = []

    def handle_data(self, data):
        if self._row is not None and self._cell_text is not None:
            self._cell_text.append(data)
        if self._option is not None:
            self._option_text.append(data)
        elif self._textarea is not None:
            self._textarea_text.append(data)
        elif self._button is not None:
            self._button_text.append(data)


def parse_page(payload):
    parser = DirectoryPageParser()
    parser.feed(payload.decode('utf-8'))
    parser.close()
    return {'forms': parser.forms, 'tables': parser.tables}


def successful_controls(form, selected_city=None, action_value=None):
    """Return (name, value) pairs for a browser-style form submission.

    ``selected_city`` selects the city option when navigating to a city;
    ``action_value`` is the value of the submit button that was actually
    activated (only that button is included).  Disabled controls, unchecked
    checkboxes and unnamed controls are omitted.  Duplicate names are kept in
    document order.  Selects use the explicit selected option and, when there
    is none, the first option (the source document's stated subset).  Radios
    without any explicit selection use their first option too.
    """
    pairs = []
    explicit_radio = set()
    for control in form['controls']:
        attributes = control['attributes']
        if control['tag'] != 'input' or attributes.get('type', 'text').lower() != 'radio':
            continue
        if 'disabled' in attributes or not attributes.get('name'):
            continue
        if 'checked' in attributes:
            explicit_radio.add(attributes['name'])
    for control in form['controls']:
        tag = control['tag']
        attributes = control['attributes']
        if tag != 'select' and ('disabled' in attributes or not attributes.get('name')):
            continue
        name = attributes.get('name')
        kind = attributes.get('type', 'text').lower()
        if tag == 'select':
            chosen = _selected_option(control['options'], selected_city, name)
            if chosen is not None and 'disabled' not in chosen['attributes']:
                pairs.append((name, chosen['attributes'].get('value', chosen['text'])))
        elif tag == 'textarea':
            pairs.append((name, control['text']))
        elif tag == 'button':
            if attributes.get('type', 'submit').lower() == 'submit' \
                    and attributes.get('value') == action_value:
                pairs.append((name, action_value))
        elif kind == 'radio':
            if name in explicit_radio:
                if 'checked' in attributes:
                    pairs.append((name, attributes.get('value', 'on')))
            else:
                # No explicit selection in this group: the first radio is used.
                pairs.append((name, attributes.get('value', 'on')))
                explicit_radio.add(name)
        elif kind == 'checkbox':
            if 'checked' in attributes:
                pairs.append((name, attributes.get('value', 'on')))
        elif kind in ('submit', 'image'):
            if attributes.get('value') == action_value:
                pairs.append((name, action_value))
        elif kind not in ('button', 'reset', 'file'):
            pairs.append((name, attributes.get('value', '')))
    return pairs


def _selected_option(options, selected_city, name):
    if selected_city is not None and name == 'city':
        for option in options:
            if option['attributes'].get('value') == selected_city:
                return option
        return None
    for option in options:
        if 'selected' in option['attributes']:
            return option
    return options[0] if options else None


def submit_target(form, origin, base_path='/'):
    """Resolve a form action and enforce the authorized origin/path scope."""
    from .transport import scope_violation

    action = form.get('action') or base_path
    if scope_violation(origin, action, base_path=base_path):
        raise ValueError('form action leaves the authorized origin or path scope')
    return action


def find_form(forms):
    directory = [form for form in forms if any(
        control['attributes'].get('name') == 'state' for control in form['controls'])]
    if len(directory) != 1:
        return None
    return directory[0]


def find_city_options(form):
    """Return configured city values from the form, preserving option order."""
    for control in form['controls']:
        if control['tag'] == 'select' and control['attributes'].get('name') == 'city':
            return [option['attributes'].get('value') for option in control['options']
                    if 'disabled' not in option['attributes']
                    and option['attributes'].get('value') is not None]
    return []


def find_action_values(form):
    values = []
    for control in form['controls']:
        attributes = control['attributes']
        if control['tag'] in ('button', 'input') and attributes.get('name') == 'action':
            if 'disabled' not in attributes and attributes.get('value') is not None:
                values.append(attributes['value'])
    return values


def _is_login(forms):
    return any(any(control['attributes'].get('name') == 'password'
                   for control in form['controls']) for form in forms)


def parse_listing(payload, origin=None, base_path='/'):
    """Parse a directory page into its form and listing table."""
    parsed = parse_page(payload)
    if _is_login(parsed['forms']):
        return {'form': None, 'table': None, 'login': True}
    form = find_form(parsed['forms'])
    table = next((item for item in parsed['tables'] if 'data-city' in item['attributes']), None)
    if form is None or table is None:
        return {'form': form, 'table': None, 'login': False}
    rows = []
    for row in table['rows']:
        attributes = row['attributes']
        if 'data-channel' not in attributes or 'data-store' not in attributes:
            continue
        rows.append({'channel': attributes['data-channel'],
                     'store': attributes['data-store'],
                     'name': row['name']})
    try:
        total = int(table['attributes'].get('data-total', ''))
    except ValueError:
        total = None
    target = submit_target(form, origin, base_path) if origin else form.get('action', '')
    return {
        'form': form,
        'table': {
            'city': table['attributes'].get('data-city'),
            'total': total,
            'last': table['attributes'].get('data-last', '').lower() == 'true',
            'rows': rows,
            'target': target,
            'city_options': find_city_options(form),
            'action_values': find_action_values(form),
        },
        'login': False,
    }
