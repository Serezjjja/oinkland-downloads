import hashlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'scripts/sync_mirror.py'


def load():
    if not SOURCE.exists():
        raise AssertionError('Automatic GitHub mirror publisher must exist')
    spec = importlib.util.spec_from_file_location('sync_mirror', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.api = load()
        self.body = b'updated game archive'
        self.item = {'key': 'server-test-windows-64', 'size': len(self.body), 'sha256': hashlib.sha256(self.body).hexdigest(),
                     'sources': ['https://launcher.oinkland.com/api/servers/test/modpack?platform=windows-64']}

    def test_rejects_private_paths_foreign_sources_and_oversized_files(self):
        invalid = [dict(self.item, key='../../secret'), dict(self.item, sources=['http://127.0.0.1/admin']),
                   dict(self.item, sources=['https://attacker.example/a.zip']), dict(self.item, size=2 * 1024 ** 3),
                   dict(self.item, sources=['https://launcher.oinkland.com/admin/api/settings'])]
        for item in invalid:
            with self.assertRaises(ValueError):
                self.api.validate_item(item)

    def test_changed_archive_download_is_verified_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = pathlib.Path(directory) / 'archive.zip'
            self.api.download_verified(self.item, dest, opener=lambda url: io.BytesIO(self.body))
            self.assertEqual(dest.read_bytes(), self.body)
            with self.assertRaises(ValueError):
                self.api.download_verified(self.item, dest, opener=lambda url: io.BytesIO(b'corrupt'))
            self.assertFalse(dest.exists())

    def test_existing_matching_asset_is_reused_without_reupload(self):
        item = self.api.validate_item(self.item)
        asset = {'name': self.api.asset_name(item), 'size': item['size'], 'digest': 'sha256:' + item['sha256'], 'state': 'uploaded'}
        self.assertTrue(self.api.asset_matches(asset, item))
        asset['digest'] = 'sha256:' + '0' * 64
        self.assertFalse(self.api.asset_matches(asset, item))

    def test_canonical_names_are_content_addressed(self):
        item = self.api.validate_item(self.item)
        self.assertEqual(self.api.asset_name(item), self.item['key'] + '-' + self.item['sha256'] + '.zip')

    def test_one_invalid_or_failed_archive_does_not_starve_later_archives(self):
        for failure in ['invalid', 'download']:
            with self.subTest(failure=failure):
                uploads = []
                bad = dict(self.item, key='server-bad-windows-64')
                if failure == 'invalid':
                    bad['size'] = 2 * 1024 ** 3
                self.api.open_public = lambda url: io.BytesIO(json.dumps({'version': 1, 'items': [bad, self.item]}).encode())
                def download(item, filename):
                    if item['key'] == bad['key']:
                        raise OSError('Unavailable archive')
                    pathlib.Path(filename).write_bytes(self.body)
                self.api.download_verified = download
                class GitHub:
                    def release(self):
                        return {'id': 1, 'assets': []}
                    def upload(self, release, item, filename):
                        uploads.append(item['key'])
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    self.api.synchronize(GitHub())
                self.assertEqual(uploads, [self.item['key']])


if __name__ == '__main__':
    unittest.main()
