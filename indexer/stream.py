"""Stream versioned hOCR objects from S3 into a bounded Solr update journal."""

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


INVENTORY_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 2
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1000
DEFAULT_MAX_PAGE_BYTES = 20_000_000
MAX_PAGE_BYTES = 100_000_000
DEFAULT_SOLR_PAGE_SIZE = 500
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_BBOX = re.compile(r'\bbbox (\d+) (\d+) (\d+) (\d+)')


class StreamIndexError(Exception):
    """An inventory row could not be streamed, validated or indexed."""


def page_document_id(corpus_id, item_id, canvas_id):
    """Return the stable page ID shared with the pilot indexer."""
    if not re.fullmatch(r'[a-z0-9-]{1,64}', str(corpus_id)):
        raise ValueError('invalid corpus identifier')
    item_id = str(item_id or '').strip()
    canvas_id = str(canvas_id or '').strip()
    if not item_id or not canvas_id:
        raise ValueError('item ID and canvas ID are required for page identity')
    canvas_hash = hashlib.sha256(canvas_id.encode('utf-8')).hexdigest()[:24]
    return f'{corpus_id}:{item_id}:{canvas_hash}'


def _s3_uri(value):
    parsed = urlsplit(str(value))
    if parsed.scheme != 's3' or not parsed.netloc or not parsed.path.lstrip('/'):
        raise ValueError('inventory must be a local path or s3://bucket/key')
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('S3 inventory URI cannot contain credentials, query or fragment')
    return parsed.netloc, parsed.path.lstrip('/')


def _inventory_lines(location, s3_client, inventory_version_id=None):
    value = str(location)
    if value.startswith('s3://'):
        if s3_client is None:
            raise ValueError('an S3 client is required for an S3 inventory')
        bucket, key = _s3_uri(value)
        request = {'Bucket': bucket, 'Key': key}
        if inventory_version_id:
            request['VersionId'] = inventory_version_id
        response = s3_client.get_object(**request)
        body = response['Body']
        try:
            yield from body.iter_lines()
        finally:
            body.close()
        return

    if inventory_version_id:
        raise ValueError('--inventory-version-id applies only to an s3:// inventory')
    with Path(location).open('rb') as stream:
        yield from stream


def _parse_row(raw_line, line_number, corpus_id):
    if not raw_line.strip():
        return None
    try:
        row = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'inventory line {line_number} is not valid JSON') from error
    if not isinstance(row, dict) or row.get('schema_version') != INVENTORY_SCHEMA_VERSION:
        raise ValueError(f'inventory line {line_number} has an unsupported schema version')
    item_id = str(row.get('item_id') or '').strip()
    canvas_id = str(row.get('canvas_id') or '').strip()
    page_number = row.get('page_number')
    if not item_id or not canvas_id:
        raise ValueError(f'inventory line {line_number} needs item_id and canvas_id')
    if type(page_number) is not int or page_number < 1:
        raise ValueError(f'inventory line {line_number} needs a positive integer page_number')
    row['item_id'] = item_id
    row['canvas_id'] = canvas_id
    row['_stream_page_id'] = page_document_id(corpus_id, item_id, canvas_id)
    row['_stream_access'] = str(row.get('access') or '').strip().casefold()
    row['_stream_withdrawn'] = _is_withdrawn(row)
    return row


def _is_withdrawn(row):
    state = str(row.get('publication_state') or '').strip().casefold()
    flag = row.get('withdrawn')
    return state in {'withdrawn', 'deleted'} or flag is True or str(flag).strip().casefold() in {'1', 'true', 'yes'}


def _canonical_row_hash(digest, row):
    data = json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    digest.update(data)
    digest.update(b'\n')


