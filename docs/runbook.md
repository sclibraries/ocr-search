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

## Verify schema and rebuild a core

Run schema, grouping, facet, and OCR-format checks only on a disposable Solr instance.
The verifier accepts loopback URLs on a random port (it rejects the usual Solr ports
8983 and 18983) and removes its synthetic documents when it finishes. It compares the
four committed synthetic hOCR pages with their MiniOCR conversions and requires
identical highlights. The reported byte totals are serialized OCR field payloads.
For a physical index size comparison, load the same pages into two empty cores and read
each core's `index.sizeInBytes` from the CoreAdmin `STATUS` response.

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

### Rebuild, verify, swap, and check a restart

This procedure is for standalone Solr cores, not SolrCloud. Keep the current live core
available until the new core has passed search checks and the rollback window has
closed. Use the same reviewed evidence, explicit public-item allowlist, and corpus ID
as the live index. The checked-in fixture command below is for a disposable rehearsal;
do not run it against a shared index.

For a scratch rehearsal, start a separately named Compose project. It gets its own
project-scoped volume and loopback port. To rebuild an existing service instead, set
`SOLR_CONTAINER` and `SOLR` to that service and preserve its volume.

```sh
OCR_SOLR_HOST_PORT=18985 docker compose -p ocr-rebuild-check -f solr/compose.yml up -d --build
```

Set these values for the Solr instance being rebuilt. `CANDIDATE_CORE` must be a name
that is not currently loaded or declared by any `core.properties` file under Solr's
data directory.

```sh
SOLR_CONTAINER="$(docker compose -p ocr-rebuild-check -f solr/compose.yml ps -q solr)"
SOLR=http://127.0.0.1:18985/solr
LIVE_CORE=ocr
CANDIDATE_CORE=ocr_build
CORPUS=mascot-pilot
EVIDENCE=/path/to/reviewed/evidence.json
SOURCE=/path/to/reviewed/hocr
PUBLIC_ITEMS=(demo-a demo-b)
```

Check the current core mapping and record the expected page count before rebuilding:

```sh
curl --fail --silent --show-error --get "$SOLR/admin/cores" \
  --data-urlencode 'action=STATUS' --data-urlencode 'wt=json'
curl --fail --silent --show-error --get "$SOLR/$LIVE_CORE/select" \
  --data-urlencode "q=corpus_id:\"$CORPUS\"" \
  --data-urlencode 'fq=access:public' --data-urlencode 'rows=0' --data-urlencode 'wt=json'
```

Create a candidate core using the OCR config, then index the reviewed input into that
core. For the committed synthetic fixtures, set `EVIDENCE=indexer/fixtures/evidence.json`,
`SOURCE=indexer/fixtures/hocr`, and `PUBLIC_ITEMS=(demo-a demo-b)` first.

```sh
docker exec "$SOLR_CONTAINER" bin/solr create -c "$CANDIDATE_CORE" -d /opt/ocr-config
PUBLIC_ITEM_ARGS=()
for item in "${PUBLIC_ITEMS[@]}"; do PUBLIC_ITEM_ARGS+=(--public-item "$item"); done
python3 indexer/index.py \
  --evidence "$EVIDENCE" \
  --source "$SOURCE" \
  --corpus "$CORPUS" \
  --solr "$SOLR/$CANDIDATE_CORE" \
  --execute "${PUBLIC_ITEM_ARGS[@]}"
```

Before swapping, confirm the candidate is healthy, its public count matches the
expected count, and a known OCR term returns the expected page:

```sh
curl --fail --silent --show-error "$SOLR/$CANDIDATE_CORE/admin/ping?wt=json"
curl --fail --silent --show-error --get "$SOLR/$CANDIDATE_CORE/select" \
  --data-urlencode "q=corpus_id:\"$CORPUS\"" \
  --data-urlencode 'fq=access:public' --data-urlencode 'rows=0' --data-urlencode 'wt=json'
curl --fail --silent --show-error --get "$SOLR/$CANDIDATE_CORE/select" \
  --data-urlencode 'q=ocr:copper' --data-urlencode 'fq=access:public' \
  --data-urlencode 'rows=10' --data-urlencode 'wt=json'
```

Replace `ocr:copper` with a known term from the reviewed corpus. Once the candidate
checks pass, atomically swap the core names. CoreAdmin keeps the previous live core
available under the candidate name. The startup entrypoint scans all `core.properties`
files before precreate, so a live `ocr` definition in the candidate's directory will
not cause an empty `/var/solr/data/ocr` core to be created on restart.

```sh
curl --fail --silent --show-error --get "$SOLR/admin/cores" \
  --data-urlencode 'action=SWAP' \
  --data-urlencode "core=$LIVE_CORE" \
  --data-urlencode "other=$CANDIDATE_CORE" \
  --data-urlencode 'wt=json'
curl --fail --silent --show-error --get "$SOLR/admin/cores" \
  --data-urlencode 'action=STATUS' --data-urlencode 'wt=json'
curl --fail --silent --show-error "$SOLR/$LIVE_CORE/admin/ping?wt=json"
```

