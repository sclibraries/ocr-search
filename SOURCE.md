# Source and import scope

Imported on 2026-10-08 from coordination repository commit
`c12d1e0157702796808122e1af7cc5858b5ed801`.

## Included

- The `ocr_search` ArchivesSpace plugin source, assets, and tests at the repository root.
- The OCR indexer and its tests under `indexer/`.
- The OCR discovery package under `discovery/`.
- The OCR Solr Dockerfile, Compose file, and Solr configuration under `solr/`.
- Adapted user-facing runbooks under `docs/`.
- A hand-written synthetic corpus with two invented items, two hOCR pages per item,
  matching minimal IIIF manifests, evidence checksums, and reference queries.

## Not included

- The Yii search API implementation, which remains in the coordination repository.
- Planning tickets, status and decision records, operational evidence, pilot logs,
  SQL samples, and broader discovery inventories.
- The full local hOCR corpora, real newspaper pages, unrelated fixtures, and internal
  deployment notes.

The discovery command was made repository-independent and its deployment-specific
shell-command output was removed. Tests and runbooks now use the standalone package
paths. The repository is dedicated to the public domain under CC0 1.0 Universal; see
`LICENSE`.
