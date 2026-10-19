"""Authorize one direct VDS-to-GitHub publication with a GitHub Actions identity."""
import json
import os
import urllib.parse
import urllib.request

AUDIENCE = 'https://launcher.oinkland.com/mirror-publisher'
REPOSITORY = 'Serezjjja/oinkland-downloads'


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
        raise ValueError('Unexpected mirror repository')
    identity_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    identity_url += ('&' if '?' in identity_url else '?') + 'audience=' + urllib.parse.quote(AUDIENCE, safe='')
    request = urllib.request.Request(identity_url, headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.urlopen(request, timeout=30) as response:
        identity = json.load(response)['value']
    body = json.dumps({'githubToken': os.environ['GITHUB_TOKEN']}).encode()
    request = urllib.request.Request('https://launcher.oinkland.com/api/mirror/publish', data=body,
                                    headers={'Authorization': 'Bearer ' + identity, 'Content-Type': 'application/json'}, method='POST')
    completed = False
    with urllib.request.urlopen(request, timeout=600) as response:
        for line in response:
            message = json.loads(line)
            if 'log' in message:
                print(message['log'], flush=True)
            if message.get('complete'):
                if not message.get('ok'):
                    raise RuntimeError('Mirror publication failed; current origin downloads remain available')
                completed = True
    if not completed:
        raise RuntimeError('Mirror publication connection ended before verification completed')


if __name__ == '__main__':
    main()
