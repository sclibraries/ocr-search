"""Sequential, checkpointed content validation with explicit sample budgets."""
from datetime import datetime, timezone
import hashlib
import time
from .validation import inspect_hocr


def ids(value):
    return set(str(value or '').split(',')) - {''}


def metadata_issues(row, result):
    issues = ['access_review_required']
    campuses = ids(row.get('campus_ids'))
    if campuses != {'169'}:
        issues.append('campus_conflict' if '169' in campuses else 'smith_ownership_unresolved')
    for key in ('page_ids', 'item_ids', 'media_ids'):
        if len(ids(row.get(key))) != 1:
            issues.append('ambiguous_or_missing_' + key)
    if not row.get('canvas_id'):
        issues.append('canvas_unresolved')
    if not row.get('aspace_record'):
        issues.append('aspace_record_unresolved')
    if not row.get('image_width') or not row.get('image_height'):
        issues.append('image_dimensions_unverified')
    elif result.get('ocr_width') and (result['ocr_width'], result['ocr_height']) != (row['image_width'], row['image_height']):
        issues.append('image_dimensions_mismatch')
    if result.get('bytes') is not None and result['bytes'] != int(row['recorded_bytes']):
        issues.append('recorded_size_mismatch')
    if row.get('expected_sha256') and result.get('sha256') != row['expected_sha256']:
        issues.append('source_checksum_mismatch')
    return issues


def audit(catalog, source, max_files=100, max_bytes=50_000_000, max_file_bytes=2_000_000,
          max_seconds=300, max_requests=100, delay=0):
    if min(max_files, max_bytes, max_file_bytes, max_seconds, max_requests) <= 0 or delay < 0:
        raise ValueError('budgets must be positive; delay must be nonnegative')
    catalog.bind_source(source.identity)
    start = time.monotonic()
    initial_bytes, initial_requests = source.bytes_read, source.requests
    initial_budget = source.budgeted_bytes
    processed, reason = 0, 'inventory_exhausted'
    for file_id, row in catalog.pending():
        if processed >= max_files:
            reason = 'file_budget'
            break
        if time.monotonic() - start >= max_seconds:
            reason = 'time_budget'
            break
        if source.requests - initial_requests >= max_requests:
            reason = 'request_budget'
            break
        if max_bytes - (source.budgeted_bytes - initial_budget) < max_file_bytes:
            reason = 'byte_budget'
            break
        before = source.bytes_read
        try:
            data, metadata = source.read(row, max_file_bytes)
            result = dict(inspect_hocr(data), **metadata, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        except Exception as error:
            # Persist a category, not credential-bearing endpoint/error messages.
            response = getattr(error, 'response', {})
            code = response.get('Error', {}).get('Code', type(error).__name__)
            category = str(error) if isinstance(error, ValueError) else code
            result = {'status': 'read_error', 'error': category}
        result.update(public_index_approved=False, checked_at=datetime.now(timezone.utc).isoformat(),
                      source=source.identity, transferred_bytes=source.bytes_read-before)
        result['issues'] = metadata_issues(row, result)
        catalog.save(file_id, result)
        processed += 1
        if delay:
            time.sleep(delay)
    return {'processed': processed, 'pending': sum(1 for _ in catalog.pending()),
            'bytes_read': source.bytes_read-initial_bytes, 'get_requests': source.requests-initial_requests,
            'budgeted_bytes': source.budgeted_bytes-initial_budget,
            'list_requests': 0, 'head_requests': 0, 'elapsed_seconds': round(time.monotonic()-start, 3),
            'stop_reason': reason}
