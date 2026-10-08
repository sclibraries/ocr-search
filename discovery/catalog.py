"""Bounded metadata import and durable discovery checkpoints; never an access grant."""
import hashlib
import json
import sqlite3


def s3_location(uri):
    scheme, separator, path = uri.partition('://')
    prefixes = {'private': 's3fs-private', 'public': 's3fs-public'}
    if not separator or scheme not in prefixes or not path or path.startswith('/'):
        raise ValueError('unsupported Drupal file URI')
    return 'compass-prod-i2-files', prefixes[scheme] + '/' + path


class Catalog:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, fingerprint TEXT, metadata TEXT, result TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')

    def close(self):
        self.db.close()

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM files').fetchone()[0]

    def bind_source(self, identity):
        old = self.db.execute("SELECT value FROM settings WHERE key='source'").fetchone()
        if old and old[0] != identity:
            raise ValueError('checkpoint belongs to another source; use a separate database')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('source', ?)", (identity,))

    def ingest(self, path, limit=100):
        seen = set()
        with self.db, open(path) as stream:
            for number, line in enumerate(stream, 1):
                if number > limit:
                    raise ValueError('inventory exceeds import limit; transaction rolled back')
                row = json.loads(line)
                file_id = int(row['file_id'])
                if file_id <= 0 or file_id in seen:
                    raise ValueError('duplicate or invalid file ID')
                seen.add(file_id)
                for key in ('uri', 'filename', 'exported_at'):
                    if not isinstance(row.get(key), str) or not row[key]:
                        raise ValueError('missing ' + key)
                s3_location(row['uri'])
                if int(row['recorded_bytes']) < 0:
                    raise ValueError('negative size')
                payload = json.dumps(row, sort_keys=True)
                fingerprint = hashlib.sha256(payload.encode()).hexdigest()
                old = self.db.execute('SELECT fingerprint FROM files WHERE id=?', (file_id,)).fetchone()
                if not old or old[0] != fingerprint:
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?, NULL)',
                                    (file_id, fingerprint, payload))
            if self.count() > 100:
                raise ValueError('checkpoint exceeds 100 files; use a separate output directory')
        return len(seen)

    def retry_errors(self):
        with self.db:
            for file_id, payload in self.db.execute('SELECT id, result FROM files WHERE result IS NOT NULL'):
                if json.loads(payload)['status'] == 'read_error':
                    self.db.execute('UPDATE files SET result=NULL WHERE id=?', (file_id,))

    def pending(self):
        for file_id, payload in self.db.execute('SELECT id, metadata FROM files WHERE result IS NULL ORDER BY id'):
            yield file_id, json.loads(payload)

    def save(self, file_id, result):
        with self.db:
            self.db.execute('UPDATE files SET result=? WHERE id=?', (json.dumps(result), file_id))

    def report(self):
        rows = []
        for payload, result in self.db.execute('SELECT metadata, result FROM files ORDER BY id'):
            row = json.loads(payload)
            row.update(json.loads(result) if result else {'status': 'pending', 'issues': [], 'public_index_approved': False})
            rows.append(row)
        return rows
