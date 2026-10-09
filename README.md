# OCR Search for ArchivesSpace

This repository contains the `ocr_search` ArchivesSpace PUI plugin, a Solr OCR
indexer, bounded discovery tools, and the Solr configuration used by the indexer.
The plugin is at the repository root so this checkout can be installed directly as
an ArchivesSpace plugin directory named `ocr_search`; ArchivesSpace ignores the
other top-level directories.

## Components

- `plugin_init.rb`, `lib/`, `public/`, and `test/`: the ArchivesSpace search and
  result views, search client, catalog, highlighting behavior, and tests.
- `indexer/`: keeps the local pilot indexer and adds a resumable S3 streaming
  indexer. Its fixtures contain invented text for two sample items, not a complete
  searchable collection.
- `discovery/`: bounded metadata import and OCR validation utilities.
- `solr/`: a standalone Solr service definition and OCR schema/configuration.
- `docs/`: local Solr and discovery runbooks.

## Relationship export and page inventory

`python3 -m discovery export-sql` prints one bounded, read-only SQL query; it does
not connect to a database. Pass `--collection-id` and `--hocr-media-use-id`
explicitly. Each query emits JSONL rows with schema version 1 and a limit from 1
to 1000. The coordination repository's operator wrapper runs these queries in a
resumable sequence, defaulting to 100 rows per batch. `--explain` prints an
`EXPLAIN FORMAT=JSON` query. A batch size above 100 requires explicit
`--allow-large-batches` acknowledgement after query-plan review.

`python3 -m discovery manifests` fetches each exported item's Compass IIIF
manifest over HTTPS, with a configurable request rate. It stores manifests in a
private directory and checkpoints their SHA-256 values in a resumable index. This
command runs only when explicitly invoked; tests inject synthetic responses and
make no network requests.

`python3 -m discovery inventory` joins the JSONL export with optional evidence,
optional converted IIIF manifests and/or the Compass manifest cache. It verifies
canvas order and IDs, carries forward an evidence SHA-256 when present (otherwise
leaving it empty for OCR-010), and uses S3 `HeadObject` to record object size,
version ID and ETag. An ancestry walk is capped at 32 levels; the report flags
items that reach that cap. A reviewed access-mapping JSON file is required. It
maps term IDs/names and the published-without-terms case to `public` or
`restricted`; unpublished records are restricted, while unmapped or unclear
values remain `unknown`. The command writes a page-inventory JSONL file and a
reconciliation JSON sidecar to `--output-location` (or `OCR_INVENTORY_OUTPUT`),
which may be a local path or an explicitly supplied `s3://bucket/key` URI. Set
`--source-bucket` or `OCR_SOURCE_BUCKET` for exports that contain keys without a
bucket. The tool has no default bucket or output destination. S3 output is an
explicit write to the operator-supplied location; tests use an in-memory fake and
make no network requests.

The export input, evidence, manifests and generated inventory may contain internal
metadata. Keep operator data in an approved private location outside this public
repository; use the committed synthetic fixtures for local tests.

## Streaming S3 indexing

`python3 -m indexer.stream` reads a page inventory, gets each public hOCR object at
its recorded S3 version, verifies its size, ETag and optional SHA-256, and sends
version 2 page documents to one explicitly named Solr core in bounded batches. The
SQLite journal stores page identities and checkpoint status, not OCR text. Use a
new journal when the inventory or corpus changes. `--dry-run` verifies inventory
and S3 objects without making Solr requests. Keep S3 credentials in the normal AWS
credential chain; the command does not accept credentials as arguments.

Example invocation (supply the private inventory, journal and core for the
approved environment):

```sh
python3 -m indexer.stream \
  --inventory /private/path/page-inventory.jsonl \
  --journal /private/path/ocr-index.sqlite \
  --corpus public-newspapers \
  --solr-url http://127.0.0.1:8983/solr/ocr
```

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

The plugin calls the companion backend server-side. The endpoint is configured with
`OCR_SEARCH_API_URL`; browsers do not choose or receive that URL. Search API version
2 returns results grouped by item and uses the same query limits and highlight format
as the earlier pilot.

