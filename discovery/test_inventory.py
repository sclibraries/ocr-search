import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class FakeObjectStore:
    def __init__(self, sizes):
        self.sizes = sizes
        self.head_calls = []
        self.put_calls = []
        self.head_errors = {}

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if kwargs['Key'] in self.head_errors:
            raise self.head_errors[kwargs['Key']]
        size = self.sizes[kwargs['Key']]
        return {'ContentLength': size, 'VersionId': 'version-' + str(size), 'ETag': f'"etag-{size}"'}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        return {}


class FakeObjectStoreError(Exception):
    response = {'Error': {'Code': 'AccessDenied'}, 'Message': 'credential-like text is not copied'}


def synthetic_inputs(root):
    manifests = root / 'manifests'
    manifests.mkdir()
    items, pages, rows, sizes = [], [], [], {}
    for item_number in range(15):
        item_id = f'demo-item-{item_number:02d}'
        manifest_url = f'https://example.test/manifests/{item_id}.json'
        record = f'https://findingaids.example.test/objects/{item_number + 1}'
        canvas_rows = []
        items.append({'id': item_id, 'manifest_url': manifest_url, 'aspace_record': record})
        for page_number in (1, 2):
            canvas_id = f'https://example.test/canvases/{item_id}/{page_number}'
            size = 100 + item_number * 2 + page_number
            key = f'synthetic/{item_id}/page-{page_number}.hocr'
            sizes[key] = size
            digest = hashlib.sha256(f'{item_id} page {page_number}'.encode()).hexdigest()
            canvas_rows.append({'@id': canvas_id, 'label': f'{item_id}, page {page_number}',
                                'width': 600, 'height': 800})
            pages.append({'item': item_id, 'page': page_number, 'canvas': canvas_id,
                          'label': f'{item_id}, page {page_number}', 'sha256': digest,
                          'aspace_record': record})
            access_terms = ['public']
            publication_state = 'published'
            if item_number == 0 and page_number == 2:
                access_terms, publication_state = ['restricted'], 'published'
            elif item_number == 1 and page_number == 1:
                access_terms, publication_state = ['open-use'], 'published'
            ancestry = [[f'collection-{item_number % 3}', f'item-{item_id}']]
            if item_number == 0:
                ancestry = [[f'collection-{item_number % 3}', 'series-a', 'series-b', f'item-{item_id}']]
            elif item_number == 1:
                ancestry.append([f'collection-{(item_number + 1) % 3}', f'item-{item_id}'])
            rows.append({
                'schema_version': 1,
                'file_id': item_number * 2 + page_number,
                'page_id': f'page-{item_id}-{page_number}',
                'item_id': item_id,
                'page_order': page_number,
                'canvas_id': canvas_id,
                'manifest_url': manifest_url,
                's3_key': key,
                'recorded_bytes': size,
                'media_use': 'Extracted hOCR',
                'campus_ids': ['synthetic-campus'],
                'collection_ancestry': ancestry,
                'ancestry_depth_limit_reached': item_number == 14 and page_number == 2,
                'aspace_record': record,
                'publication_state': publication_state,
                'access_terms': access_terms,
            })
        manifest = {'@context': 'http://iiif.io/api/presentation/2/context.json',
                    '@id': manifest_url, '@type': 'sc:Manifest',
                    'sequences': [{'@type': 'sc:Sequence', 'canvases': canvas_rows}]}
        (manifests / f'{item_id}.json').write_text(json.dumps(manifest))
    evidence = {'schema_version': 1, 'items': items, 'pages': pages}
    evidence_path = root / 'evidence.json'
    evidence_path.write_text(json.dumps(evidence))
    return rows, evidence, manifests, FakeObjectStore(sizes)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.rows, self.evidence, self.manifests, self.object_store = synthetic_inputs(self.root)
        self.access_mapping = {
            'schema_version': 1,
            'published_without_terms': 'restricted',
            'terms': [
                {'name': 'public', 'access': 'public'},
                {'name': 'restricted', 'access': 'restricted'},
            ],
        }

    def inventory_api(self):
        spec = importlib.util.find_spec('discovery.inventory')
        self.assertIsNotNone(spec, 'discovery.inventory command support has not been implemented')
        from discovery.inventory import build_inventory, write_inventory
        return build_inventory, write_inventory

    def test_inventory_reconciles_synthetic_pages_and_fails_closed_on_access(self):
        build_inventory, _ = self.inventory_api()
        result = build_inventory(self.rows, self.evidence, self.manifests, self.object_store,
                                 source_bucket='synthetic-source', access_mapping=self.access_mapping)

        self.assertEqual(len(result['pages']), 30)
        expected = {(page['item'], page['page']): page for page in self.evidence['pages']}
        for page in result['pages']:
            source = expected[(page['item_id'], page['page_number'])]
            self.assertEqual(page['canvas_id'], source['canvas'])
            self.assertEqual(page['sha256'], source['sha256'])
        access = {(page['item_id'], page['page_number']): page['access'] for page in result['pages']}
        self.assertEqual(access[('demo-item-00', 2)], 'restricted')
        self.assertEqual(access[('demo-item-01', 1)], 'unknown')
        self.assertEqual(access[('demo-item-02', 1)], 'public')
        self.assertEqual(len(self.object_store.head_calls), 30)
        self.assertEqual(result['pages'][0]['s3_etag'], '"etag-101"')
        self.assertEqual(result['reconciliation']['pages_by_collection'], {
            'collection-0': 10, 'collection-1': 10, 'collection-2': 12})
        self.assertIn('demo-item-00', result['reconciliation']['deep_items'])
        self.assertIn('demo-item-01', result['reconciliation']['multiple_parent_items'])
        self.assertIn('demo-item-14', result['reconciliation']['ancestry_truncated_items'])
        self.assertEqual(len(result['reconciliation']['sample_canvas_ids']), 10)

    def test_access_mapping_controls_terms_and_published_pages_without_terms(self):
        from discovery.inventory import classify_access
        self.assertEqual(classify_access('published', ['public'], [], self.access_mapping), 'public')
        self.assertEqual(classify_access('published', ['public', 'open-use'], [], self.access_mapping), 'unknown')
        self.assertEqual(classify_access('published', ['restricted'], [], self.access_mapping), 'restricted')
        self.assertEqual(classify_access('published', [], [], self.access_mapping), 'restricted')
        self.assertEqual(classify_access('unpublished', ['public'], [], self.access_mapping), 'restricted')

    def test_missing_evidence_checksum_stays_empty_and_head_metadata_is_kept(self):
        build_inventory, _ = self.inventory_api()
        evidence = json.loads(json.dumps(self.evidence))
        del evidence['pages'][0]['sha256']
        result = build_inventory([self.rows[0]], evidence, self.manifests, self.object_store,
                                 source_bucket='synthetic-source', access_mapping=self.access_mapping)
        page = result['pages'][0]
        self.assertEqual(page['sha256'], '')
        self.assertEqual(page['s3_etag'], '"etag-101"')
        self.assertEqual(page['s3_version_id'], 'version-101')
        self.assertEqual(page['s3_size'], 101)

    def test_manifest_metadata_is_configurable_normalized_and_reconciled_per_item(self):
        build_inventory, _ = self.inventory_api()
        first_path = self.manifests / 'demo-item-00.json'
        first = json.loads(first_path.read_text())
        first['label'] = {'en': ['Synthetic Weekly']}
        first['metadata'] = [
            {'label': 'Publication title', 'value': 'Synthetic Weekly'},
            {'label': 'Issue date', 'value': '1927-12'},
        ]
        first_path.write_text(json.dumps(first))

        second_path = self.manifests / 'demo-item-01.json'
        second = json.loads(second_path.read_text())
        second['label'] = ''
        second['metadata'] = [{'label': 'Date', 'value': 'circa 1927'}]
        second_path.write_text(json.dumps(second))

        third_path = self.manifests / 'demo-item-02.json'
        third = json.loads(third_path.read_text())
        third['label'] = 'Sample Journal'
        third['metadata'] = [{'label': 'Publication', 'value': 'Sample Journal'},
                             {'label': 'Date', 'value': '1927'}]
        third_path.write_text(json.dumps(third))

        result = build_inventory(
            self.rows[:6], self.evidence, self.manifests, self.object_store,
            source_bucket='synthetic-source', access_mapping=self.access_mapping,
            manifest_metadata_labels={
                'series': ['Publication', 'Publication title'],
                'issue_date': ['Issue date', 'Date'],
            })

        pages = {(page['item_id'], page['page_number']): page for page in result['pages']}
        self.assertEqual(pages[('demo-item-00', 1)]['item_title'], 'Synthetic Weekly')
        self.assertEqual(pages[('demo-item-00', 1)]['series'], 'Synthetic Weekly')
        self.assertEqual(pages[('demo-item-00', 1)]['issue_date'], '1927-12')
        self.assertEqual(pages[('demo-item-01', 1)]['item_title'], '')
        self.assertEqual(pages[('demo-item-01', 1)]['series'], '')
        self.assertEqual(pages[('demo-item-01', 1)]['issue_date'], '')
        self.assertEqual(pages[('demo-item-02', 1)]['issue_date'], '1927')
        self.assertEqual(result['reconciliation']['missing_item_titles'], 1)
        self.assertEqual(result['reconciliation']['missing_series'], 1)
        self.assertEqual(result['reconciliation']['missing_issue_dates'], 1)
        self.assertEqual(result['reconciliation']['dates_unparsed'], 1)

    def test_metadata_label_settings_are_read_from_json(self):
        from discovery.inventory import read_manifest_metadata_labels

        path = self.root / 'manifest-metadata-labels.json'
        path.write_text(json.dumps({
            'series': ['Newspaper title', 'Publication'],
            'issue_date': ['Issue date', 'Date'],
        }))
        self.assertEqual(read_manifest_metadata_labels(path), {
            'series': ['Newspaper title', 'Publication'],
            'issue_date': ['Issue date', 'Date'],
        })

        path.write_text(json.dumps({'series': 'Publication'}))
        with self.assertRaisesRegex(ValueError, 'list of label names'):
            read_manifest_metadata_labels(path)

    def test_inventory_requires_a_reviewed_access_mapping(self):
        build_inventory, _ = self.inventory_api()
        with self.assertRaisesRegex(ValueError, 'access mapping'):
            build_inventory(self.rows[:1], self.evidence, self.manifests, self.object_store,
                             source_bucket='synthetic-source', access_mapping=None)

    def test_compass_manifest_fills_canvas_when_evidence_and_converted_manifest_are_absent(self):
        from discovery.manifests import fetch_manifests

        rows = [dict(row, canvas_id=None, manifest_url=None) for row in self.rows[:2]]
        item_id = rows[0]['item_id']
        compass_manifest = {
            '@id': f'https://compass.example.test/node/{item_id}/manifest',
            'label': 'Fixture weekly item',
            'metadata': [
                {'label': 'Publication', 'value': 'Fixture Weekly'},
                {'label': 'Issue date', 'value': 'December 7, 1927'},
            ],
            'sequences': [{'canvases': [
                {'@id': f'https://canvas.example.test/{item_id}/1', 'label': 'Page 1'},
                {'@id': f'https://canvas.example.test/{item_id}/2', 'label': 'Page 2'},
            ]}],
        }
        raw_manifest = json.dumps(compass_manifest).encode()
        with tempfile.TemporaryDirectory() as temporary:
            manifest_dir = Path(temporary) / 'compass'
            fetch_manifests(rows, manifest_dir, 'https://compass.example.test',
                            fetcher=lambda _: raw_manifest, sleep_fn=lambda _: None)
            from discovery.inventory import build_inventory
            result = build_inventory(rows, None, None, self.object_store,
                                     source_bucket='synthetic-source',
                                     compass_manifest_directory=manifest_dir,
                                     manifest_metadata_labels={
                                         'series': ['Publication'],
                                         'issue_date': ['Issue date'],
                                     },
                                     access_mapping=self.access_mapping)

        pages = result['pages']
        self.assertEqual([page['canvas_id'] for page in pages], [
            f'https://canvas.example.test/{item_id}/1',
            f'https://canvas.example.test/{item_id}/2',
        ])
        self.assertEqual([page['sha256'] for page in pages], ['', ''])
        self.assertEqual([page['access'] for page in pages], ['public', 'restricted'])
        self.assertEqual(pages[0]['manifest_url'], compass_manifest['@id'])
        self.assertEqual(pages[0]['item_title'], 'Fixture weekly item')
        self.assertEqual(pages[0]['series'], 'Fixture Weekly')
        self.assertEqual(pages[0]['issue_date'], '1927-12-07')
        self.assertEqual(pages[0]['s3_etag'], '"etag-101"')

    def test_inventory_reproduces_committed_synthetic_fixture_evidence(self):
        build_inventory, _ = self.inventory_api()
        fixtures = Path(__file__).resolve().parent.parent / 'indexer' / 'fixtures'
        evidence = json.loads((fixtures / 'evidence.json').read_text())
        items = {item['id']: item for item in evidence['items']}
        rows, sizes = [], {}
        for index, page in enumerate(evidence['pages'], 1):
            item = items[page['item']]
            key = f"synthetic/{page['item']}/{page['local_file']}"
            size = (fixtures / 'hocr' / page['local_file']).stat().st_size
            sizes[key] = size
            rows.append({
                'schema_version': 1,
                'file_id': index,
                'page_id': f"synthetic-page-{index}",
                'item_id': page['item'],
                'page_order': page['page'],
                's3_key': key,
                'recorded_bytes': size,
                'media_use': 'synthetic-hOCR',
                'campus_ids': ['synthetic-campus'],
                'collection_ancestry': [[f"collection-{page['item']}", page['item']]],
                'publication_state': 'published',
                'access_terms': [],
            })
        store = FakeObjectStore(sizes)
        result = build_inventory(rows, evidence, fixtures / 'hocr', store,
                                 source_bucket='synthetic-source', access_mapping=self.access_mapping)
        actual = {(page['item_id'], page['page_number']): page for page in result['pages']}
        for source in evidence['pages']:
            page = actual[(source['item'], source['page'])]
            self.assertEqual(page['canvas_id'], source['canvas'])
            self.assertEqual(page['sha256'], source['sha256'])
            self.assertEqual(page['access'], 'restricted')

    def test_manifest_canvas_mismatch_is_rejected(self):
        build_inventory, _ = self.inventory_api()
        manifest_path = self.manifests / 'demo-item-00.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['sequences'][0]['canvases'][0]['@id'] = 'https://example.test/wrong-canvas'
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'canvas'):
            build_inventory(self.rows, self.evidence, self.manifests, self.object_store,
                             source_bucket='synthetic-source', access_mapping=self.access_mapping)

    def test_one_source_file_can_have_multiple_page_associations(self):
        build_inventory, _ = self.inventory_api()
        first = dict(self.rows[0])
        second = dict(self.rows[1])
        second['file_id'] = first['file_id']
        second['s3_key'] = first['s3_key']
        second['recorded_bytes'] = first['recorded_bytes']
        result = build_inventory([first, second], self.evidence, self.manifests,
                                 self.object_store, source_bucket='synthetic-source',
                                 access_mapping=self.access_mapping)
        self.assertEqual(result['reconciliation']['input_file_count'], 1)
        self.assertEqual(result['reconciliation']['page_count'], 2)
        self.assertEqual([page['page_number'] for page in result['pages']], [1, 2])

    def test_head_errors_are_reported_without_disclosing_service_messages(self):
        build_inventory, _ = self.inventory_api()
        row = self.rows[0]
        self.object_store.head_errors[row['s3_key']] = FakeObjectStoreError()
        result = build_inventory([row], self.evidence, self.manifests, self.object_store,
                                 source_bucket='synthetic-source', access_mapping=self.access_mapping)
        page = result['pages'][0]
        self.assertEqual(page['s3_status'], 'unavailable')
        self.assertEqual(page['s3_error'], 'AccessDenied')
        self.assertIn('s3_head_error', page['issues'])
        self.assertEqual(result['reconciliation']['s3_head_errors'], 1)

    def test_inventory_output_accepts_local_paths_and_configured_s3_uris(self):
        build_inventory, write_inventory = self.inventory_api()
        result = build_inventory(self.rows, self.evidence, self.manifests, self.object_store,
                                 source_bucket='synthetic-source', access_mapping=self.access_mapping)
        local_file = self.root / 'out' / 'pages.jsonl'
        write_inventory(result, str(local_file), self.object_store)
        self.assertEqual(len(local_file.read_text().splitlines()), 30)
        self.assertTrue((local_file.parent / 'pages.reconciliation.json').is_file())

        write_inventory(result, 's3://review-bucket/private/pages.jsonl', self.object_store)
        output_call = next(call for call in self.object_store.put_calls
                           if call['Key'] == 'private/pages.jsonl')
        self.assertEqual(output_call['Bucket'], 'review-bucket')
        self.assertEqual(len(output_call['Body'].decode().splitlines()), 30)
