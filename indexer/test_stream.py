"""Streaming indexer acceptance against LocalStack S3 and a disposable Solr core."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
import urllib.parse
import urllib.request
import uuid

from indexer.stream import SolrClient, _build_document, index_inventory, page_document_id


S3_ENDPOINT = os.environ.get('OCR_010_TEST_S3_ENDPOINT')
SOLR_URL = os.environ.get('OCR_010_TEST_SOLR_URL')


def synthetic_hocr(word):
    return (
        '<html><body><div class="ocr_page" title="bbox 0 0 100 100">'
        f'<span class="ocrx_word" title="bbox 10 10 40 30">{word}</span>'
        '</div></body></html>'
    ).encode()


@unittest.skipUnless(S3_ENDPOINT and SOLR_URL,
                     'set local OCR_010_TEST_S3_ENDPOINT and OCR_010_TEST_SOLR_URL')
class StreamingIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s3_url = urllib.parse.urlsplit(S3_ENDPOINT)
        solr_url = urllib.parse.urlsplit(SOLR_URL)
        if (s3_url.scheme != 'http' or s3_url.hostname not in {'127.0.0.1', 'localhost'}
                or s3_url.port != 14567):
            raise RuntimeError('OCR-010 tests only allow LocalStack at loopback port 14567')
        if (solr_url.scheme != 'http' or solr_url.hostname not in {'127.0.0.1', 'localhost'}
                or solr_url.port != 18984 or solr_url.path != '/solr/ocr-010-test'):
            raise RuntimeError('OCR-010 tests only allow the throwaway core at loopback port 18984')
        import boto3

        cls.s3 = boto3.client('s3', region_name='us-east-1',
                              endpoint_url=S3_ENDPOINT,
                              aws_access_key_id='test', aws_secret_access_key='test')
        cls.solr = SolrClient(SOLR_URL, timeout=5)
        with urllib.request.urlopen(SOLR_URL + '/schema/fields/year', timeout=5) as response:
            year_field = json.load(response)['field']
        if year_field.get('type') != 'integer':
            raise RuntimeError('throwaway Solr must load the repository schema.xml')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ocr-010-synthetic-')
        self.root = Path(self.temp.name)
        self.bucket = 'ocr-010-' + uuid.uuid4().hex[:16]
        self.corpus = 'ocr-010-test-' + uuid.uuid4().hex[:12]
        self.s3.create_bucket(Bucket=self.bucket)
        self.s3.put_bucket_versioning(Bucket=self.bucket,
                                      VersioningConfiguration={'Status': 'Enabled'})

    def tearDown(self):
        # The disposable test core is discarded after the suite; corpus IDs are unique.
        for version in self.s3.list_object_versions(Bucket=self.bucket).get('Versions', []):
            self.s3.delete_object(Bucket=self.bucket, Key=version['Key'],
                                  VersionId=version['VersionId'])
        self.s3.delete_bucket(Bucket=self.bucket)
        self.temp.cleanup()

    def store(self, key, body):
        result = self.s3.put_object(Bucket=self.bucket, Key=key, Body=body)
        return {'s3_bucket': self.bucket, 's3_key': key,
                's3_version_id': result['VersionId'], 's3_etag': result['ETag'],
                's3_size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}

    def row(self, item_id, page_number, canvas_id, body=None, access='public', **overrides):
        key = f'synthetic/{item_id}-{page_number}.hocr'
        source = self.store(key, body or synthetic_hocr('synthetic')) if body is not None else {
            's3_bucket': self.bucket, 's3_key': key, 's3_version_id': 'missing-version',
            's3_etag': '"missing"', 's3_size': 0, 'sha256': ''}
        row = {
            'schema_version': 1,
            'file_id': f'synthetic-file-{item_id}-{page_number}',
            'page_id': f'synthetic-page-{item_id}-{page_number}',
            'item_id': item_id,
            'page_number': page_number,
            'page_label': f'Synthetic page {page_number}',
            'canvas_id': canvas_id,
            'manifest_url': f'https://example.test/{item_id}/manifest',
            'aspace_record': f'https://example.test/items/{item_id}',
            'collection_ancestry': [['synthetic-collection', item_id]],
            'item_title': f'Synthetic {item_id}',
            'series': 'Synthetic Gazette',
            'issue_date': '1901-02-03',
            'access': access,
            **source,
        }
        row.update(overrides)
        return row

    def write_inventory(self, name, rows):
        path = self.root / f'{name}.jsonl'
        path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))
        return path

    def run_index(self, inventory, name, **options):
        return index_inventory(inventory, self.s3, self.solr, self.root / f'{name}.sqlite',
                               self.corpus, batch_size=2, retries=0, backoff_seconds=0,
                               **options)

    def documents(self):
        query = urllib.parse.urlencode({
            'q': f'corpus_id:"{self.corpus}"', 'fl': '*', 'rows': 100,
            'wt': 'json', 'sort': 'id asc',
        })
        with urllib.request.urlopen(f'{SOLR_URL}/select?{query}', timeout=5) as response:
            return json.load(response)['response']['docs']

    def test_indexes_only_public_pages_from_exact_s3_versions_with_v2_fields(self):
        body = synthetic_hocr('original')
        source = self.store('synthetic/item-a-1.hocr', body)
        replacement = self.store('synthetic/item-a-1.hocr', synthetic_hocr('replacement'))
        self.assertNotEqual(source['s3_version_id'], replacement['s3_version_id'])
        public = self.row('item-a', 1, 'https://example.test/canvas/a/1', body=body,
                          s3_bucket=source['s3_bucket'], s3_key=source['s3_key'],
                          s3_version_id=source['s3_version_id'], s3_etag=source['s3_etag'],
                          s3_size=source['s3_size'], sha256=source['sha256'])
        restricted = self.row('item-b', 1, 'https://example.test/canvas/b/1',
                              access='restricted')
        inventory = self.write_inventory('public-and-restricted', [public, restricted])

        result = self.run_index(inventory, 'stream-run')

        self.assertEqual((result['indexed'], result['skipped'], result['failed']), (1, 1, 0))
        docs = self.documents()
        self.assertEqual(len(docs), 1)
        document = docs[0]
        self.assertEqual(document['id'], page_document_id(
            self.corpus, 'item-a', 'https://example.test/canvas/a/1'))
        self.assertEqual(document['schema_version'], 2)
        self.assertEqual(document['access'], 'public')
        self.assertEqual(document['collection_id'], 'synthetic-collection')
        self.assertEqual(document['title'], 'Synthetic item-a')
        self.assertEqual(document['series'], 'Synthetic Gazette')
        self.assertEqual(document['issue_date'], '1901-02-03')
        self.assertEqual(document['year'], 1901)
        self.assertEqual(document['source_bucket'], self.bucket)
        self.assertEqual(document['source_key'], source['s3_key'])
        self.assertEqual(document['source_version_id'], source['s3_version_id'])
        self.assertEqual(document['source_etag'], source['s3_etag'])
        self.assertEqual(document['source_sha256'], source['sha256'])
        self.assertIn('original', document['ocr'])
        self.assertNotIn('replacement', document['ocr'])
        self.assertFalse(list(self.root.rglob('*.hocr')))

    def test_dry_run_verifies_s3_without_writing_to_solr(self):
        row = self.row('item-dry', 1, 'https://example.test/canvas/dry/1',
                       body=synthetic_hocr('dryrun'))
        result = self.run_index(self.write_inventory('dry-run', [row]), 'dry-run', dry_run=True)
        self.assertEqual(result['validated'], 1)
        self.assertEqual(result['indexed'], 0)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(self.documents(), [])

    def test_checksum_and_missing_version_fail_without_indexing(self):
        good = self.row('item-bad', 1, 'https://example.test/canvas/bad/1',
                        body=synthetic_hocr('checksum'))
        wrong_hash = dict(good, sha256='0' * 64)
        wrong_version = dict(good, page_id='synthetic-page-version',
                             canvas_id='https://example.test/canvas/bad/2',
                             s3_version_id='missing-version-id')
        result = self.run_index(self.write_inventory('bad-integrity', [wrong_hash, wrong_version]),
                                'bad-integrity')
        self.assertEqual(result['failed'], 2)
        self.assertEqual(result['indexed'], 0)
        self.assertEqual(self.documents(), [])

    def test_optional_dates_and_series_use_repository_schema(self):
        year_only = self.row('item-year-only', 1, 'https://example.test/canvas/year-only/1',
                             body=synthetic_hocr('yearonly'), issue_date='1927')
        partial = self.row('item-partial', 1, 'https://example.test/canvas/partial/1',
                           body=synthetic_hocr('partial'), issue_date='1927-12', series='')
        full = self.row('item-full', 1, 'https://example.test/canvas/full/1',
                        body=synthetic_hocr('full'), issue_date='1927-12-07')
        no_date = self.row('item-no-date', 1, 'https://example.test/canvas/no-date/1',
                           body=synthetic_hocr('nodate'), item_title='', aspace_record='',
                           s3_etag='')
        no_date.pop('issue_date')
        inventory = self.write_inventory('optional-fields', [year_only, partial, full, no_date])

        result = self.run_index(inventory, 'optional-fields')

        self.assertEqual((result['indexed'], result['failed']), (4, 0))
        docs = {doc['item_id']: doc for doc in self.documents()}
        for item_id in ('item-year-only', 'item-partial', 'item-full'):
            self.assertEqual(docs[item_id]['year'], 1927)
        self.assertEqual(docs['item-partial']['issue_date'], '1927-12')
        self.assertEqual(docs['item-full']['issue_date'], '1927-12-07')
        for field in ('issue_date', 'year', 'title', 'aspace_record', 'source_etag'):
            self.assertNotIn(field, docs['item-no-date'])
        self.assertNotIn('series', docs['item-partial'])

    def test_resume_is_idempotent_and_stale_pages_are_withdrawn(self):
        first = self.row('item-resume', 1, 'https://example.test/canvas/resume/1',
                         body=synthetic_hocr('one'))
        second = self.row('item-resume', 2, 'https://example.test/canvas/resume/2',
                          body=synthetic_hocr('two'))
        first_inventory = self.write_inventory('initial', [first, second])
        journal = self.root / 'resume.sqlite'
        initial = index_inventory(first_inventory, self.s3, self.solr, journal, self.corpus,
                                  batch_size=1, retries=0, backoff_seconds=0)
        repeated = index_inventory(first_inventory, self.s3, self.solr, journal, self.corpus,
                                   batch_size=1, retries=0, backoff_seconds=0)
        self.assertEqual(initial['indexed'], 2)
        self.assertEqual(repeated['indexed'], 2)
        self.assertEqual(len(self.documents()), 2)

        remaining = self.write_inventory('after-withdrawal', [first])
        result = self.run_index(remaining, 'after-withdrawal')
        self.assertEqual(result['indexed'], 1)
        self.assertEqual(result['withdrawn'], 1)
        self.assertEqual(len(self.documents()), 1)
        self.assertEqual(self.documents()[0]['canvas_id'], first['canvas_id'])

        restricted = dict(first, access='restricted', s3_version_id='not-a-real-version')
        restricted_inventory = self.write_inventory('restricted', [restricted])
        restricted_result = self.run_index(restricted_inventory, 'restricted')
        self.assertEqual(restricted_result['skipped'], 1)
        self.assertEqual(restricted_result['failed'], 0)
        self.assertEqual(self.documents(), [])

    def test_interrupted_run_resumes_after_a_checkpointed_batch(self):
        first = self.row('item-interrupt', 1, 'https://example.test/canvas/interrupt/1',
                         body=synthetic_hocr('first'))
        second = self.row('item-interrupt', 2, 'https://example.test/canvas/interrupt/2',
                          body=synthetic_hocr('second'))
        inventory = self.write_inventory('interrupted', [first, second])
        journal = self.root / 'interrupted.sqlite'
        delegate = self.s3
        interrupt = {'remaining': True}

        class InterruptOnce:
            def get_object(self, **kwargs):
                if kwargs['Key'] == second['s3_key'] and interrupt['remaining']:
                    interrupt['remaining'] = False
                    raise KeyboardInterrupt('synthetic process interruption')
                return delegate.get_object(**kwargs)

        with self.assertRaisesRegex(KeyboardInterrupt, 'synthetic process interruption'):
            index_inventory(inventory, InterruptOnce(), self.solr, journal, self.corpus,
                            batch_size=1, retries=0, backoff_seconds=0)
        self.assertEqual([doc['canvas_id'] for doc in self.documents()], [first['canvas_id']])

        resumed = index_inventory(inventory, self.s3, self.solr, journal, self.corpus,
                                  batch_size=1, retries=0, backoff_seconds=0)
        self.assertEqual(resumed['indexed'], 2)
        self.assertTrue(resumed['complete'])
        self.assertEqual({doc['canvas_id'] for doc in self.documents()},
                         {first['canvas_id'], second['canvas_id']})


class RetryPolicyTests(unittest.TestCase):
    def test_transient_failures_use_bounded_exponential_backoff(self):
        from indexer.stream import _retry

        attempts = []
        delays = []

        class SyntheticSlowDown(Exception):
            response = {'Error': {'Code': 'SlowDown'},
                        'ResponseMetadata': {'HTTPStatusCode': 503}}

        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise SyntheticSlowDown('synthetic throttling')
            return 'completed'

        self.assertEqual(_retry(operation, 2, 0.25, delays.append), 'completed')
        self.assertEqual(len(attempts), 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_nontransient_failure_is_not_retried(self):
        from indexer.stream import _retry

        attempts = []

        class SyntheticForbidden(Exception):
            response = {'Error': {'Code': 'AccessDenied'},
                        'ResponseMetadata': {'HTTPStatusCode': 403}}

        def operation():
            attempts.append(1)
            raise SyntheticForbidden('synthetic denial')

        with self.assertRaisesRegex(SyntheticForbidden, 'synthetic denial'):
            _retry(operation, 5, 0.25, lambda _: None)
        self.assertEqual(len(attempts), 1)

    def test_http_not_found_is_not_retried(self):
        from indexer.stream import _retry

        attempts = []

        def operation():
            attempts.append(1)
            error = HTTPError('https://example.test/object', 404, 'not found', {}, None)
            error.close()
            raise error

        with self.assertRaises(HTTPError):
            _retry(operation, 5, 0.25, lambda _: None)
        self.assertEqual(len(attempts), 1)


class DocumentFieldTests(unittest.TestCase):
    def test_empty_optional_fields_are_omitted_and_partial_dates_have_year(self):
        row = {
            '_stream_page_id': 'corpus:item:canvas-hash',
            'item_id': 'item',
            'page_number': 1,
            'canvas_id': 'https://example.test/canvas/1',
            'collection_ancestry': [['collection', 'item']],
            'manifest_url': 'https://example.test/manifest',
            'item_title': ' ',
            'aspace_record': '',
            'series': None,
            'issue_date': '',
            's3_bucket': '',
            's3_key': None,
            's3_version_id': '',
            's3_etag': '  ',
        }
        document = _build_document(row, 'corpus', 'a' * 64, '<html/>')
        for field in ('title', 'aspace_record', 'series', 'issue_date', 'year',
                      'source_bucket', 'source_key', 'source_version_id', 'source_etag'):
            self.assertNotIn(field, document)
        self.assertEqual(document['source_sha256'], 'a' * 64)

        for issue_date in ('1927', '1927-12', '1927-12-07'):
            row['issue_date'] = issue_date
            dated = _build_document(row, 'corpus', 'a' * 64, '<html/>')
            self.assertEqual(dated['year'], 1927)


if __name__ == '__main__':
    unittest.main()
