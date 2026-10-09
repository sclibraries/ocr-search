import hashlib
import json
import tempfile
import unittest
from pathlib import Path


def synthetic_manifest(item_id):
    return json.dumps({
        '@id': f'https://compass.example.test/node/{item_id}/manifest',
        'sequences': [{'canvases': [
            {'@id': f'https://canvas.example.test/{item_id}/1', 'label': 'Page 1'}
        ]}],
    }).encode()


class ManifestFetchTests(unittest.TestCase):
    def test_manifest_fetch_resumes_from_private_index_and_records_hashes(self):
        from discovery.manifests import fetch_manifests

        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / 'private-manifests'
            rows = [{'item_id': 'demo-a'}, {'item_id': 'demo-b'}, {'item_id': 'demo-c'}]
            calls = []
            fail_once = {'value': True}

            def fetcher(url):
                calls.append(url)
                if url.endswith('/node/demo-b/manifest') and fail_once['value']:
                    fail_once['value'] = False
                    raise OSError('synthetic interruption')
                item_id = url.split('/')[-2]
                return synthetic_manifest(item_id)

            with self.assertRaisesRegex(OSError, 'synthetic interruption'):
                fetch_manifests(rows, output_dir, 'https://compass.example.test',
                                requests_per_second=100, fetcher=fetcher, sleep_fn=lambda _: None)

            entries = fetch_manifests(rows, output_dir, 'https://compass.example.test',
                                      requests_per_second=100, fetcher=fetcher,
                                      sleep_fn=lambda _: None)

            self.assertEqual([entry['item_id'] for entry in entries], ['demo-a', 'demo-b', 'demo-c'])
            self.assertEqual(calls, [
                'https://compass.example.test/node/demo-a/manifest',
                'https://compass.example.test/node/demo-b/manifest',
                'https://compass.example.test/node/demo-b/manifest',
                'https://compass.example.test/node/demo-c/manifest',
            ])
            for entry in entries:
                manifest_bytes = (output_dir / entry['filename']).read_bytes()
                self.assertEqual(entry['sha256'], hashlib.sha256(manifest_bytes).hexdigest())
                self.assertEqual(json.loads(manifest_bytes)['@id'], entry['manifest_url'])

    def test_manifest_fetch_requires_https_and_validates_json_without_network(self):
        from discovery.manifests import fetch_manifests

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                fetch_manifests([{'item_id': 'demo-a'}], Path(temporary) / 'manifests',
                                'http://compass.example.test', fetcher=lambda _: b'{}')

            with self.assertRaisesRegex(ValueError, 'valid JSON'):
                fetch_manifests([{'item_id': 'demo-a'}], Path(temporary) / 'manifests',
                                'https://compass.example.test', fetcher=lambda _: b'not-json')
