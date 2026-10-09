"""Join a versioned relationship export to synthetic/approved evidence and IIIF manifests."""

import base64
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


PAGE_INVENTORY_SCHEMA_VERSION = 1
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_METADATA_LABEL_FIELDS = ('series', 'issue_date')


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


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        if '@value' in value:
            return _text(value['@value'])
        for language in ('en', '@none'):
            if language in value:
                text = _text(value[language])
                if text:
                    return text
        for part in value.values():
            text = _text(part)
            if text:
                return text
    if isinstance(value, (list, tuple)):
        for part in value:
            text = _text(part)
            if text:
                return text
    return ''


def _prepare_manifest_metadata_labels(labels):
    if labels is None:
        labels = {}
    if not isinstance(labels, dict) or set(labels) - set(_METADATA_LABEL_FIELDS):
        raise ValueError('manifest metadata labels must map series and issue_date to label-name lists')
    prepared = {}
    for field in _METADATA_LABEL_FIELDS:
        values = labels.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f'manifest metadata {field} must be a list of label names')
        normalized = []
        seen = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'manifest metadata {field} must be a list of label names')
            name = value.strip()
            if name.casefold() not in seen:
                normalized.append(name)
                seen.add(name.casefold())
        prepared[field] = normalized
    return prepared


def read_manifest_metadata_labels(path):
    """Read per-field IIIF metadata label aliases from a JSON settings file."""
    try:
        labels = json.loads(Path(path).read_text())
    except json.JSONDecodeError as error:
        raise ValueError('manifest metadata label settings are not valid JSON') from error
    return _prepare_manifest_metadata_labels(labels)


def _manifest_metadata_value(manifest, field, labels):
    aliases = labels[field]
    if not aliases:
        return ''
    aliases_by_casefold = {label.casefold() for label in aliases}
    matches = {}
    metadata = manifest.get('metadata')
    if not isinstance(metadata, list):
        return ''
    for entry in metadata:
        if not isinstance(entry, dict):
            continue
        label = _text(entry.get('label')).casefold()
        if label not in aliases_by_casefold or label in matches:
            continue
        value = _text(entry.get('value'))
        if value:
            matches[label] = value
    for alias in aliases:
        value = matches.get(alias.casefold())
        if value:
            return value
    return ''


