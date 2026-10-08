"""Read-only sources. S3 access is one bounded GET per file, with no LIST or writes."""
from pathlib import Path
from .catalog import s3_location


class LocalSource:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.identity = 'local:' + str(self.root)
        self.requests = 0
        self.bytes_read = 0
        self.budgeted_bytes = 0

    def read(self, row, limit):
        path = (self.root / row['local_file']).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('local source path escapes root')
        if path.stat().st_size > limit:
            raise ValueError('file_size_limit')
        with path.open('rb') as stream:
            data = stream.read(limit)
        self.bytes_read += len(data)
        self.budgeted_bytes += len(data)
        return data, {}


class S3Source:
    identity = 's3:compass-prod-i2-files:us-east-1:default-s3fs-prefixes'

    def __init__(self, client):
        self.client = client
        self.requests = 0
        self.bytes_read = 0
        self.budgeted_bytes = 0

    def read(self, row, limit):
        bucket, key = s3_location(row['uri'])
        params = dict(Bucket=bucket, Key=key, Range=f'bytes=0-{limit-1}')
        if row.get('version_id'):
            params['VersionId'] = row['version_id']
        self.requests += 1
        self.budgeted_bytes += limit
        response = self.client.get_object(**params)
        body = response['Body']
        self.budgeted_bytes -= limit - min(limit, response['ContentLength'])
        try:
            total = int(response.get('ContentRange', '/' + str(response['ContentLength'])).rsplit('/', 1)[-1])
            if total > limit:
                raise ValueError('file_size_limit')
            chunks = []
            received = 0
            while received < total:
                chunk = body.read(min(65536, total - received))
                if not chunk:
                    raise ValueError('incomplete_object_read')
                received += len(chunk)
                self.bytes_read += len(chunk)
                chunks.append(chunk)
            data = b''.join(chunks)
            return data, {'version_id': response.get('VersionId'), 'etag': response.get('ETag'),
                          'last_modified': str(response.get('LastModified', ''))}
        finally:
            body.close()
