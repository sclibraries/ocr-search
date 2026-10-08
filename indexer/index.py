#!/usr/bin/env python3
"""Validate a public pilot allowlist and synchronize its page documents to Solr."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def local_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('source path escapes corpus directory')
    return path


def checked_bytes(path, digest):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('source checksum mismatch: ' + path.name)
    return data


def validate_hocr(data, canvas):
    if b'<!ENTITY' in data.upper():
        raise ValueError('entity declarations are not supported')
    tree = ET.fromstring(data)
    pages = [e for e in tree.iter() if 'ocr_page' in e.get('class', '').split()]
    words = [e for e in tree.iter() if set(e.get('class', '').split()) & {'ocrx_word', 'ocr_word'}]
    if len(pages) != 1 or not any(''.join(e.itertext()).strip() for e in words):
        raise ValueError('expected one nonempty hOCR page')
    bounds = re.search(r'\bbbox (\d+) (\d+) (\d+) (\d+)', pages[0].get('title', ''))
    if not bounds or list(map(int, bounds.groups())) != [0, 0, canvas['width'], canvas['height']]:
        raise ValueError('hOCR dimensions do not match canvas')
    for word in words:
        box = re.search(r'\bbbox (\d+) (\d+) (\d+) (\d+)', word.get('title', ''))
        if not box:
            raise ValueError('word has no coordinates')
        x, y, right, bottom = map(int, box.groups())
        if not (0 <= x <= right <= canvas['width'] and 0 <= y <= bottom <= canvas['height']):
            raise ValueError('word coordinates outside canvas')
    return data.decode('utf-8')


def build_documents(evidence, source, allowed, corpus):
    if not re.fullmatch(r'[a-z0-9-]{1,64}', corpus):
        raise ValueError('invalid corpus identifier')
    items = {item['id']: item for item in evidence['items']}
    if not allowed or not allowed <= items.keys():
        raise ValueError('explicit known public item allowlist required')
    manifests = {}
    for item_id in allowed:
        if not re.fullmatch(r'[a-z0-9-]{1,64}', item_id):
            raise ValueError('invalid item identifier')
        item = items[item_id]
        manifests[item_id] = json.loads(checked_bytes(
            local_file(source, item_id + '-manifest.json'), item['manifest_sha256']))
    documents, seen = [], set()
    for page in evidence['pages']:
        item_id = page['item']
        if item_id not in allowed:
            continue
        item = items[item_id]
        canvases = manifests[item_id]['sequences'][0]['canvases']
        number = page['page']
        if type(number) is not int or not 1 <= number <= len(canvases):
            raise ValueError('invalid canvas page number')
        canvas = canvases[number - 1]
        if (page['canvas'] != canvas['@id'] or page['hocr_url'] != canvas['seeAlso']['@id']
                or page['label'] != canvas['label'] or page['aspace_record'] != item['aspace_record']):
            raise ValueError('canvas association mismatch')
        page_id = corpus + ':' + item_id + ':' + hashlib.sha256(page['canvas'].encode()).hexdigest()[:24]
        if page_id in seen:
            raise ValueError('duplicate page identifier')
        seen.add(page_id)
        data = checked_bytes(local_file(source, page['local_file']), page['sha256'])
        documents.append({
            'id': page_id, 'corpus_id': corpus, 'access': 'public', 'item_id': item_id,
            'collection_id': urllib.parse.urlparse(item['aspace_collection']).path,
            'title': item['title'], 'page_number': number, 'page_label': page['label'],
            'canvas_id': page['canvas'], 'source_manifest_url': item['manifest_url'],
            'aspace_record': item['aspace_record'], 'source_sha256': page['sha256'],
            'ocr': validate_hocr(data, canvas),
        })
    if not documents or len(documents) > 10000:
        raise ValueError('pilot corpus must contain 1–10000 pages')
    for item_id in allowed:
        if not any(d['item_id'] == item_id for d in documents):
            raise ValueError('allowlisted item has no pages')
    return documents


def request(base, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base.rstrip('/') + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def synchronize(base, corpus, documents):
    # Validate the entire input before calling this function. Only this corpus is owned.
    params = urllib.parse.urlencode({'q': 'corpus_id:"' + corpus + '"', 'fl': 'id', 'rows': 10000, 'wt': 'json'})
    existing = request(base, '/select?' + params)['response']
    if existing['numFound'] > 10000:
        raise ValueError('existing corpus exceeds pilot limit')
    desired = {doc['id'] for doc in documents}
    stale = [doc['id'] for doc in existing['docs'] if doc['id'] not in desired]
    request(base, '/update?commit=true', documents)
    if stale:
        request(base, '/update?commit=true', {'delete': [{'id': page_id} for page_id in stale]})
    return {'indexed': len(documents), 'removed': len(stale), 'corpus': corpus}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--public-item', action='append', required=True,
                        help='Explicitly allow this public item ID (repeat for each item)')
    parser.add_argument('--corpus', default='mascot-pilot')
    parser.add_argument('--solr', default='http://127.0.0.1:18983/solr/ocr')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    docs = build_documents(json.loads(args.evidence.read_text()), args.source, set(args.public_item), args.corpus)
    result = {'validated': len(docs), 'corpus': args.corpus, 'executed': False}
    if args.execute:
        result.update(synchronize(args.solr, args.corpus, docs), executed=True)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
