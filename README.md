# OCR Search for ArchivesSpace

This repository contains the `ocr_search` ArchivesSpace PUI plugin, a Solr OCR
indexer, bounded discovery tools, and the Solr configuration used by the indexer.
The plugin is at the repository root so this checkout can be installed directly as
an ArchivesSpace plugin directory named `ocr_search`; ArchivesSpace ignores the
other top-level directories.

## Components

- `plugin_init.rb`, `lib/`, `public/`, and `test/`: the ArchivesSpace search and
  result views, search client, catalog, highlighting behavior, and tests.
- `indexer/`: validates supplied hOCR and IIIF evidence and synchronizes one named
  corpus to Solr. Its fixtures contain invented text for two sample items, not a
  complete searchable collection.
- `discovery/`: bounded metadata import and OCR validation utilities.
- `solr/`: a standalone Solr service definition and OCR schema/configuration.
- `docs/`: local Solr and discovery runbooks.

## ArchivesSpace setup

Install this checkout in the ArchivesSpace plugin directory under the name
`ocr_search`, enable `ocr_search` in ArchivesSpace configuration, and restart
ArchivesSpace. Set the server-side `OCR_SEARCH_API_URL` environment variable to
the search endpoint. The browser does not supply or choose this URL.

The plugin calls the companion Yii backend's `GET /api/ocr/search` endpoint. That
endpoint is currently implemented in the coordination repository's Yii backend;
the PHP implementation is not part of this repository. See the API contract below
before connecting another implementation.

OCR highlights also depend on the viewer's public `digital-viewer:open` event. The
viewer dispatches a bubbling `CustomEvent` on its container after it opens, with the
OpenSeadragon viewer at `event.detail.viewer`. The OCR plugin listens for that event
to draw and navigate word highlights.

## Search API contract

The plugin makes a server-side GET request to `/api/ocr/search`. It sends `q`,
`per_page=25`, and `page`; it also sends `item` when a specific item is selected.
The endpoint accepts these parameters:

| Parameter | Contract |
| --- | --- |
| `q` | Required, 1–200 characters and 1–20 words or quoted phrases. All terms must match. Search operators, wildcards, and field selection are not supported. |
| `item` | Optional allowlisted item identifier. |
| `collection` | Optional ArchivesSpace resource path such as `/repositories/4/resources/1266`. |
| `page` | Optional page number from 1 to 200; defaults to 1. |
| `per_page` | Optional result page size from 1 to 25; defaults to 10. |

A successful response has this shape:

```json
{
  "query": "mascot",
  "corpus": "mascot-pilot",
  "total_pages": 2,
  "page": 1,
  "per_page": 10,
  "partial": false,
  "solr_time_ms": 4,
  "results": [
    {
      "id": "mascot-pilot:scw:example",
      "item_id": "scw",
      "collection_id": "/repositories/4/resources/1266",
      "title": "Example item title",
      "page_number": 8,
      "page_label": "Example item, Page 8",
      "canvas_id": "https://compass.fivecolleges.edu/node/1344523/canvas/6534396",
      "source_manifest_url": "https://compass.fivecolleges.edu/node/1344523/manifest",
      "aspace_record": "https://findingaids.smith.edu/repositories/4/archival_objects/410931",
      "snippets": [
        {
          "html": "A <mark>mascot</mark> appears on this page.",
          "pages": [],
          "regions": [],
          "highlights": []
        }
      ]
    }
  ]
}
```

`total_pages` is the number of matching pages. A result includes stable page and
item metadata plus up to five OCR snippets. Snippet text is escaped; `<mark>` is
the only markup added by the API. Highlight geometry is returned in `regions` and
`highlights`. `source_manifest_url` identifies provenance and is not, by itself, a
viewer destination. `partial` indicates incomplete search or highlighting work.

Invalid input returns HTTP 400. An unavailable or unconfigured search service
returns HTTP 503.

## Tests

Run from the repository root:

```sh
ruby test/catalog_test.rb
ruby test/search_client_test.rb
node --test test/highlights.test.cjs
python3 -m unittest discover -s indexer -p 'test_index.py'
python3 -m unittest discovery.test_discovery
```

`indexer/test_index.py` uses the synthetic fixtures by default. To run it against a
local corpus, set `OCR_FIXTURE_DIR` to the directory containing its hOCR files and
IIIF manifests, and `OCR_EVIDENCE_FILE` to the matching evidence JSON file.

`indexer/test_http.py` is an integration test for a live search API and Solr
service. It is skipped unless both `OCR_SEARCH_BASE_URL` and an existing
`OCR_EVIDENCE_FILE` are set. For a local corpus, also set `OCR_FIXTURE_DIR` to its
hOCR and manifest directory. `OCR_SEARCH_SOLR_URL` selects the Solr endpoint
(default `http://127.0.0.1:18983/solr/ocr`). For example:

```sh
OCR_SEARCH_BASE_URL=http://127.0.0.1:18093/api/ocr/search \
OCR_EVIDENCE_FILE=/path/to/evidence.json \
OCR_FIXTURE_DIR=/path/to/hocr-and-manifests \
python3 -m unittest discover -s indexer -p 'test_http.py'
```

The browser test requires a running ArchivesSpace PUI and its configured search API,
plus Playwright. Set `PLAYWRIGHT_MODULE` to the Playwright module path. Set
`CHROME_PATH` to a browser executable if needed; otherwise Playwright uses its
bundled browser.

## Licence

This repository is dedicated to the public domain under CC0 1.0 Universal.
