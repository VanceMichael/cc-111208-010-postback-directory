from html.parser import HTMLParser


class FormInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'form':
            self.current = {'action': attributes.get('action', ''),
                            'method': attributes.get('method', 'get').upper(), 'controls': []}
            self.forms.append(self.current)
        elif self.current is not None and tag in ('input', 'button', 'select', 'option', 'textarea'):
            self.current['controls'].append({'tag': tag, 'attributes': attributes})

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


def inspect(payload):
    parser = FormInspector()
    parser.feed(payload.decode('utf-8'))
    return {'forms': parser.forms}
