# OCR discovery tools

The `discovery` package imports a bounded JSONL metadata inventory, records a local
checkpoint, and can inspect local files or a bounded set of S3 objects. It does not
change source records or grant permission to publish searchable text. Reports can
contain item and file metadata; store each run in an appropriate local location.

## Requirements

- Python 3.9 or newer
- `boto3` only when using `--read-s3` or `discovery inventory`

From the repository root, view the commands and run the tests:

```sh
python3 -m discovery --help
python3 -m unittest discovery.test_discovery
```

## Generate a bounded metadata query

`export-sql` prints a SELECT query and does not connect to a database. Provide the
collection and media-use identifiers reviewed for the metadata source:

```sh
python3 -m discovery export-sql \
  --collection-id 12345 --hocr-media-use-id 67890 --limit 100
```

Replace the example IDs with reviewed values. Each query is capped at 1000 rows;
the coordination repository's wrapper defaults to 100 and requires
`--allow-large-batches` above that after query-plan review. `--explain` prints an
`EXPLAIN FORMAT=JSON` query. Review it in the approved operator session before
raising the batch size. Query output is candidates for further inspection, not a
public-index allowlist.

The `manifests` command fetches each exported item's Compass manifest at
`/node/{item}/manifest`, using HTTPS and an operator-supplied base URL. It rate
limits requests, privately saves the response and SHA-256, and resumes from its
local index. Invoking this command makes network requests; use it only in the
approved operator workflow. Tests supply fake responses and do not access a
network:

```sh
python3 -m discovery manifests \
  --export-jsonl ./relationships.jsonl \
  --output-dir /path/to/private/compass-manifests \
  --base-url https://<reviewed-origin> \
  --requests-per-second 1
```

## Build a page inventory

The inventory command joins SQL-export rows with optional evidence, a converted
manifest when available or the Compass manifest cache, and read-only S3 `HeadObject`
metadata. It requires a reviewed access mapping file that maps Compass term IDs
and/or names to `public` or `restricted`, and sets `published_without_terms` to one
of those values. Unmapped terms remain `unknown`; without the file the command
refuses to run. Evidence SHA-256 values are copied when present and remain empty
when absent, for OCR-010 to compute while streaming. S3 size, version ID and ETag
are recorded. Keep operator data outside this public repository:

```sh
python3 -m discovery inventory \
  --export-jsonl ./relationships.jsonl \
  --compass-manifest-dir /path/to/private/compass-manifests \
  --access-mapping-file /path/to/private/reviewed-access-mapping.json \
  --source-bucket "$OCR_SOURCE_BUCKET" \
  --output-location "$OCR_INVENTORY_OUTPUT"
```

Add `--evidence-file PATH` when checksum evidence exists. Add `--manifest-dir PATH`
when a converted IIIF manifest is available; it takes precedence over the Compass
manifest for its matching item.

`OCR_INVENTORY_OUTPUT` may be a private local path or an explicitly selected
`s3://bucket/key` URI. S3 output is written only when that URI is supplied. The
command writes a JSONL page inventory and a reconciliation JSON sidecar with counts
per collection, deep and multiple-parent item IDs, unresolved ancestry, and sample
canvas IDs. The SQL ancestry walk stops at 32 levels and records any row that reaches
that limit; resolve those items before treating ancestry as complete. It does not
treat an open-use term, publication state alone, or failed S3 metadata lookup as
public-index approval.

## Inspect an inventory

Save one JSON object per line in an inventory file, then import the metadata without
reading any content:

```sh
python3 -m discovery inspect \
  --inventory ./sample.jsonl \
  --output ./discovery-run
```

Each run is limited to 100 files. The output directory contains a SQLite checkpoint
and JSON/CSV reports. Reuse the same output directory to resume an unchanged sample;
use a separate directory for a different sample.

To validate local hOCR files, include a `local_file` field in each inventory row
and pass the directory containing those files:

```sh
python3 -m discovery inspect \
  --inventory ./sample.jsonl \
  --output ./discovery-run \
  --local-source ./hocr-files
```

S3 inspection is opt-in. It uses the normal AWS credential provider chain and
performs bounded ranged GET requests. Install `boto3` and supply a locally configured
profile if required:

```sh
python3 -m discovery inspect \
  --inventory ./sample.jsonl \
  --output ./discovery-run \
  --read-s3 --profile PROFILE_NAME
```

The defaults cap each run at 100 files and 100 requests, 50 MB total transfer,
2 MB per file, one worker, and five minutes. Smaller limits can be supplied with
`--max-files`, `--max-requests`, `--max-bytes`, `--max-file-bytes`, and
`--max-seconds`. Read errors remain visible in reports; they are not treated as
proof that a source object is absent.

## Interpret results

The reports capture validation status, checksums, page dimensions, word counts,
metadata issues, and resource use. Source text is not written into the reports.
`public_index_approved` remains false: file readability, a public record, or an
observed access term does not establish permission to publish the text. Review
access, ownership, item-to-page mappings, and text quality separately before
proposing an index allowlist.