def _normalize_issue_date(value):
    """Normalize recognized full or partial dates without inferring missing parts."""
    value = value.strip()
    if not value:
        return ''
    partial = re.fullmatch(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?', value)
    if partial:
        year_text, month_text, day_text = partial.groups()
        year = int(year_text)
        try:
            if month_text is None:
                date(year, 1, 1)
                return f'{year:04d}'
            month = int(month_text)
            if day_text is None:
                date(year, month, 1)
                return f'{year:04d}-{month:02d}'
            return date(year, month, int(day_text)).isoformat()
        except ValueError:
            return ''

    for date_format in ('%B %d, %Y', '%b %d, %Y', '%B %Y', '%b %Y'):
        try:
            parsed = datetime.strptime(value, date_format)
        except ValueError:
            continue
        if '%d' in date_format:
            return parsed.date().isoformat()
        return f'{parsed.year:04d}-{parsed.month:02d}'
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
    if not directory:
        return by_uri, by_sha256
    for path in sorted(Path(directory).rglob('*.json')):
        raw = path.read_bytes()
        manifest = json.loads(raw)
        uri = manifest.get('@id') or manifest.get('id')
        if uri and uri in by_uri:
            raise ValueError(f'duplicate manifest identifier: {uri}')
        canvases = _manifest_canvases(manifest)
        if not canvases or any(not canvas['id'] for canvas in canvases):
            raise ValueError(f'manifest has missing canvases or canvas identifiers: {path.name}')
        manifest_entry = {'manifest': manifest, 'canvases': canvases}
        if uri:
            by_uri[uri] = manifest_entry
        by_sha256[hashlib.sha256(raw).hexdigest()] = manifest_entry
    return by_uri, by_sha256


def _evidence_maps(evidence):
    evidence = evidence or {}
    if not isinstance(evidence, dict):
        raise ValueError('evidence file must contain a JSON object')
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
        digest = str(page.get('sha256') or '').lower()
        if digest and not _SHA256.fullmatch(digest):
            raise ValueError('evidence page has an invalid SHA-256 checksum')
        key = (item_key, page_number)
        if key in pages_by_item_order:
            raise ValueError(f'duplicate evidence page: {item_key} page {page_number}')
        pages_by_item_order[key] = dict(page, sha256=digest)
        if page.get('page_id') is not None:
            pages_by_item_id[(item_key, str(page['page_id']))] = pages_by_item_order[key]
    return items_by_id, pages_by_item_order, pages_by_item_id


def _item_evidence(raw_item_id, items_by_id):
    item = items_by_id.get(str(raw_item_id))
    if item is None:
        return {'id': str(raw_item_id)}
    return item


def _prepare_access_mapping(mapping):
    if not isinstance(mapping, dict) or mapping.get('schema_version') != 1:
        raise ValueError('access mapping must be a schema-version 1 JSON object')
    no_terms = str(mapping.get('published_without_terms', '')).strip().lower()
    if no_terms not in {'public', 'restricted'}:
        raise ValueError('access mapping needs published_without_terms set to public or restricted')
    entries = mapping.get('terms')
    if not isinstance(entries, list):
        raise ValueError('access mapping terms must be a list')

    by_id, by_name = {}, {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('each access mapping term must be an object')
        access = str(entry.get('access', '')).strip().lower()
        if access not in {'public', 'restricted'}:
            raise ValueError('access mapping term values must be public or restricted')
        term_id = str(entry.get('id', '')).strip()
        name = str(entry.get('name', '')).strip().casefold()
        if not term_id and not name:
            raise ValueError('each access mapping term needs an id or name')
        for key, lookup in ((term_id, by_id), (name, by_name)):
            if key:
                if key in lookup and lookup[key] != access:
                    raise ValueError(f'conflicting access mapping for term {key}')
                lookup[key] = access
    return {'published_without_terms': no_terms, 'by_id': by_id, 'by_name': by_name}


def read_access_mapping(path):
    """Load a required operator-reviewed access mapping file."""
    try:
        mapping = json.loads(Path(path).read_text())
    except json.JSONDecodeError as error:
        raise ValueError('access mapping file is not valid JSON') from error
    _prepare_access_mapping(mapping)
    return mapping


def classify_access(publication_state, access_terms, access_term_ids, mapping):
    """Classify only from an explicit reviewed mapping; unknown values fail closed."""
    return _classify_access_prepared(publication_state, access_terms, access_term_ids,
                                     _prepare_access_mapping(mapping))


def _classify_access_prepared(publication_state, access_terms, access_term_ids, prepared):
    state = str(publication_state or '').strip().casefold()
    if state in {'unpublished', 'draft', '0', 'false', 'withdrawn'}:
        return 'restricted'
    if state not in {'published', '1', 'true'}:
        return 'unknown'

    term_ids = _strings(access_term_ids)
    names = [name.casefold() for name in _strings(access_terms)]
    if not term_ids and not names:
        return prepared['published_without_terms']

    id_values = [prepared['by_id'].get(term_id) for term_id in term_ids]
    name_values = [prepared['by_name'].get(name) for name in names]
    if 'restricted' in id_values or 'restricted' in name_values:
        return 'restricted'
    if term_ids and all(value == 'public' for value in id_values):
        return 'public'
    if names and all(value == 'public' for value in name_values):
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


def _report(pages, sample_size, dates_unparsed=()):
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
        'missing_item_titles': len({page['item_id'] for page in pages if not page['item_title']}),
        'missing_series': len({page['item_id'] for page in pages if not page['series']}),
        'missing_issue_dates': len({page['item_id'] for page in pages if not page['issue_date']}),
        'dates_unparsed': len(dates_unparsed),
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
                    access_mapping=None, compass_manifest_directory=None, sample_size=10,
                    manifest_metadata_labels=None):
    """Build the page inventory and reconciliation report without writing or uploading data."""
    if not 1 <= sample_size <= 100:
        raise ValueError('canvas sample size must be 1–100')
    if access_mapping is None:
        raise ValueError('a reviewed access mapping file is required')
    prepared_access = _prepare_access_mapping(access_mapping)
    prepared_metadata_labels = _prepare_manifest_metadata_labels(manifest_metadata_labels)
    items_by_id, evidence_pages, evidence_pages_by_id = _evidence_maps(evidence)
    manifests, manifests_by_sha256 = _read_manifests(manifest_directory)
    compass_manifests = {}
    if compass_manifest_directory:
        from .manifests import read_manifest_index
        for entry in read_manifest_index(compass_manifest_directory):
            manifest = json.loads((Path(compass_manifest_directory) / entry['filename']).read_bytes())
            canvases = _manifest_canvases(manifest)
            if not canvases or any(not canvas['id'] for canvas in canvases):
                raise ValueError(f"Compass manifest has missing canvas identifiers: {entry['filename']}")
            compass_manifests[entry['item_id']] = {
                'url': entry['manifest_url'], 'manifest': manifest, 'canvases': canvases,
            }
    seen_file_associations, seen_pages = set(), set()
    pages = []
    dates_unparsed = set()

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
        manifest_url = row.get('manifest_url') or item.get('manifest_url')
        manifest_entry = manifests.get(manifest_url)
        if manifest_entry is None and item.get('manifest_sha256'):
            manifest_entry = manifests_by_sha256.get(str(item['manifest_sha256']).lower())
        if manifest_entry is None:
            compass_manifest = compass_manifests.get(str(row['item_id']))
            if compass_manifest is None:
                raise ValueError(f'no converted or Compass manifest for {stable_item_id}')
            manifest_entry = compass_manifest
            manifest_url = manifest_url or compass_manifest['url']
        manifest = manifest_entry['manifest']
        canvases = manifest_entry['canvases']
        if page_order > len(canvases):
            raise ValueError(f'manifest has no canvas for {stable_item_id} page {page_order}')
        canvas = canvases[page_order - 1]
        evidence_page = evidence_page or {}
        evidence_canvas = evidence_page.get('canvas') or evidence_page.get('canvas_id')
        supplied_canvas = row.get('canvas_id')
        if supplied_canvas and supplied_canvas != canvas['id']:
            raise ValueError(f'export canvas does not match manifest for {stable_item_id} page {page_order}')
        if evidence_canvas and evidence_canvas != canvas['id']:
            raise ValueError(f'evidence canvas does not match manifest for {stable_item_id} page {page_order}')

        expected_sha256 = str(evidence_page.get('sha256') or '').lower()
        supplied_sha256 = row.get('sha256') or row.get('expected_sha256')
        if expected_sha256 and supplied_sha256 and str(supplied_sha256).lower() != expected_sha256:
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
        if remote_checksum and expected_sha256:
            remote_sha256 = base64.b64decode(remote_checksum).hex()
            if remote_sha256 != expected_sha256:
                issues.append('s3_checksum_mismatch')
        ancestry = _paths(row.get('collection_ancestry'))
        item_title = _text(manifest.get('label'))
        series = _manifest_metadata_value(manifest, 'series', prepared_metadata_labels)
        raw_issue_date = _manifest_metadata_value(manifest, 'issue_date', prepared_metadata_labels)
        issue_date = _normalize_issue_date(raw_issue_date)
        if raw_issue_date and not issue_date:
            dates_unparsed.add(stable_item_id)
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
            'item_title': item_title,
            'series': series,
            'issue_date': issue_date,
            'aspace_record': (evidence_page.get('aspace_record') or item.get('aspace_record')
                              or row.get('aspace_record')),
            's3_bucket': bucket,
            's3_key': key,
            's3_version_id': head.get('VersionId'),
            's3_etag': head.get('ETag'),
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
            'access': _classify_access_prepared(row.get('publication_state'),
                                                row.get('access_terms'),
                                                row.get('access_term_ids'), prepared_access),
            'issues': issues,
        }
        pages.append(page)

    pages.sort(key=lambda page: (page['item_id'], page['page_number'], str(page['file_id'])))
    return {'pages': pages, 'reconciliation': _report(pages, sample_size, dates_unparsed)}


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
