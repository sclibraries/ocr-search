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
