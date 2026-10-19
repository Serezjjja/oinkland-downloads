import contextlib
import importlib.util
import io
import json
import pathlib
import unittest
from unittest.mock import patch


class RemoteSyncTests(unittest.TestCase):
    def setUp(self):
        source = pathlib.Path(__file__).resolve().parents[1] / 'scripts/remote_sync.py'
        spec = importlib.util.spec_from_file_location('remote_sync', source)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.env = {'GITHUB_REPOSITORY': self.api.REPOSITORY, 'GITHUB_TOKEN': 'job-secret',
                    'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://fixture.actions.githubusercontent.com/id?api-version=1',
                    'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'broker-secret'}

    def run_remote(self, messages):
        requests = []
        def open_request(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                return io.BytesIO(json.dumps({'value': 'identity-secret'}).encode())
            return io.BytesIO(b''.join(json.dumps(message).encode() + b'\n' for message in messages))
        output = io.StringIO()
        with patch.dict(self.api.os.environ, self.env, clear=True), \
             patch.object(self.api.urllib.request, 'urlopen', open_request), contextlib.redirect_stdout(output):
            self.api.main()
        return requests, output.getvalue()

    def test_fixed_endpoint_separate_credentials_and_safe_logs(self):
        requests, output = self.run_remote([{'accepted': True}, {'heartbeat': True}, {'log': 'UNCHANGED archive'}, {'complete': True, 'ok': True}])
        broker, publication = requests
        self.assertIn('audience=https%3A%2F%2Flauncher.oinkland.com%2Fmirror-publisher', broker.full_url)
        self.assertEqual(broker.get_header('Authorization'), 'Bearer broker-secret')
        self.assertEqual(publication.full_url, 'https://launcher.oinkland.com/api/mirror/publish')
        self.assertEqual(publication.get_header('Authorization'), 'Bearer identity-secret')
        self.assertEqual(publication.method, 'POST')
        self.assertEqual(json.loads(publication.data), {'githubToken': 'job-secret'})
        self.assertEqual(output, 'UNCHANGED archive\n')
        for secret in ['job-secret', 'broker-secret', 'identity-secret']:
            self.assertNotIn(secret, output)
            self.assertNotIn(secret, publication.full_url)

    def test_failed_or_incomplete_publication_fails_the_job(self):
        for messages in [[{'complete': True, 'ok': False}], [{'accepted': True}], []]:
            with self.subTest(messages=messages), self.assertRaises(RuntimeError):
                self.run_remote(messages)

    def test_foreign_repository_is_rejected_before_credentials_are_used(self):
        self.env['GITHUB_REPOSITORY'] = 'foreign/repository'
        with patch.dict(self.api.os.environ, self.env, clear=True), patch.object(self.api.urllib.request, 'urlopen') as request:
            with self.assertRaises(ValueError):
                self.api.main()
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
