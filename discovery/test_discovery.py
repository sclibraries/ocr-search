import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from discovery.catalog import Catalog, s3_location
from discovery.validation import inspect_hocr
from discovery.sources import LocalSource, S3Source
from discovery.runner import audit

HOCR = b'<html><div class="ocr_page" title="bbox 0 0 100 200"><span class="ocrx_word" title="bbox 1 2 20 30">Mascot</span></div></html>'

def candidate(**changes):
    row = dict(file_id=1, filename='page.shtml', uri='private://2024-10/page.shtml',
               recorded_bytes=len(HOCR), local_file='page.hocr', campus_ids='169',
               media_ids='2', page_ids='3', item_ids='4', collection_id=1335646,
               exported_at='2026-09-22T00:00:00Z', image_width=100, image_height=200)
    return dict(row, **changes)

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.db = Catalog(self.root / 'audit.sqlite')
        self.addCleanup(self.db.close)
        (self.root / 'page.hocr').write_bytes(HOCR)

    def ingest(self, rows):
        path = self.root / 'inventory.jsonl'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return self.db.ingest(path, limit=100)

    def test_confirmed_prefix_defaults_and_unknown_scheme(self):
        self.assertEqual(s3_location('private://2024-10/page.shtml'),
                         ('compass-prod-i2-files', 's3fs-private/2024-10/page.shtml'))
        with self.assertRaises(ValueError): s3_location('https://example.org/file')

    def test_hocr_content_not_extension_and_bad_coordinates(self):
        self.assertEqual(inspect_hocr(HOCR)['word_count'], 1)
        self.assertEqual(inspect_hocr(b'<html>ordinary page</html>')['status'], 'not_hocr')
        result = inspect_hocr(HOCR.replace(b'20 30', b'120 300'))
        self.assertEqual(result['status'], 'invalid_coordinates')
        self.assertEqual(inspect_hocr(HOCR.replace(b'</html>', b''))['status'], 'malformed_html')

    def test_blank_zero_area_placeholders_are_counted_and_skipped(self):
        blank = b'<span class="ocrx_word" title="bbox 0 200 0 200"> &#160; </span>'
        data = HOCR.replace(b'</div>', blank * 7 + b'</div>')
        result = inspect_hocr(data)
        self.assertEqual(result['status'], 'valid_hocr')
        self.assertEqual(result['word_count'], 1)
        self.assertEqual(result.get('skipped_blank_words'), 7)

    def test_invalid_text_boxes_and_other_blank_errors_remain_rejected(self):
        for text, box in [('Mascot', '0 200 0 200'), ('Mascot', '1 2 120 300'),
                          (' ', '0 201 0 201'), (' ', '20 30 1 2'), (' ', '')]:
            with self.subTest(text=text, box=box):
                data = HOCR.replace(b'Mascot', text.encode()).replace(b'1 2 20 30', box.encode())
                self.assertEqual(inspect_hocr(data)['status'], 'invalid_coordinates')

    def test_placeholders_alone_are_empty_hocr(self):
        data = HOCR.replace(b'Mascot', b' ').replace(b'1 2 20 30', b'0 200 0 200')
        result = inspect_hocr(data)
        self.assertEqual(result['status'], 'empty_hocr')
        self.assertEqual(result['word_count'], 0)
        self.assertEqual(result.get('skipped_blank_words'), 1)

    def test_placeholder_count_survives_checkpoint_and_csv_report(self):
        import contextlib
        import csv
        from discovery.__main__ import reports
        blank = b'<span class="ocr_word" title="bbox 0 200 0 200"> </span>'
        data = HOCR.replace(b'</div>', blank + b'</div>')
        (self.root / 'page.hocr').write_bytes(data)
        self.ingest([candidate(recorded_bytes=len(data))])
        run = audit(self.db, LocalSource(self.root))
        self.assertEqual(self.db.report()[0].get('skipped_blank_words'), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            reports(self.db, self.root, run)
        with (self.root / 'report.csv').open() as stream:
            self.assertEqual(next(csv.DictReader(stream)).get('skipped_blank_words'), '1')

    def test_import_is_transactional_and_bounded(self):
        path = self.root / 'inventory.jsonl'
        path.write_text(json.dumps(candidate())+'\n'+json.dumps(candidate(file_id=2))+'\n')
        with self.assertRaises(ValueError): self.db.ingest(path, limit=1)
        self.assertEqual(self.db.count(), 0)
        path.write_text(json.dumps(candidate())+'\n'+json.dumps(candidate())+'\n')
        with self.assertRaises(ValueError): self.db.ingest(path, limit=100)
        self.assertEqual(self.db.count(), 0)

    def test_resume_and_changed_inventory(self):
        self.ingest([candidate()])
        source = LocalSource(self.root)
        result = audit(self.db, source, max_files=100, max_bytes=10000, max_file_bytes=1000)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(self.db.report()[0]['status'], 'valid_hocr')
        self.assertFalse(self.db.report()[0]['public_index_approved'])
        self.assertEqual(audit(self.db, source)['processed'], 0)
        self.ingest([candidate(recorded_bytes=999)])
        self.assertEqual(audit(self.db, source)['processed'], 1)
        self.assertIn('recorded_size_mismatch', self.db.report()[0]['issues'])

    def test_budgets_and_identity_prevent_cross_source_resume(self):
        self.ingest([candidate(), candidate(file_id=2)])
        result = audit(self.db, LocalSource(self.root), max_files=1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['pending'], 1)
        result = audit(self.db, LocalSource(self.root), max_bytes=1, max_file_bytes=100)
        self.assertEqual(result['processed'], 0)
        self.assertEqual(result['stop_reason'], 'byte_budget')
        with self.assertRaises(ValueError): audit(self.db, Mock(identity='another source'))

    def test_missing_and_conflicting_metadata_stays_unapproved(self):
        self.ingest([candidate(campus_ids='168,169', image_width=None, image_height=None)])
        audit(self.db, LocalSource(self.root))
        row = self.db.report()[0]
        self.assertIn('campus_conflict', row['issues'])
        self.assertIn('image_dimensions_unverified', row['issues'])
        self.assertFalse(row['public_index_approved'])

    def test_local_path_escape_and_oversize(self):
        source = LocalSource(self.root)
        with self.assertRaises(ValueError): source.read(candidate(local_file='../outside'), 100)
        with self.assertRaises(ValueError): source.read(candidate(), 1)

    def test_s3_single_bounded_get_and_body_closed(self):
        body = io.BytesIO(HOCR)
        client = Mock()
        client.get_object.return_value = dict(Body=body, ContentLength=len(HOCR),
            ContentRange=f'bytes 0-{len(HOCR)-1}/{len(HOCR)}', VersionId='v1')
        source = S3Source(client)
        data, metadata = source.read(candidate(), 1000)
        self.assertEqual(data, HOCR)
        self.assertEqual(metadata['version_id'], 'v1')
        client.get_object.assert_called_once_with(Bucket='compass-prod-i2-files',
            Key='s3fs-private/2024-10/page.shtml', Range='bytes=0-999')
        self.assertTrue(body.closed)
        self.assertFalse(client.list_objects_v2.called)

    def test_oversized_s3_responses_still_consume_transfer_budget(self):
        self.ingest([candidate(), candidate(file_id=2), candidate(file_id=3)])
        client = Mock()
        client.get_object.side_effect = lambda **kwargs: dict(Body=io.BytesIO(b'xxxxxxxxxx'), ContentLength=10, ContentRange='bytes 0-9/10000')
        result = audit(self.db, S3Source(client), max_bytes=20, max_file_bytes=10)
        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['stop_reason'], 'byte_budget')
        self.assertEqual(result['budgeted_bytes'], 20)

    def test_retry_only_errors(self):
        self.ingest([candidate(local_file='missing')])
        audit(self.db, LocalSource(self.root))
        self.assertEqual(self.db.report()[0]['status'], 'read_error')
        self.db.retry_errors()
        self.assertEqual(len(list(self.db.pending())), 1)

    def test_s3_oversize_is_not_misclassified_as_invalid_hocr(self):
        body = io.BytesIO(HOCR[:10])
        client = Mock()
        client.get_object.return_value = dict(Body=body, ContentLength=10, ContentRange='bytes 0-9/10000')
        with self.assertRaisesRegex(ValueError, 'size_limit'): S3Source(client).read(candidate(), 10)
        self.assertTrue(body.closed)


