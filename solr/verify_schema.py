#!/usr/bin/env python3
"""Exercise the OCR schema against a caller-supplied local, disposable Solr core."""
import argparse
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'indexer' / 'fixtures' / 'hocr'
sys.path.insert(0, str(ROOT / 'indexer'))
from miniocr import hocr_to_miniocr

SYNTHETIC_TERMS = {
    'demo-a-01.hocr': 'copper',
    'demo-a-02.hocr': 'silver',
    'demo-b-01.hocr': 'midnight',
    'demo-b-02.hocr': 'curious',
}


def validate_solr_url(value):
    parsed = urlsplit(value)
    path_parts = parsed.path.strip('/').split('/')
    port = parsed.port
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or not port or port in {8983, 18983}
            or len(path_parts) != 2 or path_parts[0] != 'solr'
            or not re.fullmatch(r'[A-Za-z0-9_-]+', path_parts[1])
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('use an explicit local disposable core URL, e.g. http://127.0.0.1:49152/solr/ocr_next')
    return value.rstrip('/')


def request(base_url, path, params=None, payload=None):
    query = '' if not params else '?' + urllib.parse.urlencode(params, doseq=True)
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        base_url + path + query,
        data=data,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def make_page(doc_id, corpus, item_id, page_number, series, year, ocr):
    return {
        'id': doc_id,
        'corpus_id': corpus,
        'access': 'public',
        'item_id': item_id,
        'page_number': page_number,
        'page_label': f'Page {page_number}',
        'canvas_id': f'https://example.test/canvas/{doc_id}',
        'collection_id': '/collection/demo',
        'title': f'Synthetic issue {item_id}',
        'series': series,
        'issue_date': f'{year}-12',
        'year': year,
        'source_manifest_url': f'https://example.test/manifest/{item_id}',
        'source_bucket': 'synthetic-bucket',
        'source_key': f'synthetic/{item_id}/{page_number}.hocr',
        'source_version_id': 'synthetic-version',
        'source_etag': 'synthetic-etag',
        'source_sha256': 'a' * 64,
        'schema_version': 2,
        'ocr': ocr,
    }


