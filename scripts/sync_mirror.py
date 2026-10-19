"""Publish verified public game archives using only the job's repository token."""
import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import pathlib
import re
import tempfile
import sys
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = 'Serezjjja/oinkland-downloads'
RELEASE = 'automatic-mirror'
ORIGIN = 'https://launcher.oinkland.com'
MAX_BYTES = 2 * 1024 ** 3 - 1


def valid_source(value):
    if not isinstance(value, str):
        return False
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or url.username or url.password or url.port not in (None, 443) or url.fragment:
        return False
    if url.hostname == 'github.com':
        return bool(re.fullmatch(r'/Serezjjja/oinkland-downloads/releases/download/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\.zip', url.path)) and not url.query
    if url.hostname != 'launcher.oinkland.com':
        return False
    if re.fullmatch(r'/dist/(assets|libraries)-1\.21\.zip', url.path):
        return not url.query
    return bool(re.fullmatch(r'/api/servers/[a-zA-Z0-9_-]+/modpack', url.path)) and url.query == 'platform=windows-64'


def validate_item(item):
    if not isinstance(item, dict) or not re.fullmatch(r'(server-[a-zA-Z0-9_-]+-windows-64|dist-(assets|libraries)-1\.21)', str(item.get('key', ''))):
        raise ValueError('Invalid catalogue archive key')
    size = item.get('size')
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_BYTES:
        raise ValueError('Archive exceeds the GitHub release size limit')
    if not re.fullmatch(r'[a-f0-9]{64}', str(item.get('sha256', ''))):
        raise ValueError('Invalid archive SHA-256')
    sources = item.get('sources')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 3 or not all(valid_source(url) for url in sources):
        raise ValueError('Invalid public archive source')
    return {key: item[key] for key in ['key', 'size', 'sha256', 'sources']}


def asset_name(item):
    return item['key'] + '-' + item['sha256'] + '.zip'


def asset_matches(asset, item):
    return (asset.get('name') == asset_name(item) and asset.get('size') == item['size']
            and asset.get('state') == 'uploaded' and asset.get('digest') == 'sha256:' + item['sha256'])


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        allowed = url.scheme == 'https' and not url.username and not url.password and url.port in (None, 443)
        allowed = allowed and url.hostname in ('launcher.oinkland.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
        if not allowed:
            raise ValueError('Unsafe archive redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(url):
    return urllib.request.build_opener(PublicRedirects()).open(urllib.request.Request(url, headers={'User-Agent': 'OinkLand-mirror-sync'}), timeout=120)


def download_verified(item, dest, opener=open_public):
    item = validate_item(item)
    dest = pathlib.Path(dest)
    failure = None
    for url in item['sources']:
        try:
            digest = hashlib.sha256()
            size = 0
            with opener(url) as response, dest.open('wb') as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > item['size']:
                        raise ValueError('Archive response exceeds its advertised size')
                    digest.update(chunk)
                    output.write(chunk)
            if size != item['size'] or digest.hexdigest() != item['sha256']:
                raise ValueError('Archive changed during synchronization or failed SHA-256 verification')
            return dest
        except Exception as error:
            failure = error
            dest.unlink(missing_ok=True)
    raise failure or ValueError('Archive has no source')


class GitHub:
    def __init__(self, token):
        if not token:
            raise ValueError('Repository job token is missing')
        self.token = token

    def api(self, method, endpoint, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'OinkLand-mirror-sync'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + endpoint, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    def release(self):
        try:
            return self.api('GET', '/releases/tags/' + RELEASE)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        return self.api('POST', '/releases', {'tag_name': RELEASE, 'target_commitish': 'main', 'name': 'Automatic game mirror',
                                            'body': 'Content-addressed game archives synchronized from the OinkLand administration panel.', 'make_latest': 'false'})

    def upload(self, release_id, item, filename):
        connection = http.client.HTTPSConnection('uploads.github.com', timeout=180)
        try:
            endpoint = '/repos/' + REPOSITORY + '/releases/' + str(release_id) + '/assets?name=' + urllib.parse.quote(asset_name(item))
            connection.putrequest('POST', endpoint)
            for key, value in {'Authorization': 'Bearer ' + self.token, 'User-Agent': 'OinkLand-mirror-sync',
                               'Content-Type': 'application/zip', 'Content-Length': str(item['size'])}.items():
                connection.putheader(key, value)
            connection.endheaders()
            with pathlib.Path(filename).open('rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    connection.send(chunk)
            response = connection.getresponse()
            data = json.loads(response.read())
            if response.status != 201 or not asset_matches(data, item):
                raise ValueError('GitHub upload failed or returned a different digest: HTTP ' + str(response.status))
            return data
        finally:
            connection.close()

    def heartbeat(self):
        """A weekly content update keeps the public scheduled workflow active."""
        now = dt.datetime.now(dt.timezone.utc)
        previous = None
        try:
            previous = self.api('GET', '/contents/mirror-health.json')
            data = json.loads(base64.b64decode(previous['content']))
            if (now.date() - dt.date.fromisoformat(data['last_checked'])).days < 7:
                return
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        payload = {'message': 'chore: keep automatic mirror schedule active', 'branch': 'main',
                   'content': base64.b64encode((json.dumps({'last_checked': now.date().isoformat()}, indent=2) + '\n').encode()).decode()}
        if previous:
            payload['sha'] = previous['sha']
        self.api('PUT', '/contents/mirror-health.json', payload)


def synchronize(github):
    with open_public(ORIGIN + '/api/mirror/catalogue') as response:
        catalogue = json.loads(response.read(1024 * 1024 + 1))
    if catalogue.get('version') != 1 or not isinstance(catalogue.get('items'), list) or len(catalogue['items']) > 100:
        raise ValueError('Invalid mirror catalogue')
    items = catalogue['items']
    release = github.release()
    assets = {a['name']: a for a in release['assets']}
    published = reused = 0
    failures = []
    with tempfile.TemporaryDirectory(prefix='oink-mirror-') as directory:
        for candidate in items:
            filename = None
            try:
                item = validate_item(candidate)
                name = asset_name(item)
                existing = assets.get(name)
                if existing and asset_matches(existing, item):
                    reused += 1
                    print('UNCHANGED', item['key'], item['sha256'], flush=True)
                    continue
                if existing:
                    github.api('DELETE', '/releases/assets/' + str(existing['id']))
                filename = pathlib.Path(directory) / name
                download_verified(item, filename)
                github.upload(release['id'], item, filename)
                filename.unlink()
                published += 1
                print('PUBLISHED', item['key'], item['size'], item['sha256'], flush=True)
            except Exception as error:
                failures.append(str(error))
                print('ARCHIVE_FAILED', str(error), flush=True)
            finally:
                if filename is not None:
                    filename.unlink(missing_ok=True)
    print(json.dumps({'published': published, 'unchanged': reused, 'failed': len(failures), 'total': len(items)}), flush=True)
    if failures:
        raise RuntimeError(str(len(failures)) + ' archives failed; other archives were processed')


def main():
    payload = json.load(sys.stdin) if sys.argv[1:] == ['--token-stdin'] else None
    repository = payload.get('repository') if payload else os.environ.get('GITHUB_REPOSITORY')
    if repository != REPOSITORY:
        raise ValueError('This job is restricted to the public OinkLand downloads repository')
    github = GitHub(payload.get('token') if payload else os.environ.get('GITHUB_TOKEN'))
    try:
        synchronize(github)
    finally:
        github.heartbeat()


if __name__ == '__main__':
    main()
