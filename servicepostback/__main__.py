import argparse
import json
from pathlib import Path
import sys
from .parser import inspect
from .journal import Journal
from .transport import capture


def main():
    parser = argparse.ArgumentParser(description='保养网点门户的 HTML 表单与原文查看工具。')
    sub = parser.add_subparsers(dest='command', required=True)
    view = sub.add_parser('inspect', help='show the raw control tree of one HTML file')
    view.add_argument('source')
    raw = sub.add_parser('capture', help='save one raw response in the journal')
    raw.add_argument('url')
    raw.add_argument('--state', default='var')
    raw.add_argument('--method', default='GET')
    raw.add_argument('--body')
    raw.add_argument('--header', action='append', default=[])
    run = sub.add_parser('harvest', help='walk every configured city of a directory')
    run.add_argument('url')
    run.add_argument('--state', required=True)
    run.add_argument('--city', action='append', default=[])
    progress = sub.add_parser('progress', help='show city progress')
    progress.add_argument('--state', required=True)
    site = sub.add_parser('site', help='show published sites or one site\'s source pages')
    site.add_argument('--state', required=True)
    site.add_argument('--channel')
    site.add_argument('--store')
    site.add_argument('--status')
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            headers = dict(value.split(':', 1) for value in args.header)
            headers = {key.strip(): value.strip() for key, value in headers.items()}
            status, response_headers, payload = capture(args.url, args.method,
                Path(args.body).read_bytes() if args.body else None, headers)
            journal = Journal(args.state)
            try:
                result = journal.save(payload, args.url)
            finally:
                journal.close()
            result.update({'http_status': status, 'headers': response_headers})
        elif args.command == 'inspect':
            result = inspect(Path(args.source).read_bytes())
        elif args.command == 'harvest':
            from .harvest import harvest
            result = harvest(args.url, args.state, cities=args.city or None)
            if not result['ok']:
                print(json.dumps(result, ensure_ascii=True, sort_keys=True))
                return 2
        elif args.command == 'progress':
            from .store import Store
            store = Store(args.state)
            try:
                result = {'progress': store.progress(), 'pages': store.pages(),
                          'counts': store.counts()}
            finally:
                store.close()
        else:
            from .store import Store
            store = Store(args.state)
            try:
                if args.channel and args.store:
                    result = {'sources': store.site_sources(args.channel, args.store)}
                else:
                    result = {'sites': store.sites(args.status)}
            finally:
                store.close()
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