def verify(base_url):
    token = uuid.uuid4().hex[:12]
    corpus = 'schema-check-' + token
    comparison_corpus = 'schema-compare-' + token
    legacy_id = 'legacy-' + token
    document_ids = [legacy_id]
    synthetic_files = sorted(FIXTURES.glob('demo-*.hocr'))
    hocr = (FIXTURES / 'demo-a-01.hocr').read_text(encoding='utf-8')
    documents = [{
        'id': legacy_id,
        'corpus_id': corpus,
        'access': 'public',
        'item_id': 'legacy-item',
        'collection_id': '/collection/demo',
        'page_number': 1,
        'page_label': '1',
        'canvas_id': 'https://example.test/canvas/legacy',
        'source_manifest_url': 'https://example.test/manifest/legacy',
        'source_sha256': 'b' * 64,
        'ocr': hocr,
    }]
    documents.extend([
        make_page('demo-a-1-' + token, corpus, 'demo-a-issue', 1,
                  'Demo A Publication', 1927, hocr),
        make_page('demo-a-2-' + token, corpus, 'demo-a-issue', 2,
                  'Demo A Publication', 1927, hocr),
        make_page('demo-b-1-' + token, corpus, 'demo-b-issue', 1,
                  'Demo B Publication', 1928, hocr),
    ])
    stem_id = 'stem-' + token
    documents.append(make_page(stem_id, comparison_corpus, 'demo-stem-item', 1,
                               'Synthetic Stem Publication', 1932,
                               hocr.replace('copper', 'vote', 1)))
    document_ids.extend(doc['id'] for doc in documents[1:])

    hocr_bytes = 0
    miniocr_bytes = 0
    comparison_pairs = []
    for path in synthetic_files:
        hocr_text = path.read_text(encoding='utf-8')
        miniocr_text = hocr_to_miniocr(hocr_text)
        hocr_bytes += len(hocr_text.encode('utf-8'))
        miniocr_bytes += len(miniocr_text.encode('utf-8'))
        item_id = 'demo-a-item' if path.name.startswith('demo-a-') else 'demo-b-item'
        page_number = 1 if '-01.' in path.name else 2
        series = 'Synthetic A' if item_id == 'demo-a-item' else 'Synthetic B'
        year = 1927 if item_id == 'demo-a-item' else 1928
        hocr_id = 'hocr-' + token + '-' + path.stem
        miniocr_id = 'miniocr-' + token + '-' + path.stem
        documents.append(make_page(hocr_id, comparison_corpus, item_id, page_number,
                                   series, year, hocr_text))
        documents.append(make_page(miniocr_id, comparison_corpus, item_id, page_number,
                                   series, year, miniocr_text))
        document_ids.extend((hocr_id, miniocr_id))
        comparison_pairs.append((hocr_id, miniocr_id, SYNTHETIC_TERMS[path.name]))

    comparison_percent = 100.0 * (hocr_bytes - miniocr_bytes) / hocr_bytes

    def highlight_signature(doc_id, word):
        result = request(base_url, '/select', {
            'q': f'ocr:{word}',
            'fq': [f'corpus_id:{comparison_corpus}', f'id:{doc_id}'],
            'fl': 'id',
            'hl': 'true',
            'hl.ocr.fl': 'ocr',
            'hl.ocr.absoluteHighlights': 'true',
            'wt': 'json',
        })
        if result['response']['numFound'] != 1:
            raise AssertionError(f'expected one highlighted document for {doc_id}')
        document = result['response']['docs'][0]
        highlighting = result.get('ocrHighlighting', {}).get(doc_id)
        if not highlighting:
            highlighting = document.get('ocrHighlighting')
        if not highlighting:
            raise AssertionError(f'no OCR highlights returned for {doc_id}')
        ocr_highlighting = highlighting.get('ocr') if isinstance(highlighting, dict) else highlighting
        snippets = (ocr_highlighting.get('snippets')
                    if isinstance(ocr_highlighting, dict) else ocr_highlighting)
        if not snippets:
            raise AssertionError(f'no OCR snippets returned for {doc_id}')
        keys = ('text', 'pages', 'regions', 'highlights')
        return [{key: snippet.get(key) for key in keys} for snippet in snippets]

    try:
        request(base_url, '/update', {'commit': 'true'}, documents)

        stemmed = request(base_url, '/select', {
            'q': 'ocr:voting',
            'fq': [f'corpus_id:{comparison_corpus}', f'id:{stem_id}'],
            'rows': '0',
            'wt': 'json',
        })['response']
        if stemmed['numFound'] != 1:
            raise AssertionError('query "voting" did not match the synthetic page containing "vote"')

        grouped = request(base_url, '/select', {
            'q': '*:*',
            'fq': f'corpus_id:{corpus}',
            'group': 'true',
            'group.field': 'item_id',
            'group.ngroups': 'true',
            'group.limit': '10',
            'rows': '10',
            'wt': 'json',
        })['grouped']['item_id']
        if grouped['ngroups'] != 3:
            raise AssertionError(f"expected 3 item groups, got {grouped['ngroups']}")
        group_page_counts = sorted(group['doclist']['numFound'] for group in grouped['groups'])
        if group_page_counts != [1, 1, 2]:
            raise AssertionError(f'expected group page counts [1, 1, 2], got {group_page_counts}')

        faceted = request(base_url, '/select', {
            'q': '*:*',
            'fq': f'corpus_id:{corpus}',
            'rows': '0',
            'facet': 'true',
            'facet.mincount': '1',
            'facet.field': ['series', 'collection_id', 'issue_date', 'year'],
            'wt': 'json',
        })['facet_counts']['facet_fields']
        if faceted['series'] != ['Demo A Publication', 2, 'Demo B Publication', 1]:
            raise AssertionError(f"unexpected series facet counts: {faceted['series']}")
        if faceted['year'] != ['1927', 2, '1928', 1]:
            raise AssertionError(f"unexpected year facet counts: {faceted['year']}")
        if faceted['collection_id'] != ['/collection/demo', 4]:
            raise AssertionError(f"unexpected collection facet counts: {faceted['collection_id']}")
        if faceted['issue_date'] != ['1927-12', 2, '1928-12', 1]:
            raise AssertionError(f"unexpected issue-date facet counts: {faceted['issue_date']}")

        stored_fields = ('title', 'page_label', 'source_manifest_url', 'series', 'issue_date',
                         'year', 'source_bucket', 'source_key', 'source_version_id',
                         'source_etag', 'schema_version')
        stored_doc = request(base_url, '/select', {
            'q': f'id:{documents[1]["id"]}',
            'fl': ','.join(stored_fields),
            'wt': 'json',
        })['response']['docs'][0]
        for field in stored_fields:
            if stored_doc.get(field) != documents[1][field]:
                raise AssertionError(f'stored field {field} did not round-trip')

        for hocr_id, miniocr_id, word in comparison_pairs:
            hocr_highlights = highlight_signature(hocr_id, word)
            miniocr_highlights = highlight_signature(miniocr_id, word)
            if hocr_highlights != miniocr_highlights:
                raise AssertionError(f'hOCR and MiniOCR highlights differ for {word}')

        legacy = request(base_url, '/select', {
            'q': f'id:{legacy_id}',
            'fl': 'id,title,page_label,source_manifest_url,schema_version,series,year',
            'wt': 'json',
        })['response']
        if legacy['numFound'] != 1 or 'schema_version' in legacy['docs'][0]:
            raise AssertionError('legacy pilot document did not remain readable with new fields absent')

        return {'groups': grouped['ngroups'], 'group_page_counts': group_page_counts,
                'legacy_documents': 1, 'series_facets': faceted['series'],
                'year_facets': faceted['year'], 'synthetic_hocr_field_bytes': hocr_bytes,
                'synthetic_miniocr_field_bytes': miniocr_bytes,
                'synthetic_field_size_reduction_percent': round(comparison_percent, 1),
                'highlight_comparisons': len(comparison_pairs), 'stem_check': 'voting matches vote'}
    finally:
        request(base_url, '/update', {'commit': 'true'},
                {'delete': [{'id': doc_id} for doc_id in document_ids]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solr-url', required=True,
                        help='local throwaway core URL, e.g. http://127.0.0.1:49152/solr/ocr')
    args = parser.parse_args()
    base_url = validate_solr_url(args.solr_url)
    print(json.dumps(verify(base_url), sort_keys=True))


if __name__ == '__main__':
    main()
