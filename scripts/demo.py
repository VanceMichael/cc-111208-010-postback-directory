import json
from pathlib import Path
import subprocess
import sys
import tempfile
from .materialize import main as materialize
from .portal import running


def run(*arguments):
    result = subprocess.run([sys.executable, *arguments], check=True,
                            capture_output=True, text=True)
    print(result.stdout.strip())


def main():
    materialize()
    subprocess.run([sys.executable, '-m', 'servicepostback', 'inspect',
                    'fixtures/city-a.html'], check=True)
    with running(stale_after=1) as server, tempfile.TemporaryDirectory() as state:
        print('# harvest: city switch, pagination, one expired state, neighbouring duplicate')
        run('-m', 'servicepostback', 'harvest', server.origin + '/directory', '--state', state)
        run('-m', 'servicepostback', 'progress', '--state', state)
        run('-m', 'servicepostback', 'site', '--state', state, '--channel', 'C1', '--store', 'S1')
        posts = [request for request in server.requests if request['method'] == 'POST']
        print(json.dumps({'requests': len(server.requests),
                          'posts': [{'city': request['form']['city'],
                                     'action': request['form']['action'],
                                     'state': request['form']['state'][0],
                                     'status': request.get('status')}
                                    for request in posts]}, ensure_ascii=False))
    with running(conflict=True) as server, tempfile.TemporaryDirectory() as state:
        print('# conflicting neighbour content keeps both source pages and holds the site')
        run('-m', 'servicepostback', 'harvest', server.origin + '/directory', '--state', state)
        run('-m', 'servicepostback', 'site', '--state', state, '--channel', 'C1', '--store', 'S1')


if __name__ == '__main__':
    main()
