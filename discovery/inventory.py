"""Join a versioned relationship export to synthetic/approved evidence and IIIF manifests."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


PAGE_INVENTORY_SCHEMA_VERSION = 1
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_RESTRICTED_TERMS = {'restricted', 'staff', 'staff only', 'private', 'confidential'}


def read_export(path):
    """Read JSONL emitted by ``discovery export-sql`` and reject mixed schemas."""
    rows = []
    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f'export line {line_number} is not JSON') from error
            if not isinstance(row, dict) or row.get('schema_version') != 1:
                raise ValueError(f'export line {line_number} has an unsupported schema version')
            rows.append(row)
    return rows


def _strings(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r'[|,]', value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()]


def _paths(value):
    if not value:
        return []
    if isinstance(value, str):
        paths = []
        for raw_path in re.split(r'[|,]', value):
            path = [part.strip() for part in raw_path.split('>') if part.strip()]
            if path:
                paths.append(path)
        return paths
    if isinstance(value, (list, tuple)):
        paths = []
        for candidate in value:
            if isinstance(candidate, (list, tuple)):
                path = [str(part).strip() for part in candidate if str(part).strip()]
            else:
                path = [str(candidate).strip()] if str(candidate).strip() else []
            if path:
                paths.append(path)
        return paths
    return []


def _label(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for language in ('en', '@none'):
            labels = value.get(language)
            if isinstance(labels, list) and labels:
                return str(labels[0])
        for labels in value.values():
            if isinstance(labels, list) and labels:
                return str(labels[0])
    return ''


def _manifest_canvases(manifest):
    sequences = manifest.get('sequences')
    if isinstance(sequences, list) and sequences:
        canvases = sequences[0].get('canvases', [])
        return [
            {'id': canvas.get('@id') or canvas.get('id'), 'label': _label(canvas.get('label'))}
            for canvas in canvases
        ]
    return [
        {'id': canvas.get('id') or canvas.get('@id'), 'label': _label(canvas.get('label'))}
        for canvas in manifest.get('items', [])
        if canvas.get('type') == 'Canvas' or canvas.get('@type') == 'sc:Canvas'
    ]


def _read_manifests(directory):
    by_uri = {}
    by_sha256 = {}
    for path in sorted(Path(directory).rglob('*.json')):
        raw = path.read_bytes()
        manifest = json.loads(raw)
        uri = manifest.get('@id') or manifest.get('id')
        if uri and uri in by_uri:
            raise ValueError(f'duplicate manifest identifier: {uri}')
        canvases = _manifest_canvases(manifest)
        if not canvases or any(not canvas['id'] for canvas in canvases):
            raise ValueError(f'manifest has missing canvases or canvas identifiers: {path.name}')
        if uri:
            by_uri[uri] = canvases
        by_sha256[hashlib.sha256(raw).hexdigest()] = canvases
    return by_uri, by_sha256


def _evidence_maps(evidence):
    items_by_id = {}
    pages_by_item_order = {}
    pages_by_item_id = {}
    for item in evidence.get('items', []):
        item_id = str(item.get('id', ''))
        if not item_id:
            raise ValueError('evidence item is missing its id')
        items_by_id[item_id] = item
        if item.get('compass_node') is not None:
            items_by_id[str(item['compass_node'])] = item
    for page in evidence.get('pages', []):
        item_key = str(page.get('item', ''))
        page_number = page.get('page', page.get('page_number'))
        if not item_key or type(page_number) is not int or page_number < 1:
            raise ValueError('evidence page needs an item and a positive page number')
        if not _SHA256.fullmatch(str(page.get('sha256', ''))):
            raise ValueError('evidence page has an invalid SHA-256 checksum')
        key = (item_key, page_number)
        if key in pages_by_item_order:
            raise ValueError(f'duplicate evidence page: {item_key} page {page_number}')
        pages_by_item_order[key] = page
        if page.get('page_id') is not None:
            pages_by_item_id[(item_key, str(page['page_id']))] = page
    return items_by_id, pages_by_item_order, pages_by_item_id


def _item_evidence(raw_item_id, items_by_id):
    item = items_by_id.get(str(raw_item_id))
    if item is None:
        raise ValueError(f'no evidence item maps to exported item {raw_item_id}')
    return item


def _access_class(publication_state, access_terms, public_terms):
    state = str(publication_state or '').strip().lower()
    terms = {term.casefold() for term in _strings(access_terms)}
    if state in {'unpublished', 'draft', '0', 'false', 'withdrawn'} or terms & _RESTRICTED_TERMS:
        return 'restricted'
    if state in {'published', '1', 'true'} and terms and terms <= public_terms:
        return 'public'
    return 'unknown'


def _s3_source(row, source_bucket):
    bucket = row.get('s3_bucket') or source_bucket
    key = row.get('s3_key')
    uri = row.get('source_uri') or row.get('uri') or ''
    if isinstance(uri, str) and uri.startswith('s3://'):
        parsed = urlsplit(uri)
        bucket, key = parsed.netloc, parsed.path.lstrip('/')
    elif not key and isinstance(uri, str) and '://' in uri:
        scheme, path = uri.split('://', 1)
        if scheme in {'private', 'public'} and path:
            key = ('s3fs-private/' if scheme == 'private' else 's3fs-public/') + path
    if not bucket or not key or str(key).startswith('/'):
        raise ValueError('each export row needs an S3 bucket setting and relative key')
    return str(bucket), str(key)


def _head_object(client, bucket, key, version_id=None):
    if client is None:
        raise ValueError('a read-only S3 client is required for HeadObject checks')
    params = {'Bucket': bucket, 'Key': key}
    if version_id:
        params['VersionId'] = version_id
    return client.head_object(**params)


def _head_error_code(error):
    response = getattr(error, 'response', {})
    details = response.get('Error', {}) if isinstance(response, dict) else {}
    code = details.get('Code') if isinstance(details, dict) else None
    return str(code or type(error).__name__)[:80]


def _report(pages, sample_size):
    counts = {}
    deep_items = set()
    multiple_parent_items = set()
    unresolved_items = set()
    truncated_items = set()
    page_keys_by_collection = {}
    paths_by_item = {}
    for page in pages:
        item_id = page['item_id']
        paths = page['collection_ancestry']
        if not paths:
            unresolved_items.add(item_id)
        if page['ancestry_depth_limit_reached']:
            truncated_items.add(item_id)
        item_paths = [tuple(path[:-1] if len(path) > 1 else path) for path in paths]
        paths_by_item.setdefault(item_id, set()).update(item_paths)
        if any(len(path) > 2 for path in item_paths):
            deep_items.add(item_id)
        for path in paths:
            collection = path[0]
            page_key = (item_id, page['page_number'])
            page_keys_by_collection.setdefault(collection, set()).add(page_key)
    for item_id, paths in paths_by_item.items():
        if len(paths) > 1 or len({path[0] for path in paths if path}) > 1:
            multiple_parent_items.add(item_id)
    counts.update({collection: len(page_keys) for collection, page_keys in page_keys_by_collection.items()})
    samples = []
    for page in pages:
        if page['canvas_id'] and len(samples) < sample_size:
            samples.append({'item_id': page['item_id'], 'page_number': page['page_number'],
                            'canvas_id': page['canvas_id']})
    return {
        'schema_version': 1,
        'input_file_count': len({page['file_id'] for page in pages}),
        'page_count': len(pages),
        's3_head_errors': sum(page['s3_status'] == 'unavailable' for page in pages),
        'size_mismatches': sum('recorded_size_mismatch' in page['issues'] for page in pages),
        'pages_by_collection': dict(sorted(counts.items())),
        'deep_items': sorted(deep_items),
        'multiple_parent_items': sorted(multiple_parent_items),
        'ancestry_unresolved_items': sorted(unresolved_items),
        'ancestry_truncated_items': sorted(truncated_items),
        'sample_canvas_ids': samples,
    }


def build_inventory(export_rows, evidence, manifest_directory, s3_client, source_bucket=None,
                    public_access_terms=('public',), sample_size=10):
    """Build the page inventory and reconciliation report without writing or uploading data."""
    if not 1 <= sample_size <= 100:
        raise ValueError('canvas sample size must be 1–100')
    items_by_id, evidence_pages, evidence_pages_by_id = _evidence_maps(evidence)
    manifests, manifests_by_sha256 = _read_manifests(manifest_directory)
    public_terms = {term.casefold() for term in _strings(public_access_terms)}
    seen_file_associations, seen_pages = set(), set()
    pages = []

    for row in export_rows:
        if not isinstance(row, dict) or row.get('schema_version') != 1:
            raise ValueError('export row has an unsupported schema version')
        for field in ('file_id', 'page_id', 'item_id'):
            if row.get(field) is None or str(row[field]) == '':
                raise ValueError(f'export row is missing {field}')
        file_id = str(row['file_id'])
        association = (file_id, str(row['page_id']), str(row['item_id']))
        if association in seen_file_associations:
            raise ValueError(f'duplicate exported file/page association: {association}')
        seen_file_associations.add(association)
        item = _item_evidence(row['item_id'], items_by_id)
        stable_item_id = str(item['id'])
        page_order = row.get('page_order')
        if page_order is None:
            raise ValueError(f'page order is missing for file {file_id}; use the manifest/evidence mapping')
        try:
            page_order = int(page_order)
        except (TypeError, ValueError) as error:
            raise ValueError(f'page order is invalid for file {file_id}') from error
        if page_order < 1:
            raise ValueError(f'page order must be positive for file {file_id}')
        page_key = (stable_item_id, page_order)
        if page_key in seen_pages:
            raise ValueError(f'duplicate item/page mapping: {stable_item_id} page {page_order}')
        seen_pages.add(page_key)

        evidence_page = evidence_pages_by_id.get((str(row['item_id']), str(row['page_id'])))
        if evidence_page is None:
            evidence_page = evidence_pages.get((stable_item_id, page_order))
        if evidence_page is None:
            raise ValueError(f'no checksum evidence for {stable_item_id} page {page_order}')

        manifest_url = row.get('manifest_url') or item.get('manifest_url')
        manifest_canvases = manifests.get(manifest_url)
        if manifest_canvases is None and item.get('manifest_sha256'):
            manifest_canvases = manifests_by_sha256.get(str(item['manifest_sha256']).lower())
        if manifest_canvases is None:
            raise ValueError(f'no local converted manifest for {stable_item_id}')
        canvases = manifest_canvases
        if page_order > len(canvases):
            raise ValueError(f'manifest has no canvas for {stable_item_id} page {page_order}')
        canvas = canvases[page_order - 1]
        evidence_canvas = evidence_page.get('canvas') or evidence_page.get('canvas_id')
        supplied_canvas = row.get('canvas_id')
        if supplied_canvas and supplied_canvas != canvas['id']:
            raise ValueError(f'export canvas does not match manifest for {stable_item_id} page {page_order}')
        if evidence_canvas and evidence_canvas != canvas['id']:
            raise ValueError(f'evidence canvas does not match manifest for {stable_item_id} page {page_order}')

        expected_sha256 = str(evidence_page['sha256']).lower()
        supplied_sha256 = row.get('sha256') or row.get('expected_sha256')
        if supplied_sha256 and str(supplied_sha256).lower() != expected_sha256:
            raise ValueError(f'export checksum does not match evidence for {stable_item_id} page {page_order}')
        bucket, key = _s3_source(row, source_bucket)
        if s3_client is None:
            raise ValueError('a read-only S3 client is required for HeadObject checks')
        head_error = None
        try:
            head = _head_object(s3_client, bucket, key, row.get('version_id'))
            s3_size = int(head['ContentLength'])
        except Exception as error:
            head, s3_size = {}, None
            head_error = _head_error_code(error)
        recorded_size = row.get('recorded_bytes')
        issues = []
        if head_error:
            issues.append('s3_head_error')
        if recorded_size is not None and s3_size is not None and int(recorded_size) != s3_size:
            issues.append('recorded_size_mismatch')
        remote_checksum = head.get('ChecksumSHA256')
        if remote_checksum:
            remote_sha256 = base64.b64decode(remote_checksum).hex()
            if remote_sha256 != expected_sha256:
                issues.append('s3_checksum_mismatch')
        ancestry = _paths(row.get('collection_ancestry'))
        page = {
            'schema_version': PAGE_INVENTORY_SCHEMA_VERSION,
            'file_id': row['file_id'],
            'page_id': row['page_id'],
            'item_id': stable_item_id,
            'source_item_id': row['item_id'],
            'page_number': page_order,
            'page_label': canvas['label'] or evidence_page.get('label', ''),
            'canvas_id': canvas['id'],
            'manifest_url': manifest_url,
            'aspace_record': (evidence_page.get('aspace_record') or item.get('aspace_record')
                              or row.get('aspace_record')),
            's3_bucket': bucket,
            's3_key': key,
            's3_version_id': head.get('VersionId'),
            's3_status': 'unavailable' if head_error else 'verified',
            's3_error': head_error,
            'recorded_bytes': int(recorded_size) if recorded_size is not None else None,
            's3_size': s3_size,
            'sha256': expected_sha256,
            'media_use': row.get('media_use'),
            'campus_ids': _strings(row.get('campus_ids')),
            'collection_ancestry': ancestry,
            'ancestry_depth_limit_reached': bool(row.get('ancestry_depth_limit_reached')),
            'publication_state': row.get('publication_state'),
            'access_terms': _strings(row.get('access_terms')),
            'access': _access_class(row.get('publication_state'), row.get('access_terms'), public_terms),
            'issues': issues,
        }
        pages.append(page)

    pages.sort(key=lambda page: (page['item_id'], page['page_number'], str(page['file_id'])))
    return {'pages': pages, 'reconciliation': _report(pages, sample_size)}


def _local_sidecar(path):
    return path.with_name(path.stem + '.reconciliation.json')


def _jsonl(pages):
    return ''.join(json.dumps(page, sort_keys=True) + '\n' for page in pages).encode()


def write_inventory(result, output_location, s3_client=None):
    """Write JSONL and a reconciliation sidecar to a local path or configured s3:// URI."""
    output = str(output_location)
    if output.startswith('s3://'):
        parsed = urlsplit(output)
        bucket, key = parsed.netloc, parsed.path.lstrip('/')
        if not bucket or not key or parsed.query or parsed.fragment:
            raise ValueError('S3 output must be s3://bucket/key without query or fragment')
        if s3_client is None:
            raise ValueError('an S3 client is required for an s3:// output location')
        report_key = key.rsplit('.', 1)[0] + '.reconciliation.json' if '.' in key.rsplit('/', 1)[-1] else key + '.reconciliation.json'
        s3_client.put_object(Bucket=bucket, Key=key, Body=_jsonl(result['pages']),
                             ContentType='application/x-ndjson')
        s3_client.put_object(Bucket=bucket, Key=report_key,
                             Body=(json.dumps(result['reconciliation'], indent=2, sort_keys=True) + '\n').encode(),
                             ContentType='application/json')
        return {'inventory': output, 'reconciliation': f's3://{bucket}/{report_key}'}

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    sidecar = _local_sidecar(path)
    os.umask(0o077)
    for destination, data in (
        (path, _jsonl(result['pages'])),
        (sidecar, (json.dumps(result['reconciliation'], indent=2, sort_keys=True) + '\n').encode()),
    ):
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    return {'inventory': str(path), 'reconciliation': str(sidecar)}