English word variants use Solr's light `KStemFilterFactory` in both the index and
query analyzers (`vote` matches `voting`). Quoted words must appear together in
order, and all query terms must match. Fuzzy matches, wildcards, operators and field
selection are not supported. The existing query limits remain 200 characters and
20 terms.

### `GET /api/ocr/search`

Only the following parameters are accepted. Any other parameter returns HTTP 400.

| Parameter | Contract |
| --- | --- |
| `q` | Required. One to 200 characters and at most 20 terms; words and quoted phrases only. |
| `series` | Optional exact value from `facets.series`; 1–128 valid UTF-8 characters with no control characters. The backend uses a dereferenced Solr term query (`{!term f=series v=$...}`); the value is never concatenated into query text. |
| `year_from`, `year_to` | Optional integers from 1000 to 2100; the start year cannot exceed the end year. |
| `collection` | Optional ArchivesSpace resource path, for example `/repositories/2/resources/1`, using the existing API rules. |
| `item` | Optional item identifier matching `^[a-z0-9-]{1,64}$`. Selects item scope. |
| `sort` | `relevance` (default), `date_asc` or `date_desc`. Date sorts use `issue_date`, put undated items last, and break ties by `item_id`. |
| `page` | Optional page from 1 to 200; defaults to 1. |
| `per_page` | Without `item`, 1–20 items (default 10). With `item`, 1–25 pages (default 10). |

Results are grouped by item. Search results include at most three matching pages per
item, ordered by relevance and then page number, with at most two snippets per page.
Item scope returns one item with its matching pages, paginated by `page` and
`per_page`; date sorts order those pages by page number, while relevance uses score.
Item scope pages include up to five snippets each.

```json
{
  "query": "mascot", "corpus": "example", "sort": "relevance",
  "filters": {"series": null, "year_from": null, "year_to": null, "collection": null, "item": null},
  "page": 1, "per_page": 10, "total_items": 1, "matching_pages": 2,
  "partial": false, "solr_time_ms": 4,
  "facets": {
    "series": [{"value": "Example Weekly", "count": 1}],
    "decade": [{"value": 1920, "count": 1}]
  },
  "results": [{
    "item_id": "demo-a", "title": "Example Weekly, 1927-12-07",
    "series": "Example Weekly", "issue_date": "1927-12-07",
    "collection_id": "/repositories/2/resources/1",
    "aspace_record": "https://example.org/records/demo-a",
    "source_manifest_url": "https://example.org/manifests/demo-a",
    "matching_pages": 2,
    "pages": [{
      "id": "example:demo-a:8", "page_number": 8, "page_label": "Page 8",
      "canvas_id": "https://example.org/canvas/demo-a/8",
      "snippets": [{"html": "A <mark>mascot</mark> appears.", "pages": [], "regions": [], "highlights": []}]
    }]
  }]
}
```

`total_items` counts matching items and `matching_pages` counts matching pages; the
version 1 `total_pages` field is removed. Missing optional indexed fields are
`null`. `facets.series` contains at most 20 values and `facets.decade` contains
decade values; both counts are item counts. Each facet reflects all filters except
its own. Snippet text is escaped, `<mark>` is the only added markup, and highlight
geometry remains in `pages[].snippets[].regions` and `highlights`. `partial`
indicates incomplete search or highlighting work. `source_manifest_url` is
provenance and does not itself select a page viewer.

### `GET /api/ocr/items/{id}`

Returns item metadata and the public page list, ordered by `page_number`, with no
OCR text. The response contains `item_id`, `title`, `series`, `issue_date`,
`collection_id`, `aspace_record`, `source_manifest_url`, `page_count`, `truncated`
and `pages` (`page_number`, `page_label`, `canvas_id`). At most 1,000 pages are
returned; `truncated` is true when more exist. An item without public pages returns
HTTP 404. The item endpoint uses the same upstream timeouts and rate limits as
search.