Restart the container twice. After each restart, wait for Solr to answer, then repeat
the count and known-term queries against `$LIVE_CORE`. The live core's count and
known-term result must remain the values verified before the swap. Check container
logs for `OCR Solr startup error`; any such line means the live core needs attention.
The live-core ping can return HTTP 503 while Solr is still loading its cores, so poll it
before running the search checks:

```sh
wait_for_live_core() {
  local attempt
  for attempt in {1..60}; do
    if curl --fail --silent "$SOLR/$LIVE_CORE/admin/ping?wt=json" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for live core $LIVE_CORE" >&2
  return 1
}

docker restart "$SOLR_CONTAINER"
wait_for_live_core
curl --fail --silent --show-error "$SOLR/$LIVE_CORE/admin/ping?wt=json"
curl --fail --silent --show-error --get "$SOLR/$LIVE_CORE/select" \
  --data-urlencode "q=corpus_id:\"$CORPUS\"" \
  --data-urlencode 'fq=access:public' --data-urlencode 'rows=0' --data-urlencode 'wt=json'
curl --fail --silent --show-error --get "$SOLR/$LIVE_CORE/select" \
  --data-urlencode 'q=ocr:copper' --data-urlencode 'fq=access:public' \
  --data-urlencode 'rows=10' --data-urlencode 'wt=json'
docker logs "$SOLR_CONTAINER" --since 2m 2>&1 | grep 'OCR Solr startup error' || true

docker restart "$SOLR_CONTAINER"
wait_for_live_core
curl --fail --silent --show-error "$SOLR/$LIVE_CORE/admin/ping?wt=json"
curl --fail --silent --show-error --get "$SOLR/$LIVE_CORE/select" \
  --data-urlencode "q=corpus_id:\"$CORPUS\"" \
  --data-urlencode 'fq=access:public' --data-urlencode 'rows=0' --data-urlencode 'wt=json'
curl --fail --silent --show-error --get "$SOLR/$LIVE_CORE/select" \
  --data-urlencode 'q=ocr:copper' --data-urlencode 'fq=access:public' \
  --data-urlencode 'rows=10' --data-urlencode 'wt=json'
docker logs "$SOLR_CONTAINER" --since 2m 2>&1 | grep 'OCR Solr startup error' || true
```

### Rollback and cleanup

After a swap, the old live core is registered as `$CANDIDATE_CORE`. Before relying on
it for rollback, query that core directly and confirm `numFound` is nonzero for the
corpus being restored. Also run a known-term query and confirm it returns the expected
old page. A core with no matching documents or an unreadable index is not a usable
rollback copy; stop and investigate instead of swapping to it.

```sh
curl --fail --silent --show-error "$SOLR/$CANDIDATE_CORE/admin/ping?wt=json"
curl --fail --silent --show-error --get "$SOLR/$CANDIDATE_CORE/select" \
  --data-urlencode "q=corpus_id:\"$CORPUS\"" \
  --data-urlencode 'fq=access:public' --data-urlencode 'rows=0' --data-urlencode 'wt=json'
curl --fail --silent --show-error --get "$SOLR/$CANDIDATE_CORE/select" \
  --data-urlencode 'q=ocr:copper' --data-urlencode 'fq=access:public' \
  --data-urlencode 'rows=10' --data-urlencode 'wt=json'
```

Only after confirming that the candidate core has the old index, roll back by repeating
the same `SWAP` with `core=ocr` and `other=$CANDIDATE_CORE`. Ping and search the restored
live core, then restart and check it again. Keep the retired core through the agreed
rollback window. After that window, unload the old core by its current CoreAdmin name
with `deleteInstanceDir=true`, `deleteDataDir=true`, and `deleteIndex=true`; check the
STATUS response first because its instance directory can differ from its current name
after a swap. These CoreAdmin operations are for standalone cores. See the
[Solr 9.8 CoreAdmin guide](https://solr.apache.org/guide/solr/9_8/configuration-guide/coreadmin-api.html)
for the `STATUS`, `SWAP`, and `UNLOAD` actions.

```sh
curl --fail --silent --show-error --get "$SOLR/admin/cores" \
  --data-urlencode 'action=UNLOAD' \
  --data-urlencode "core=$CANDIDATE_CORE" \
  --data-urlencode 'deleteInstanceDir=true' \
  --data-urlencode 'deleteDataDir=true' \
  --data-urlencode 'deleteIndex=true' \
  --data-urlencode 'wt=json'
```

For a disposable Compose rehearsal, remove only that rehearsal after its checks finish:

```sh
OCR_SOLR_HOST_PORT=18985 docker compose -p ocr-rebuild-check -f solr/compose.yml up -d --build
docker compose -p ocr-rebuild-check -f solr/compose.yml down -v
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
