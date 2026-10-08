"""Integration checks against an explicitly configured search API and Solr."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import unittest

BASE = os.environ.get('OCR_SEARCH_BASE_URL')
EVIDENCE_FILE = Path(os.environ['OCR_EVIDENCE_FILE']) if os.environ.get('OCR_EVIDENCE_FILE') else None
SOLR = os.environ.get('OCR_SEARCH_SOLR_URL', 'http://127.0.0.1:18983/solr/ocr')
FIXTURE_DIR = Path(os.environ.get('OCR_FIXTURE_DIR', Path(__file__).resolve().parent / 'fixtures' / 'hocr'))
RUN_INTEGRATION = bool(BASE and EVIDENCE_FILE and EVIDENCE_FILE.is_file())


def read_evidence():
    return json.loads(EVIDENCE_FILE.read_text())


def get(params):
    url = BASE.rstrip('/') + '?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.load(error)


def nonempty_reference(evidence):
    return next((row for row in evidence['reference_queries'] if row['pages']), None)


@unittest.skipUnless(
    RUN_INTEGRATION,
    'set OCR_SEARCH_BASE_URL and OCR_EVIDENCE_FILE for a live integration run',
)
class HttpTests(unittest.TestCase):
    def test_reference_queries(self):
        evidence = read_evidence()
        for row in evidence['reference_queries']:
            with self.subTest(item=row['item'], query=row['query']):
                status, result = get({'q': row['query'], 'item': row['item'], 'per_page': 25})
                self.assertEqual(status, 200)
                self.assertEqual(sorted(r['page_number'] for r in result['results']), row['pages'])
                self.assertFalse(result['partial'])
                for hit in result['results']:
                    self.assertTrue(hit['snippets'])
                    self.assertIn('<mark>', hit['snippets'][0]['html'])
                    self.assertNotIn('ocr', hit)

    def test_global_search_and_pagination(self):
        evidence = read_evidence()
        query_counts = {}
        for row in evidence['reference_queries']:
            query_counts[row['query']] = query_counts.get(row['query'], 0) + len(row['pages'])
        query = next((query for query, count in query_counts.items() if count >= 2), None)
        if query is None:
            self.skipTest('evidence has no query with at least two reference pages')
        status, result = get({'q': query, 'per_page': 1})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(result['total_pages'], 2)
        self.assertEqual(len(result['results']), 1)
        _, second = get({'q': query, 'per_page': 1, 'page': 2})
        self.assertNotEqual(result['results'][0]['id'], second['results'][0]['id'])

    def test_collection_scope(self):
        evidence = read_evidence()
        row = nonempty_reference(evidence)
        if row is None:
            self.skipTest('evidence has no reference result')
        item = next(item for item in evidence['items'] if item['id'] == row['item'])
        collection = urllib.parse.urlparse(item['aspace_collection']).path
        status, result = get({'q': row['query'], 'collection': collection})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(result['total_pages'], 1)
        unknown = collection.rsplit('/', 1)[0] + '/999999'
        self.assertEqual(get({'q': row['query'], 'collection': unknown})[1]['total_pages'], 0)

    def test_public_api_excludes_restricted_and_other_corpus_documents(self):
        from index import build_documents, request

        evidence = read_evidence()
        row = nonempty_reference(evidence)
        if row is None:
            self.skipTest('evidence has no reference result')
        docs = build_documents(evidence, FIXTURE_DIR, {row['item']}, 'ocr-search-regression')
        matching = next(d for d in docs if d['page_number'] == row['pages'][0])
        status, baseline = get({'q': row['query'], 'per_page': 25})
        self.assertEqual(status, 200)
        restricted = dict(matching, id='ocr-search-restricted-regression',
                          corpus_id=baseline['corpus'], access='restricted')
        try:
            request(SOLR, '/update?commit=true', [matching, restricted])
            status, result = get({'q': row['query'], 'per_page': 25})
            self.assertEqual(status, 200)
            self.assertEqual(result['total_pages'], baseline['total_pages'])
            self.assertNotIn(restricted['id'], [hit['id'] for hit in result['results']])
        finally:
            request(SOLR, '/update?commit=true',
                    {'delete': [{'id': matching['id']}, {'id': restricted['id']}]})

    def test_reindex_updates_and_removes_only_owned_pages(self):
        from index import build_documents, request, synchronize

        evidence = read_evidence()
        row = nonempty_reference(evidence)
        if row is None:
            self.skipTest('evidence has no reference result')
        docs = build_documents(evidence, FIXTURE_DIR, {row['item']}, 'ocr-search-regression')
        status, baseline = get({'q': row['query']})
        self.assertEqual(status, 200)
        try:
            synchronize(SOLR, 'ocr-search-regression', docs)
            again = synchronize(SOLR, 'ocr-search-regression', docs)
            self.assertEqual(again['removed'], 0)
            docs[0]['title'] = 'Updated synthetic regression fixture'
            result = synchronize(SOLR, 'ocr-search-regression', docs[:-1])
            self.assertEqual(result['removed'], 1)
            params = urllib.parse.urlencode({
                'q': 'corpus_id:"ocr-search-regression"', 'fl': 'id,title', 'rows': 25, 'wt': 'json'
            })
            state = request(SOLR, '/select?' + params)['response']
            self.assertEqual(state['numFound'], len(docs) - 1)
            self.assertEqual(next(d['title'] for d in state['docs'] if d['id'] == docs[0]['id']),
                             'Updated synthetic regression fixture')
            self.assertEqual(get({'q': row['query']})[1]['total_pages'], baseline['total_pages'])
        finally:
            request(SOLR, '/update?commit=true', {'delete': [{'id': d['id']} for d in docs]})

    def test_rejects_query_injection_and_unbounded_requests(self):
        for params in [{'q': '*:*'}, {'q': 'sample', 'fq': 'access:restricted'},
                       {'q': 'sample', 'per_page': 10000}, {'q': '"broken'}]:
            with self.subTest(params=params):
                self.assertEqual(get(params)[0], 400)


if __name__ == '__main__':
    unittest.main()