def _inventory_identity(location, s3_client, corpus_id, db, inventory_version_id=None):
    db.execute('DROP TABLE IF EXISTS temp.ocr010_input')
    db.execute('''CREATE TEMP TABLE ocr010_input (
        row_number INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE,
        access TEXT NOT NULL,
        withdrawn INTEGER NOT NULL
    )''')
    digest = hashlib.sha256()
    row_number = 0
    for line_number, raw_line in enumerate(
            _inventory_lines(location, s3_client, inventory_version_id), 1):
        if isinstance(raw_line, str):
            raw_line = raw_line.encode('utf-8')
        row = _parse_row(raw_line, line_number, corpus_id)
        if row is None:
            continue
        row_number += 1
        _canonical_row_hash(digest, {key: value for key, value in row.items()
                                    if not key.startswith('_stream_')})
        try:
            db.execute(
                'INSERT INTO temp.ocr010_input(row_number,page_id,access,withdrawn) VALUES(?,?,?,?)',
                (row_number, row['_stream_page_id'], row['_stream_access'], int(row['_stream_withdrawn'])))
        except sqlite3.IntegrityError as error:
            raise ValueError(f'inventory contains a duplicate page identity at line {line_number}') from error
    if row_number == 0:
        raise ValueError('inventory must contain at least one page; refusing an empty corpus update')
    return digest.hexdigest(), row_number


class RunJournal:
    """Private SQLite checkpoint containing metadata and per-page status only."""

    def __init__(self, path, corpus_id, inventory_sha256, page_count, dry_run, db=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if db is None:
            previous_umask = os.umask(0o077)
            try:
                db = sqlite3.connect(str(self.path))
            finally:
                os.umask(previous_umask)
        self.db = db
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('''CREATE TABLE IF NOT EXISTS ocr010_meta (
            name TEXT PRIMARY KEY, value TEXT NOT NULL
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS ocr010_pages (
            row_number INTEGER PRIMARY KEY,
            page_id TEXT NOT NULL UNIQUE,
            access TEXT NOT NULL,
            withdrawn INTEGER NOT NULL,
            status TEXT,
            error TEXT
        )''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS ocr010_withdrawn (
            page_id TEXT PRIMARY KEY
        )''')
        self.db.commit()

        existing = self.meta()
        if not existing:
            self.db.execute('''INSERT INTO ocr010_pages(row_number,page_id,access,withdrawn)
                SELECT row_number,page_id,access,withdrawn FROM temp.ocr010_input''')
            values = {
                'schema_version': '1',
                'corpus_id': corpus_id,
                'inventory_sha256': inventory_sha256,
                'page_count': str(page_count),
                'dry_run': '1' if dry_run else '0',
                'source_complete': '0',
                'reconcile_cursor': '*',
                'reconcile_complete': '0',
            }
            self.db.executemany('INSERT INTO ocr010_meta(name,value) VALUES(?,?)', values.items())
            self.db.commit()
        else:
            expected = {
                'schema_version': '1',
                'corpus_id': corpus_id,
                'inventory_sha256': inventory_sha256,
                'page_count': str(page_count),
                'dry_run': '1' if dry_run else '0',
            }
            for key, value in expected.items():
                if existing.get(key) != value:
                    self.close()
                    raise ValueError(f'journal {key} differs; use a new journal for a changed run')
            mismatch = self.db.execute('''SELECT COUNT(*) FROM temp.ocr010_input i
                LEFT JOIN ocr010_pages p USING(row_number)
                WHERE p.page_id IS NULL OR p.page_id != i.page_id
                   OR p.access != i.access OR p.withdrawn != i.withdrawn''').fetchone()[0]
            if mismatch:
                self.close()
                raise ValueError('journal page rows do not match the inventory; use a new journal')

    def meta(self):
        return {row['name']: row['value'] for row in
                self.db.execute('SELECT name,value FROM ocr010_meta')}

    def set_meta(self, **values):
        self.db.executemany('INSERT INTO ocr010_meta(name,value) VALUES(?,?) '
                            'ON CONFLICT(name) DO UPDATE SET value=excluded.value',
                            ((key, str(value)) for key, value in values.items()))
        self.db.commit()

    def page_status(self, row_number):
        row = self.db.execute('SELECT status FROM ocr010_pages WHERE row_number=?',
                              (row_number,)).fetchone()
        return row['status'] if row else None

    def save_statuses(self, updates):
        with self.db:
            self.db.executemany('UPDATE ocr010_pages SET status=?, error=? WHERE row_number=?',
                                ((status, error[:500] if error else None, row_number)
                                 for row_number, status, error in updates))

    def indexed(self, page_id):
        row = self.db.execute('SELECT status FROM ocr010_pages WHERE page_id=?',
                              (page_id,)).fetchone()
        return bool(row and row['status'] == 'indexed')

    def page_for_id(self, page_id):
        return self.db.execute('SELECT status,withdrawn FROM ocr010_pages WHERE page_id=?',
                               (page_id,)).fetchone()

    def record_withdrawn(self, page_ids):
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO ocr010_withdrawn(page_id) VALUES(?)',
                                ((page_id,) for page_id in page_ids))

    def counts(self):
        counts = {row['status']: row['n'] for row in self.db.execute(
            'SELECT status,COUNT(*) AS n FROM ocr010_pages GROUP BY status') if row['status']}
        counts['withdrawn'] = counts.get('withdrawn', 0) + self.db.execute(
            'SELECT COUNT(*) FROM ocr010_withdrawn').fetchone()[0]
        counts['indexed'] = counts.get('indexed', 0)
        counts['skipped'] = counts.get('skipped', 0)
        counts['failed'] = counts.get('failed', 0)
        counts['validated'] = counts.get('validated', 0)
        return counts

    def close(self):
        self.db.close()