class ExportTests(unittest.TestCase):
    def test_export_query_names_the_versioned_inventory_fields(self):
        from discovery.export import sample_sql

        query = sample_sql(collection_id=9001, hocr_media_use_id=777)
        for field in ('schema_version', 'exported_at', 'collection_id', 'page_order', 'canvas_id', 's3_key', 'recorded_bytes',
                      'media_use', 'campus_ids', 'collection_ancestry', 'aspace_record',
                      'ancestry_depth_limit_reached', 'publication_state', 'access_terms'):
            with self.subTest(field=field):
                self.assertIn(field, query)

    def test_collection_query_keeps_campus_anomalies_and_paginates(self):
        import sqlite3
        from discovery.export import sample_sql
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.create_function('UTC_TIMESTAMP', 0, lambda: '2026-09-22 00:00:00')
        db.create_function('CONCAT_WS', -1, lambda separator, *values: separator.join(str(v) for v in values if v is not None))
        db.create_function('CONCAT', -1, lambda *values: ''.join(str(value) for value in values))
        db.create_function('LOCATE', 2, lambda needle, haystack: haystack.find(needle) + 1)
        definitions = {
            'file_managed': 'fid INTEGER, filename TEXT, uri TEXT, filemime TEXT, filesize INTEGER',
            'node__field_member_of': 'entity_id INTEGER, deleted INTEGER, field_member_of_target_id INTEGER',
            'node__field_campus': 'entity_id INTEGER, deleted INTEGER, field_campus_target_id INTEGER',
            'node__field_weight': 'entity_id INTEGER, deleted INTEGER, field_weight_value INTEGER',
            'node_field_data': 'nid INTEGER, status INTEGER',
            'media__field_media_of': 'entity_id INTEGER, deleted INTEGER, field_media_of_target_id INTEGER',
            'media__field_media_use': 'entity_id INTEGER, deleted INTEGER, field_media_use_target_id INTEGER',
            'media__field_media_file': 'entity_id INTEGER, deleted INTEGER, field_media_file_target_id INTEGER',
            'media__field_access_terms': 'entity_id INTEGER, deleted INTEGER, field_access_terms_target_id INTEGER',
            'node__field_access_terms': 'entity_id INTEGER, deleted INTEGER, field_access_terms_target_id INTEGER',
            'taxonomy_term_field_data': 'tid INTEGER, name TEXT',
        }
        for name, columns in definitions.items(): db.execute(f'CREATE TABLE {name} ({columns})')
        for fid, campus in [(1,169),(2,168),(3,169)]:
            db.execute('INSERT INTO file_managed VALUES (?, ?, ?, ?, ?)', (fid, f'{fid}.html', f'private://{fid}.html', 'text/html', 10))
            if fid == 3:
                db.executemany('INSERT INTO node__field_member_of VALUES (?, 0, ?)',
                               [(100+fid,9001),(100+fid,980),(980,9001),(200+fid,100+fid)])
            else:
                db.executemany('INSERT INTO node__field_member_of VALUES (?, 0, ?)',
                               [(100+fid,9001),(200+fid,100+fid)])
            db.execute('INSERT INTO node__field_campus VALUES (?,0,?)', (100+fid,campus))
            db.execute('INSERT INTO node__field_weight VALUES (?,0,?)', (200+fid,fid))
            db.executemany('INSERT INTO node_field_data VALUES (?,1)', [(100+fid,), (200+fid,)])
            db.execute('INSERT INTO media__field_media_of VALUES (?,0,?)', (300+fid,200+fid))
            db.execute('INSERT INTO media__field_media_use VALUES (?,0,777)', (300+fid,))
            db.execute('INSERT INTO media__field_media_file VALUES (?,0,?)', (300+fid,fid))
        sql = sample_sql(collection_id=9001, hocr_media_use_id=777, limit=1)
        first = json.loads(db.execute(sql).fetchone()[0])
        self.assertEqual(first['file_id'], 1)
        self.assertEqual(first['schema_version'], 1)
        self.assertEqual(first['page_order'], 1)
        self.assertEqual(first['s3_key'], 's3fs-private/1.html')
        row = json.loads(db.execute(sample_sql(collection_id=9001, hocr_media_use_id=777,
                                               after_file_id=1)).fetchone()[0])
        self.assertEqual(row['file_id'], 2)
        self.assertEqual(set(row['campus_ids'].split(',')) - {''}, {'168'})
        deep = json.loads(db.execute(sample_sql(collection_id=9001, hocr_media_use_id=777,
                                                after_file_id=2)).fetchone()[0])
        self.assertIn('9001>980>103>203', deep['collection_ancestry'])
        with self.assertRaises(ValueError):
            sample_sql(collection_id=9001, hocr_media_use_id=777, limit=101)

    def test_cursor_resumes_after_every_row_when_a_file_has_multiple_page_associations(self):
        import sqlite3
        from discovery.export import sample_sql
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.create_function('UTC_TIMESTAMP', 0, lambda: '2026-09-22 00:00:00')
        db.create_function('CONCAT_WS', -1, lambda separator, *values: separator.join(str(v) for v in values if v is not None))
        db.create_function('CONCAT', -1, lambda *values: ''.join(str(value) for value in values))
        db.create_function('LOCATE', 2, lambda needle, haystack: haystack.find(needle) + 1)
        definitions = {
            'file_managed': 'fid INTEGER, filename TEXT, uri TEXT, filemime TEXT, filesize INTEGER',
            'node__field_member_of': 'entity_id INTEGER, deleted INTEGER, field_member_of_target_id INTEGER',
            'node__field_weight': 'entity_id INTEGER, deleted INTEGER, field_weight_value INTEGER',
            'node_field_data': 'nid INTEGER, status INTEGER',
            'media__field_media_of': 'entity_id INTEGER, deleted INTEGER, field_media_of_target_id INTEGER',
            'media__field_media_use': 'entity_id INTEGER, deleted INTEGER, field_media_use_target_id INTEGER',
            'media__field_media_file': 'entity_id INTEGER, deleted INTEGER, field_media_file_target_id INTEGER',
            'media__field_access_terms': 'entity_id INTEGER, deleted INTEGER, field_access_terms_target_id INTEGER',
            'node__field_access_terms': 'entity_id INTEGER, deleted INTEGER, field_access_terms_target_id INTEGER',
            'taxonomy_term_field_data': 'tid INTEGER, name TEXT',
            'node__field_campus': 'entity_id INTEGER, deleted INTEGER, field_campus_target_id INTEGER',
        }
        for name, columns in definitions.items():
            db.execute(f'CREATE TABLE {name} ({columns})')
        db.execute("INSERT INTO file_managed VALUES (1,'shared.hocr','private://shared.hocr','text/html',20)")
        db.executemany('INSERT INTO node__field_member_of VALUES (?,0,?)',
                       [(100,9001),(101,9001),(200,100),(200,101),(201,100)])
        db.executemany('INSERT INTO node__field_weight VALUES (?,0,?)', [(200,1),(201,2)])
        db.executemany('INSERT INTO node_field_data VALUES (?,1)', [(100,),(101,),(200,),(201,)])
        for media_id, page_id in [(300,200),(301,201)]:
            db.execute('INSERT INTO media__field_media_of VALUES (?,0,?)', (media_id,page_id))
            db.execute('INSERT INTO media__field_media_use VALUES (?,0,777)', (media_id,))
            db.execute('INSERT INTO media__field_media_file VALUES (?,0,1)', (media_id,))

        first = json.loads(db.execute(sample_sql(collection_id=9001, hocr_media_use_id=777,
                                                 limit=1)).fetchone()[0])
        second = json.loads(db.execute(sample_sql(collection_id=9001, hocr_media_use_id=777,
                                                  after_file_id=first['file_id'],
                                                  after_page_id=first['page_id'],
                                                  after_item_id=first['item_id'],
                                                  limit=1)).fetchone()[0])
        third = json.loads(db.execute(sample_sql(collection_id=9001, hocr_media_use_id=777,
                                                 after_file_id=second['file_id'],
                                                 after_page_id=second['page_id'],
                                                 after_item_id=second['item_id'],
                                                 limit=1)).fetchone()[0])
        self.assertEqual((first['file_id'], first['page_id']), (1, 200))
        self.assertEqual((first['file_id'], first['page_id'], first['item_id']), (1, 200, 100))
        self.assertEqual((second['file_id'], second['page_id'], second['item_id']), (1, 200, 101))
        self.assertEqual((third['file_id'], third['page_id'], third['item_id']), (1, 201, 100))