Bad input returns HTTP 400, an unavailable Solr service returns HTTP 503, and a
rate-limited request returns HTTP 429 with `{"error":"busy"}`. The API keeps
`access:"public"` and corpus filters on every Solr request, a 1,500 ms Solr query
limit, 1,000 ms highlight limit, 500 ms connection timeout, 2,500 ms total
transport timeout and 2 MiB upstream response limit. The shared rate-limit defaults
are eight concurrent requests globally and five requests per second per address
(burst 20); configured trusted plugin hosts default to 50 requests per second.
Deployment-specific limits and trusted-host names are not committed.

Item titles and page lists come from the item endpoint. The zoomable page viewer
uses the single-page catalog manifest only for items in `lib/catalog.json`; other
items show matching excerpts and a link to their finding-aid record.

## Tests

Run from the repository root:

```sh
ruby test/catalog_test.rb
ruby test/search_client_test.rb
ruby test/controller_test.rb
node --test test/highlights.test.cjs
python3 -m unittest discover -s solr -p 'test_*.py'
python3 -m unittest discover -s indexer -p 'test_index.py'
python3 -m unittest discover -s indexer -p 'test_stream.py'
python3 -m unittest discovery.test_discovery
python3 -m unittest discover -s discovery -p 'test_inventory.py'
python3 -m unittest discover -s discovery -p 'test_manifests.py'
```

`indexer/test_index.py` uses the synthetic fixtures by default. To run it against a
local corpus, set `OCR_FIXTURE_DIR` to the directory containing its hOCR files and
IIIF manifests, and `OCR_EVIDENCE_FILE` to the matching evidence JSON file.

`indexer/test_stream.py` is an integration acceptance test. It skips unless
`OCR_010_TEST_S3_ENDPOINT` and `OCR_010_TEST_SOLR_URL` identify the dedicated
loopback LocalStack endpoint on port 14567 and throwaway `ocr-010-test` Solr core
on port 18984. It rejects other hosts, ports and core paths. Build and start the
test Solr from this repository's Dockerfile and configuration; its schema and
`solrconfig.xml` are loaded as-is, with no test-only field additions:

```sh
docker build -t ocr-search-test-solr solr/
docker run --rm --name ocr-010-test-solr \
  -p 127.0.0.1:18984:8983 \
  -e SOLR_HEAP=512m \
  -e SOLR_OPTS=-Dsolr.config.lib.enabled=true \
  ocr-search-test-solr solr-precreate ocr-010-test /opt/ocr-config
```

Start LocalStack in a separate terminal:

```sh
docker run --rm --name ocr-010-test-localstack \
  -p 127.0.0.1:14567:4566 \
  -e SERVICES=s3 \
  -e PERSISTENCE=0 \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=test \
  -e AWS_SECRET_ACCESS_KEY=test \
  localstack/localstack:3.8
```

Run the tests from another terminal:

```sh
OCR_010_TEST_S3_ENDPOINT=http://127.0.0.1:14567 \
OCR_010_TEST_SOLR_URL=http://127.0.0.1:18984/solr/ocr-010-test \
python3 -m unittest indexer.test_stream
```

The tests use only invented hOCR and metadata.

The schema verifier also checks that a query for `voting` matches a synthetic hOCR
page containing `vote`. Run it against a temporary local core built from `solr/`
using an unused loopback port:

```sh
python3 solr/verify_schema.py --solr-url http://127.0.0.1:49152/solr/ocr_next
```

It inserts synthetic documents and removes them after the check. Do not point it at
a shared or production core.

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

`test/browser_v2.mjs` starts a loopback-only UI/API fixture with synthetic responses
and checks the version 2 search views, keyboard focus, accessibility names and
no-JavaScript navigation. It also requires Playwright through `PLAYWRIGHT_MODULE`.
For a manual keyboard and screen-reader pass against the same fake API, follow
[`test/keyboard_accessibility.md`](test/keyboard_accessibility.md).

Changing either `text_ocr` analyzer requires a full rebuild: build a new core with
the updated schema, verify its results, then swap it into service. Existing indexed
documents are not reanalyzed when the schema file changes.

## Licence

This repository is dedicated to the public domain under CC0 1.0 Universal.