class SolrClient:
    """Small JSON API client for one explicit Solr core URL."""

    def __init__(self, base_url, timeout=30):
        parsed = urlsplit(str(base_url))
        core_path = parsed.path.strip('/').split('/')
        if (parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Solr URL must be an HTTP(S) core URL without credentials or query')
        if len(core_path) != 2 or core_path[0] != 'solr' or not re.fullmatch(
                r'[A-Za-z0-9_.-]+', core_path[1]):
            raise ValueError('Solr URL must identify one /solr/<core> endpoint')
        self.base_url = str(base_url).rstrip('/')
        self.timeout = timeout

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(self.base_url + path, data=data,
                          headers={'Content-Type': 'application/json'} if data is not None else {},
                          method='POST' if data is not None else 'GET')
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            error.close()
            raise
        header = result.get('responseHeader', {}) if isinstance(result, dict) else {}
        if header and header.get('status', 0) != 0:
            raise RuntimeError(f'Solr returned status {header.get("status")}')
        return result

    def add_documents(self, documents):
        if documents:
            self._request('/update?commit=true', documents)

    def delete_ids(self, page_ids):
        if page_ids:
            self._request('/update?commit=true', {'delete': [{'id': page_id} for page_id in page_ids]})

    def list_ids(self, corpus_id, cursor_mark='*', rows=DEFAULT_SOLR_PAGE_SIZE):
        params = urlencode({
            'q': f'corpus_id:"{corpus_id}"',
            'fl': 'id',
            'rows': rows,
            'sort': 'id asc',
            'cursorMark': cursor_mark,
            'wt': 'json',
        })
        result = self._request('/select?' + params)
        response = result.get('response', {})
        ids = [doc['id'] for doc in response.get('docs', []) if doc.get('id')]
        next_mark = result.get('nextCursorMark', cursor_mark)
        return ids, next_mark


def _retryable(error):
    code = ''
    status = None
    response = getattr(error, 'response', None)
    if isinstance(response, dict):
        details = response.get('Error', {})
        metadata = response.get('ResponseMetadata', {})
        code = str(details.get('Code', ''))
        status = metadata.get('HTTPStatusCode')
    if isinstance(error, HTTPError):
        return error.code in {408, 429, 500, 502, 503, 504}
    if isinstance(error, URLError):
        return True
    if status in {408, 429, 500, 502, 503, 504}:
        return True
    if code in {'SlowDown', 'RequestTimeout', 'RequestTimeoutException',
                'InternalError', 'ServiceUnavailable', 'Throttling'}:
        return True
    return type(error).__name__ in {
        'EndpointConnectionError', 'ConnectionClosedError', 'ConnectTimeoutError',
        'ReadTimeoutError', 'TimeoutError', 'ConnectionError', 'OSError',
    }


def _retry(operation, retries, backoff_seconds, sleep_fn):
    for attempt in range(retries + 1):
        try:
            return operation()
        except Exception as error:
            if attempt >= retries or not _retryable(error):
                raise
            sleep_fn(backoff_seconds * (2 ** attempt))


def _read_hocr(s3_client, row, retries, backoff_seconds, sleep_fn, max_page_bytes):
    bucket = str(row.get('s3_bucket') or '').strip()
    key = str(row.get('s3_key') or '').strip()
    version_id = str(row.get('s3_version_id') or '').strip()
    expected_sha256 = str(row.get('sha256') or '').strip().lower()
    expected_etag = str(row.get('s3_etag') or '').strip()
    expected_size = row.get('s3_size')
    if not bucket or not key or not version_id:
        raise StreamIndexError('page needs s3_bucket, s3_key and exact s3_version_id')
    if key.startswith('/'):
        raise StreamIndexError('S3 key must be relative')
    if expected_sha256 and not _SHA256.fullmatch(expected_sha256):
        raise StreamIndexError('inventory has an invalid SHA-256 checksum')

    def get_and_stream():
        response = s3_client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        body = response['Body']
        try:
            returned_version = str(response.get('VersionId') or '')
            if returned_version != version_id:
                raise StreamIndexError('S3 returned a different object version')
            returned_etag = str(response.get('ETag') or '')
            if expected_etag and returned_etag != expected_etag:
                raise StreamIndexError('S3 ETag does not match the inventory')
            content_length = response.get('ContentLength')
            if expected_size is not None and content_length is not None and int(expected_size) != int(content_length):
                raise StreamIndexError('S3 object size does not match the inventory')
            chunks = bytearray()
            digest = hashlib.sha256()
            while True:
                chunk = body.read(min(64 * 1024, max_page_bytes + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                digest.update(chunk)
                if len(chunks) > max_page_bytes:
                    raise StreamIndexError(f'hOCR page exceeds {max_page_bytes} bytes')
            if content_length is not None and len(chunks) != int(content_length):
                raise StreamIndexError('S3 response length differs from ContentLength')
            actual_sha256 = digest.hexdigest()
            if expected_sha256 and expected_sha256 != actual_sha256:
                raise StreamIndexError('hOCR SHA-256 does not match the inventory')
            return bytes(chunks), actual_sha256
        finally:
            body.close()

    return _retry(get_and_stream, retries, backoff_seconds, sleep_fn)


def validate_streamed_hocr(data):
    """Validate page and word structure without saving the source bytes."""
    import xml.etree.ElementTree as ET

    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise StreamIndexError('hOCR document type and entity declarations are not supported')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise StreamIndexError('hOCR is not well-formed XML') from error
    pages = [element for element in root.iter()
             if 'ocr_page' in element.get('class', '').split()]
    words = [element for element in root.iter()
             if set(element.get('class', '').split()) & {'ocrx_word', 'ocr_word'}]
    if len(pages) != 1 or not words or not any(''.join(word.itertext()).strip() for word in words):
        raise StreamIndexError('expected one nonempty hOCR page')
    page_box = _BBOX.search(pages[0].get('title', ''))
    if not page_box:
        raise StreamIndexError('hOCR page has no bbox')
    page_left, page_top, page_right, page_bottom = map(int, page_box.groups())
    if page_right <= page_left or page_bottom <= page_top:
        raise StreamIndexError('hOCR page has invalid dimensions')
    for word in words:
        match = _BBOX.search(word.get('title', ''))
        if not match:
            raise StreamIndexError('hOCR word has no bbox')
        left, top, right, bottom = map(int, match.groups())
        if not (page_left <= left <= right <= page_right
                and page_top <= top <= bottom <= page_bottom):
            raise StreamIndexError('hOCR word bbox is outside its page')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise StreamIndexError('hOCR must be UTF-8') from error


def _collection_id(row):
    value = row.get('collection_ancestry') or []
    paths = []
    if isinstance(value, str):
        for raw_path in re.split(r'[|,]', value):
            path = [part.strip() for part in raw_path.split('>') if part.strip()]
            if path:
                paths.append(path)
    elif isinstance(value, (list, tuple)):
        for candidate in value:
            if isinstance(candidate, (list, tuple)):
                path = [str(part).strip() for part in candidate if str(part).strip()]
            else:
                path = [str(candidate).strip()] if str(candidate).strip() else []
            if path:
                paths.append(path)
    roots = {path[0] for path in paths if path}
    if len(roots) != 1:
        raise StreamIndexError('collection ancestry must identify exactly one top collection')
    return next(iter(roots))


def _year(issue_date):
    value = str(issue_date or '').strip()
    if not value:
        return ''
    if re.fullmatch(r'\d{4}', value):
        year = int(value)
        return year if year >= 1 else ''
    try:
        if re.fullmatch(r'\d{4}-\d{2}', value):
            year, month = map(int, value.split('-'))
            if year >= 1 and 1 <= month <= 12:
                return year
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value).year
    except ValueError:
        return ''
    return ''


def _optional_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _build_document(row, corpus_id, source_sha256, hocr):
    page_number = row['page_number']
    issue_date = _optional_text(row.get('issue_date'))
    collection = _collection_id(row)
    document = {
        'id': row['_stream_page_id'],
        'corpus_id': corpus_id,
        'access': 'public',
        'item_id': str(row['item_id']),
        'page_number': page_number,
        'page_label': str(row.get('page_label') or ''),
        'canvas_id': str(row['canvas_id']),
        'source_manifest_url': str(row.get('manifest_url') or row.get('source_manifest_url') or ''),
        'collection_id': collection,
        'ocr': hocr,
        'schema_version': INDEX_SCHEMA_VERSION,
    }
    optional_fields = {
        'aspace_record': _optional_text(row.get('aspace_record')),
        'title': _optional_text(row.get('item_title')),
        'series': _optional_text(row.get('series')),
        'issue_date': issue_date,
        'year': _year(issue_date),
        'source_bucket': _optional_text(row.get('s3_bucket')),
        'source_key': _optional_text(row.get('s3_key')),
        'source_version_id': _optional_text(row.get('s3_version_id')),
        'source_etag': _optional_text(row.get('s3_etag')),
        'source_sha256': _optional_text(source_sha256),
    }
    document.update({key: value for key, value in optional_fields.items()
                     if value is not None and value != ''})
    return document


def _error_text(error):
    return (str(error).strip() or type(error).__name__)[:500]


def _process_rows(rows, journal, s3_client, solr_client, corpus_id, batch_size,
                  retries, backoff_seconds, sleep_fn, max_page_bytes, dry_run):
    statuses = []
    documents = []
    deletions = []
    for row_number, row in rows:
        page_id = row['_stream_page_id']
        if row['_stream_withdrawn']:
            deletions.append((row_number, page_id, 'withdrawn'))
            continue
        if row['_stream_access'] != 'public':
            deletions.append((row_number, page_id, 'skipped'))
            continue
        try:
            data, sha256 = _read_hocr(s3_client, row, retries, backoff_seconds,
                                      sleep_fn, max_page_bytes)
            hocr = validate_streamed_hocr(data)
            document = _build_document(row, corpus_id, sha256, hocr)
            if dry_run:
                statuses.append((row_number, 'validated', None))
            else:
                documents.append((row_number, document))
        except Exception as error:
            statuses.append((row_number, 'failed', _error_text(error)))

    if dry_run:
        statuses.extend((row_number, status, None) for row_number, _, status in deletions)
        journal.save_statuses(statuses)
        return

    for start in range(0, len(deletions), batch_size):
        deletion_batch = deletions[start:start + batch_size]
        if deletion_batch:
            page_ids = [entry[1] for entry in deletion_batch]
            try:
                _retry(lambda: solr_client.delete_ids(page_ids), retries, backoff_seconds, sleep_fn)
                statuses.extend((row_number, status, None)
                                for row_number, _, status in deletion_batch)
            except Exception as error:
                failure = _error_text(error)
                statuses.extend((row_number, 'failed', failure)
                                for row_number, _, _ in deletion_batch)

    for start in range(0, len(documents), batch_size):
        document_batch = documents[start:start + batch_size]
        if document_batch:
            try:
                _retry(lambda: solr_client.add_documents([entry[1] for entry in document_batch]),
                       retries, backoff_seconds, sleep_fn)
                statuses.extend((row_number, 'indexed', None)
                                for row_number, _ in document_batch)
            except Exception as error:
                failure = _error_text(error)
                statuses.extend((row_number, 'failed', failure)
                                for row_number, _ in document_batch)

    journal.save_statuses(statuses)


def _reconcile_solr(journal, solr_client, corpus_id, batch_size, retries,
                    backoff_seconds, sleep_fn):
    meta = journal.meta()
    if meta.get('reconcile_complete') == '1':
        return True
    cursor = meta.get('reconcile_cursor', '*')
    while True:
        try:
            ids, next_cursor = _retry(lambda: solr_client.list_ids(corpus_id, cursor),
                                      retries, backoff_seconds, sleep_fn)
            stale = [page_id for page_id in ids if not journal.indexed(page_id)]
            for start in range(0, len(stale), batch_size):
                page_ids = stale[start:start + batch_size]
                _retry(lambda: solr_client.delete_ids(page_ids), retries,
                       backoff_seconds, sleep_fn)
                withdrawn = []
                for page_id in page_ids:
                    if journal.page_for_id(page_id) is None:
                        withdrawn.append(page_id)
                if withdrawn:
                    journal.record_withdrawn(withdrawn)
        except Exception as error:
            journal.set_meta(reconcile_error=_error_text(error))
            return False
        journal.set_meta(reconcile_cursor=next_cursor, reconcile_error='')
        if next_cursor == cursor:
            journal.set_meta(reconcile_complete='1')
            return True
        cursor = next_cursor


def index_inventory(inventory, s3_client, solr_client, journal_path, corpus_id,
                    batch_size=DEFAULT_BATCH_SIZE, retries=3, backoff_seconds=0.5,
                    max_page_bytes=DEFAULT_MAX_PAGE_BYTES, dry_run=False,
                    retry_failed=False, inventory_version_id=None, sleep_fn=time.sleep):
    """Validate and stream one inventory to Solr without creating local OCR files.

    The journal contains page IDs and statuses only. Reusing it requires the same
    inventory contents, corpus, and dry-run mode; use a fresh journal for a new
    snapshot. Failed pages may be retried with ``retry_failed=True``.
    """
    if not re.fullmatch(r'[a-z0-9-]{1,64}', str(corpus_id)):
        raise ValueError('invalid corpus identifier')
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f'batch size must be 1–{MAX_BATCH_SIZE}')
    if type(retries) is not int or not 0 <= retries <= 10:
        raise ValueError('retries must be 0–10')
    if backoff_seconds < 0 or backoff_seconds > 30:
        raise ValueError('backoff seconds must be 0–30')
    if type(max_page_bytes) is not int or not 1 <= max_page_bytes <= MAX_PAGE_BYTES:
        raise ValueError(f'max page bytes must be 1–{MAX_PAGE_BYTES}')
    if dry_run and solr_client is not None:
        # Accepting a client is harmless, but never call it on the dry-run path.
        pass
    elif not dry_run and solr_client is None:
        raise ValueError('a Solr client is required unless --dry-run is set')
    if s3_client is None:
        raise ValueError('an S3 client is required')

    journal = None
    try:
        temp_db = None
        journal_file = Path(journal_path)
        journal_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        previous_umask = os.umask(0o077)
        try:
            temp_db = sqlite3.connect(str(journal_file))
        finally:
            os.umask(previous_umask)
        os.chmod(journal_file, 0o600)
        temp_db.row_factory = sqlite3.Row
        temp_db.execute('''CREATE TABLE IF NOT EXISTS ocr010_meta (
            name TEXT PRIMARY KEY, value TEXT NOT NULL
        )''')
        temp_db.execute('''CREATE TABLE IF NOT EXISTS ocr010_pages (
            row_number INTEGER PRIMARY KEY, page_id TEXT NOT NULL UNIQUE,
            access TEXT NOT NULL, withdrawn INTEGER NOT NULL, status TEXT, error TEXT
        )''')
        temp_db.execute('''CREATE TABLE IF NOT EXISTS ocr010_withdrawn (
            page_id TEXT PRIMARY KEY
        )''')
        temp_db.commit()
        inventory_sha256, page_count = _inventory_identity(
            inventory, s3_client, corpus_id, temp_db, inventory_version_id)
        journal = RunJournal(journal_path, corpus_id, inventory_sha256, page_count, dry_run, temp_db)
        temp_db = None
        journal.db.execute('DROP TABLE IF EXISTS temp.ocr010_input')

        pending = []
        row_number = 0
        for line_number, raw_line in enumerate(
                _inventory_lines(inventory, s3_client, inventory_version_id), 1):
            if isinstance(raw_line, str):
                raw_line = raw_line.encode('utf-8')
            row = _parse_row(raw_line, line_number, corpus_id)
            if row is None:
                continue
            row_number += 1
            status = journal.page_status(row_number)
            if status is not None and not (retry_failed and status == 'failed'):
                continue
            pending.append((row_number, row))
            if len(pending) >= batch_size:
                _process_rows(pending, journal, s3_client, solr_client, corpus_id,
                              batch_size, retries, backoff_seconds, sleep_fn,
                              max_page_bytes, dry_run)
                pending = []
        if pending:
            _process_rows(pending, journal, s3_client, solr_client, corpus_id,
                          batch_size, retries, backoff_seconds, sleep_fn,
                          max_page_bytes, dry_run)

        unresolved = journal.db.execute(
            'SELECT COUNT(*) FROM ocr010_pages WHERE status IS NULL').fetchone()[0]
        journal.set_meta(source_complete='1' if unresolved == 0 else '0')
        reconcile_complete = True
        if not dry_run:
            reconcile_complete = _reconcile_solr(journal, solr_client, corpus_id,
                                                  batch_size, retries, backoff_seconds,
                                                  sleep_fn)
        counts = journal.counts()
        counts.update({
            'corpus_id': corpus_id,
            'inventory_sha256': inventory_sha256,
            'total_pages': page_count,
            'dry_run': bool(dry_run),
            'complete': unresolved == 0 and reconcile_complete,
            'journal': str(Path(journal_path)),
        })
        return counts
    finally:
        if journal:
            journal.close()
        elif 'temp_db' in locals() and temp_db is not None:
            temp_db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Stream versioned public hOCR pages from S3 to Solr.')
    parser.add_argument('--inventory', required=True,
                        help='local JSONL path or s3://bucket/key page inventory')
    parser.add_argument('--inventory-version-id', help='pin an S3-hosted inventory version')
    parser.add_argument('--journal', required=True, type=Path,
                        help='private SQLite run journal; contains page IDs/statuses, never OCR')
    parser.add_argument('--corpus', required=True, help='explicit corpus identifier')
    parser.add_argument('--solr-url', help='explicit Solr core URL; required except for --dry-run')
    parser.add_argument('--profile', help='named AWS profile; credentials are never command-line arguments')
    parser.add_argument('--region', default=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION'))
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('--backoff-seconds', type=float, default=0.5)
    parser.add_argument('--max-page-bytes', type=int, default=DEFAULT_MAX_PAGE_BYTES)
    parser.add_argument('--retry-failed', action='store_true',
                        help='retry pages marked failed in an otherwise matching journal')
    parser.add_argument('--dry-run', action='store_true',
                        help='verify public inventory pages and S3 objects without Solr calls')
    args = parser.parse_args(argv)
    if not args.dry_run and not args.solr_url:
        parser.error('--solr-url is required unless --dry-run is set')
    if args.dry_run and args.solr_url:
        parser.error('--dry-run does not accept --solr-url')
    try:
        import boto3
        from botocore.config import Config

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        s3 = session.client('s3', config=Config(connect_timeout=5, read_timeout=30,
                                                retries={'total_max_attempts': 1}))
        solr = SolrClient(args.solr_url) if args.solr_url else None
        result = index_inventory(
            args.inventory, s3, solr, args.journal, args.corpus,
            batch_size=args.batch_size, retries=args.retries,
            backoff_seconds=args.backoff_seconds, max_page_bytes=args.max_page_bytes,
            dry_run=args.dry_run, retry_failed=args.retry_failed,
            inventory_version_id=args.inventory_version_id)
    except (OSError, ValueError, StreamIndexError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result['complete'] or result['failed']:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
