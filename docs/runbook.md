# Local OCR Solr runbook

This runbook starts the Solr service and validates the included indexer fixtures.
The ArchivesSpace plugin and the Yii search API are separate components; this
repository does not start or implement the API. See the root README for the API
contract.

## Requirements

- Docker with the Compose plugin
- Python 3.9 or newer

## Start Solr

From the repository root:

```sh
docker compose -f solr/compose.yml up -d --build
curl --fail 'http://127.0.0.1:18983/solr/ocr/admin/ping?wt=json'
```

## Validate and index the sample fixture

The committed fixture contains four synthetic pages from two invented items and
is intended for regression checks. It is not a complete corpus. Validate it without
writing to Solr:

```sh
python3 indexer/index.py \
  --evidence indexer/fixtures/evidence.json \
  --source indexer/fixtures/hocr \
  --public-item demo-a --public-item demo-b
```

To index the same sample into the local Solr service, repeat the command with
`--execute`. The indexer requires explicit `--public-item` values, verifies source
hashes and page mappings before writing, and synchronizes only the selected corpus
identifier. Use a dedicated Solr core and corpus identifier for other data.

For a separate corpus, supply a reviewed evidence JSON file and a source directory
containing the referenced hOCR and IIIF manifest files. Only include item IDs that
have been explicitly approved for public search. An item's public availability or
technical readability is not, by itself, permission to index or redistribute its
text.

## Verify schema and core replacement

Run schema, grouping, facet, and OCR-format checks only on a disposable Solr instance.
The verifier accepts loopback URLs on a random port (it rejects the usual Solr ports
8983 and 18983) and removes its synthetic documents when it finishes. It compares the
four committed synthetic hOCR pages with their MiniOCR conversions and requires
identical highlights.
The reported byte totals are the serialized OCR field payloads. For a physical index
size comparison, load the same pages into two empty cores and read each core's
`index.sizeInBytes` from the CoreAdmin `STATUS` response.

`indexer/miniocr.py` exposes `hocr_to_miniocr(text)` as a standalone helper. The local
folder indexer continues to submit hOCR; the helper is available for an indexer caller
to opt into MiniOCR separately.

From the repository root, build and start a no-volume Solr container with an automatic
loopback port:

```sh
docker build -t ocr-search-solr-verify ./solr
docker run --rm -d --name ocr-search-solr-verify \
  -p 127.0.0.1::8983 \
  -e SOLR_HEAP=512m \
  -e SOLR_OPTS=-Dsolr.config.lib.enabled=true \
  ocr-search-solr-verify solr-precreate ocr /opt/ocr-config
docker port ocr-search-solr-verify 8983/tcp
```

Set `OCR_TEST_PORT` to the port printed by `docker port`. The verifier prints group
counts, facet counts, legacy-document compatibility, highlight comparisons, and the
synthetic OCR byte totals:

```sh
OCR_TEST_PORT=49152
python3 solr/verify_schema.py \
  --solr-url "http://127.0.0.1:${OCR_TEST_PORT}/solr/ocr"
```

Create a candidate core from the same configuration, verify it, then swap the core
names. `ocr_next` keeps the previous core available for rollback until it is safe to
remove:

```sh
docker exec ocr-search-solr-verify bin/solr create -c ocr_next -d /opt/ocr-config
python3 solr/verify_schema.py \
  --solr-url "http://127.0.0.1:${OCR_TEST_PORT}/solr/ocr_next"

curl --fail --silent --show-error --get \
  --data-urlencode 'action=SWAP' \
  --data-urlencode 'core=ocr' \
  --data-urlencode 'other=ocr_next' \
  --data-urlencode 'wt=json' \
  "http://127.0.0.1:${OCR_TEST_PORT}/solr/admin/cores"
curl --fail "http://127.0.0.1:${OCR_TEST_PORT}/solr/ocr/admin/ping?wt=json"
python3 solr/verify_schema.py \
  --solr-url "http://127.0.0.1:${OCR_TEST_PORT}/solr/ocr"
```

CoreAdmin switches the loaded core names in place. Repeat the same `SWAP` request to
roll back while both cores are available. Keep the old core until the replacement has
been verified and the rollback window has closed.
Stop the throwaway container when finished:

```sh
docker stop ocr-search-solr-verify
```

## Stop Solr

```sh
docker compose -f solr/compose.yml stop
```

The integration test needs both a live API endpoint and Solr. It is skipped unless
`OCR_SEARCH_BASE_URL` and `OCR_EVIDENCE_FILE` point to a live endpoint and matching
evidence JSON. For a local corpus, also set `OCR_FIXTURE_DIR` to its hOCR and
manifest directory; set `OCR_SEARCH_SOLR_URL` if Solr is not at its default local
address. The integration test writes temporary documents to the named regression
corpus and removes them afterward, so use a dedicated test index.
