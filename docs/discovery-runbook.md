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
collection identifier and cursor appropriate for the metadata source you have
reviewed:

```sh
python3 -m discovery export-sql \
  --collection-id 12345 --hocr-media-use-id 67890 --after-file-id 0 --limit 100
```

Replace the example IDs with reviewed values. The limit is capped at 100 rows.
Review the query and its target schema before running it in another system. Its
results are candidates for further inspection, not a public-index allowlist.
For later batches, resume from the last JSONL row using all three cursor fields:
`--after-file-id`, `--after-page-id`, and `--after-item-id`.

## Build a page inventory

The inventory command joins versioned SQL-export rows with a supplied evidence file,
local converted IIIF manifests, and read-only S3 `HeadObject` metadata. The source
bucket and output location are explicit settings; no bucket is embedded in the
tool. Use a local synthetic fixture for tests, and keep operator data outside this
public repository:

```sh
python3 -m discovery inventory \
  --export-jsonl ./relationships.jsonl \
  --evidence-file ./evidence.json \
  --manifest-dir ./manifests \
  --source-bucket "$OCR_SOURCE_BUCKET" \
  --output-location "$OCR_INVENTORY_OUTPUT"
```

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
