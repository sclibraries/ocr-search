"""Fetch and privately checkpoint Compass IIIF manifests."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


MANIFEST_INDEX = 'manifest-index.jsonl'
MANIFEST_INDEX_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 25_000_000


def _manifest_url(base_url, item_id):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('Compass manifest base URL must be an HTTPS origin without credentials or query')
    return f'{base_url.rstrip("/")}/node/{quote(str(item_id), safe="")}/manifest'


def _http_get(url, timeout):
    request = Request(url, headers={'Accept': 'application/json, application/ld+json'})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_MANIFEST_BYTES + 1)
    if len(body) > MAX_MANIFEST_BYTES:
        raise ValueError('Compass manifest exceeds the 25 MB safety limit')
    return body


def _index_path(directory):
    return Path(directory) / MANIFEST_INDEX


def _trim_partial_index_line(path):
    if not path.exists():
        return
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        final_newline = raw.rfind(b'\n') + 1
        with path.open('r+b') as stream:
            stream.truncate(final_newline)
            stream.flush()
            os.fsync(stream.fileno())


def _read_index(directory):
    path = _index_path(directory)
    _trim_partial_index_line(path)
    if not path.exists():
        return {}
    entries = {}
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f'manifest index line {line_number} is not JSON') from error
            if not isinstance(entry, dict) or entry.get('schema_version') != MANIFEST_INDEX_SCHEMA_VERSION:
                raise ValueError(f'manifest index line {line_number} has an unsupported schema')
            item_id = str(entry.get('item_id', ''))
            filename = str(entry.get('filename', ''))
            digest = str(entry.get('sha256', '')).lower()
            if not item_id or not filename or Path(filename).name != filename or len(digest) != 64:
                raise ValueError(f'manifest index line {line_number} is missing required fields')
            entries[item_id] = entry
    return entries


def _valid_entry(directory, entry):
    path = Path(directory) / entry['filename']
    if not path.is_file():
        return False
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        return False
    try:
        return isinstance(json.loads(raw), dict)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False


def read_manifest_index(directory):
    """Return verified manifest index entries without making any network calls."""
    entries = _read_index(directory)
    invalid = [item_id for item_id, entry in entries.items() if not _valid_entry(directory, entry)]
    if invalid:
        raise ValueError(f'manifest index has missing or checksum-invalid files for items: {", ".join(invalid[:10])}')
    return [entries[item_id] for item_id in sorted(entries)]


def _append_index(directory, entry):
    path = _index_path(directory)
    encoded = (json.dumps(entry, sort_keys=True, separators=(',', ':')) + '\n').encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _store_manifest(directory, item_id, body):
    filename = hashlib.sha256(item_id.encode()).hexdigest() + '.json'
    destination = Path(directory) / filename
    descriptor, temporary_name = tempfile.mkstemp(prefix='.manifest-', dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return filename


def fetch_manifests(export_rows, output_dir, base_url, requests_per_second=1.0,
                    fetcher=None, sleep_fn=time.sleep, timeout=10):
    """Fetch missing item manifests, save mode-0600 files, and resume from the index.

    ``fetcher`` is an injection point for deterministic offline tests. The default
    performs HTTPS GETs only when this function is called by the operator command.
    """
    if requests_per_second <= 0:
        raise ValueError('requests per second must be positive')
    if timeout <= 0:
        raise ValueError('manifest request timeout must be positive')
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    entries = _read_index(directory)
    for item_id, entry in list(entries.items()):
        if not _valid_entry(directory, entry):
            entries.pop(item_id)

    item_ids = sorted({str(row.get('item_id', '')) for row in export_rows})
    if any(not item_id for item_id in item_ids):
        raise ValueError('every export row needs an item_id before fetching manifests')
    get = fetcher or (lambda url: _http_get(url, timeout))
    request_interval = 1.0 / requests_per_second
    last_request = None
    for item_id in item_ids:
        existing = entries.get(item_id)
        if existing and _valid_entry(directory, existing):
            continue
        url = _manifest_url(base_url, item_id)
        if last_request is not None:
            elapsed = time.monotonic() - last_request
            if elapsed < request_interval:
                sleep_fn(request_interval - elapsed)
        last_request = time.monotonic()
        body = get(url)
        if isinstance(body, str):
            body = body.encode()
        if not isinstance(body, bytes) or len(body) > MAX_MANIFEST_BYTES:
            raise ValueError('Compass manifest response is not bytes or exceeds the 25 MB safety limit')
        try:
            manifest = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('Compass manifest response is not valid JSON') from error
        if not isinstance(manifest, dict):
            raise ValueError('Compass manifest response must be a JSON object')
        filename = _store_manifest(directory, item_id, body)
        entry = {
            'schema_version': MANIFEST_INDEX_SCHEMA_VERSION,
            'item_id': item_id,
            'manifest_url': url,
            'filename': filename,
            'sha256': hashlib.sha256(body).hexdigest(),
            'bytes': len(body),
        }
        _append_index(directory, entry)
        entries[item_id] = entry
    return [entries[item_id] for item_id in item_ids]
