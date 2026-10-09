import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
EVIDENCE_FILE = Path(os.environ.get('OCR_EVIDENCE_FILE', FIXTURES / 'evidence.json'))
SOURCE_DIR = Path(os.environ.get('OCR_FIXTURE_DIR', FIXTURES / 'hocr'))

class IndexTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name('index.py')
        self.assertTrue(path.exists(), 'OCR indexer has not been implemented')
        spec = importlib.util.spec_from_file_location('ocr_index', path)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.evidence = EVIDENCE_FILE
        self.source = SOURCE_DIR

    def read_evidence(self):
        return json.loads(self.evidence.read_text())

    def build(self, evidence=None, allowed=None):
        evidence = evidence if evidence is not None else self.read_evidence()
        if allowed is None:
            allowed = {item['id'] for item in evidence['items']}
        return self.api.build_documents(evidence, self.source, allowed, 'test-corpus')

    def test_fixture_keeps_stable_page_identity_and_boxes(self):
        evidence = self.read_evidence()
        docs = self.build()
        self.assertEqual(len(docs), len(evidence['pages']))
        self.assertEqual(len({d['id'] for d in docs}), len(docs))
        page = docs[0]
        self.assertRegex(page['ocr'], r'\bbbox \d+ \d+ \d+ \d+')
        self.assertEqual(page['canvas_id'], evidence['pages'][0]['canvas'])
        self.assertEqual(docs, self.build(evidence))
        if {item['id'] for item in evidence['items']} == {'demo-a', 'demo-b'}:
            page = next(d for d in docs if d['item_id'] == 'demo-a' and d['page_number'] == 2)
            self.assertIn('silver', page['ocr'])
            self.assertIn('falcon', page['ocr'])
            self.assertIn('bbox 110 100 180 130', page['ocr'])
        else:
            page = next(d for d in docs if d['item_id'] == 'scw' and d['page_number'] == 8)
            self.assertIn('Mascot', page['ocr'])
            self.assertIn('bbox 3328 3007 3513 3055', page['ocr'])

    def test_allowlist_excludes_other_items(self):
        evidence = self.read_evidence()
        item_id = evidence['items'][0]['id']
        expected_pages = [page for page in evidence['pages'] if page['item'] == item_id]
        docs = self.build(evidence, allowed={item_id})
        self.assertEqual(len(docs), len(expected_pages))
        self.assertEqual({d['item_id'] for d in docs}, {item_id})

    def test_publication_and_issue_date_reach_solr_documents(self):
        evidence = self.read_evidence()
        item = evidence['items'][0]
        item['series'] = 'Sample Publication'
        item['issue_date'] = '1927-12-07'

        docs = self.build(evidence)
        item_docs = [doc for doc in docs if doc['item_id'] == item['id']]

        self.assertTrue(item_docs)
        for doc in item_docs:
            self.assertEqual(doc['series'], 'Sample Publication')
            self.assertEqual(doc['issue_date'], '1927-12-07')
            self.assertEqual(doc['year'], 1927)

    def test_synthetic_evidence_includes_item_publication_fields(self):
        evidence = self.read_evidence()
        items = {item['id']: item for item in evidence['items']}
        if set(items) != {'demo-a', 'demo-b'}:
            self.skipTest('uses the committed synthetic evidence fixture')

        self.assertEqual(items['demo-a']['series'], 'Copper Kite Gazette')
        self.assertEqual(items['demo-a']['issue_date'], '1927-12-07')
        self.assertEqual(items['demo-b']['series'], 'Midnight Owl Reader')
        self.assertEqual(items['demo-b']['issue_date'], '1928-01')

    def test_partial_issue_dates_derive_year(self):
        for issue_date in ('1927', '1927-12'):
            with self.subTest(issue_date=issue_date):
                evidence = self.read_evidence()
                item = evidence['items'][0]
                item['series'] = 'Sample Publication'
                item['issue_date'] = issue_date

                docs = self.build(evidence)
                doc = next(doc for doc in docs if doc['item_id'] == item['id'])

                self.assertEqual(doc['issue_date'], issue_date)
                self.assertEqual(doc['year'], 1927)

    def test_missing_publication_and_issue_date_fields_are_omitted(self):
        evidence = self.read_evidence()
        item = evidence['items'][0]
        item.pop('series', None)
        item.pop('issue_date', None)

        docs = self.build(evidence)
        item_docs = [doc for doc in docs if doc['item_id'] == item['id']]

        self.assertTrue(item_docs)
        for doc in item_docs:
            self.assertNotIn('series', doc)
            self.assertNotIn('issue_date', doc)
            self.assertNotIn('year', doc)

    def test_modified_ocr_is_rejected(self):
        data = self.read_evidence()
        data['pages'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.build(data)

    def test_invalid_page_mapping_is_rejected(self):
        data = self.read_evidence()
        data['pages'][0]['canvas'] = 'https://example.test/wrong'
        with self.assertRaisesRegex(ValueError, 'canvas'):
            self.build(data)

    def test_duplicate_page_is_rejected(self):
        data = self.read_evidence()
        data['pages'].append(copy.deepcopy(data['pages'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.build(data)

    def test_path_escape_is_rejected(self):
        data = self.read_evidence()
        data['pages'][0]['local_file'] = '../outside.hocr'
        with self.assertRaisesRegex(ValueError, 'path'):
            self.build(data)

    def test_empty_or_unknown_allowlist_is_rejected(self):
        for allowed in [set(), {'unknown'}]:
            with self.assertRaises(ValueError):
                self.api.build_documents(self.read_evidence(), self.source, allowed, 'test-corpus')

if __name__ == '__main__':
    unittest.main()
