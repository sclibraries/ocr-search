# AGENTS.md

Guidance for AI coding agents working in this repository. `CLAUDE.md` imports this file; edit
here only.

## What this is

- The `ocr_search` ArchivesSpace PUI plugin (repository root: `plugin_init.rb`, `public/`, `lib/`).
- `indexer/`: builds Solr page documents from local hOCR evidence or streams exact
  S3 object versions from a page inventory into Solr.
- `discovery/`: bounded relationship export and page-inventory tooling; S3 output requires an explicit destination.
- `solr/`: Solr configuration and a Compose file for a local OCR core.

## Test

From the repository root:

```sh
ruby test/catalog_test.rb
ruby test/search_client_test.rb
node --test test/highlights.test.cjs
python3 -m unittest discover -s indexer -p 'test_index.py'
python3 -m unittest discover -s indexer -p 'test_stream.py'
python3 -m unittest discovery.test_discovery
python3 -m unittest discover -s discovery -p 'test_inventory.py'
python3 -m unittest discover -s discovery -p 'test_manifests.py'
```

`indexer/test_http.py` and the browser test need a running search API, Solr and ArchivesSpace;
they skip without them. See the README for the environment variables.

## Interfaces other systems depend on

Do not change these unless your task explicitly covers it, and report any change in your handoff.

| Interface | Defined in | Who depends on it |
|---|---|---|
| Search API: `GET /api/ocr/search` parameters and response shape | README "Search API contract" | The backend that implements the endpoint lives in another repository; a change here needs a matching change there |
| Solr field names and types | `solr/` | The search API backend queries these fields |
| Evidence file format, checksum, canvas and path checks | `indexer/index.py`, `indexer/fixtures/evidence.json` | Ingest tooling that will produce evidence files |
| `OCR_SEARCH_API_URL` (server-side, read by the plugin) | `public/controllers/ocr_search_controller.rb` | Host configuration |
| The viewer's `digital-viewer:open` event (`detail.viewer`) | The `digital_viewer` plugin | Page highlights here; do not reimplement the viewer |

Canvas identifiers in results must match the manifest's canvases, so the viewer opens the right page.

## Public repository rules

This repository is public and dedicated to the public domain under CC0 1.0.

- **Never commit real OCR text or page images.** Fixtures are synthetic (`demo-a`, `demo-b`).
  Point tests at a local real corpus with `OCR_FIXTURE_DIR` and `OCR_EVIDENCE_FILE` instead.
- No tickets, evidence logs, staff names, internal hostnames, server paths or credentials.
- Discovery SQL output is read-only and is never executed by the public tool. Inventory
  reconciliation uses `HeadObject`; streaming indexing uses `GetObject` only for the inventory's
  exact recorded version. Inventory output can be written only to an explicitly supplied local
  path or `s3://` URI. Never use production systems while developing or testing this repository.

## Working rules

- Work on a branch named for your task. Do not push or merge to `main`; a reviewer does that.
- Keep to the files your task names. If the task is wrong or incomplete, stop and report.
- End with: branch and commit, every command run with its result, anything you changed beyond the
  task and why, and open questions.
