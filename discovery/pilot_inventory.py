"""Build an offline regression inventory from OCR-001 evidence; not a fresh S3 export."""
import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


def rows(evidence):
    items = {item['id']: item for item in evidence['items']}
    for number, page in enumerate(evidence['pages'], 1):
        item = items[page['item']]
        path = unquote(urlsplit(page['hocr_url']).path).split('/system/files/', 1)[1]
        yield {
            'file_id': number, 'id_source': 'synthetic local regression ID, not Drupal fid',
            'filename': path.rsplit('/', 1)[-1], 'uri': 'private://' + path,
            'exported_at': '2026-09-21 retained OCR-001 evidence',
            'recorded_bytes': page['bytes'], 'local_file': page['local_file'],
            'expected_sha256': page['sha256'], 'campus_ids': '169',
            'media_ids': '', 'page_ids': '', 'item_ids': str(item['compass_node']),
            'canvas_id': page['canvas'], 'aspace_record': page['aspace_record'],
            'image_width': page['page_bbox'][2], 'image_height': page['page_bbox'][3],
            'image_dimensions_source': 'OCR-001 manifest/corpus evidence; no fresh image request',
            'source_manifest_url': item['manifest_url'], 'image_service': page['image_service'],
            'access_status': 'unreviewed by discovery tool',
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    for row in rows(json.loads(args.evidence.read_text())):
        print(json.dumps(row))
