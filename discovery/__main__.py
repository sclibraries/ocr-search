"""Usage: python3 -m discovery --help"""
import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
from .catalog import Catalog
from .export import sample_sql
from .inventory import build_inventory, read_export, write_inventory
from .runner import audit
from .sources import LocalSource, S3Source


def reports(catalog, output, run):
    rows = catalog.report()
    (output / 'report.json').write_text(json.dumps({'run': run, 'rows': rows}, indent=2)+'\n')
    fields = ['file_id', 'filename', 'uri', 'status', 'sha256', 'bytes', 'word_count',
              'page_ids', 'item_ids', 'canvas_id', 'aspace_record', 'issues', 'error', 'public_index_approved',
              'skipped_blank_words']
    with (output / 'report.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            safe = dict(row, issues=';'.join(row['issues']))
            for key, value in safe.items():
                if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                    safe[key] = "'" + value
            writer.writerow(safe)
    summary = dict(run, inventory_files=len(rows), statuses=dict(Counter(row['status'] for row in rows)),
                   issues=dict(Counter(issue for row in rows for issue in row['issues'])),
                   public_index_approved=0, coverage='bounded supplied inventory; not a full collection audit')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description='Bounded OCR discovery, relationship export, and page-inventory reconciliation.')
    commands = parser.add_subparsers(dest='command', required=True)
    sql = commands.add_parser('export-sql', help='Print a bounded SELECT; does not connect to a database')
    sql.add_argument('--collection-id', type=int, required=True)
    sql.add_argument('--hocr-media-use-id', type=int, required=True)
    sql.add_argument('--after-file-id', type=int, default=0)
    sql.add_argument('--after-page-id', type=int)
    sql.add_argument('--after-item-id', type=int)
    sql.add_argument('--limit', type=int, default=100)
    inventory = commands.add_parser('inventory', help='Join a SQL export to S3 metadata and IIIF manifests')
    inventory.add_argument('--export-jsonl', required=True, type=Path)
    inventory.add_argument('--evidence-file', required=True, type=Path)
    inventory.add_argument('--manifest-dir', required=True, type=Path)
    inventory.add_argument('--source-bucket', default=os.environ.get('OCR_SOURCE_BUCKET'))
    inventory.add_argument('--output-location', default=os.environ.get('OCR_INVENTORY_OUTPUT'))
    inventory.add_argument('--profile', help='Named AWS credential profile; never pass keys on the command line')
    inventory.add_argument('--region', default=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION'))
    inventory.add_argument('--canvas-sample-size', type=int, default=10)
    run = commands.add_parser('inspect', help='Import a JSONL inventory and optionally inspect contents')
    run.add_argument('--inventory', required=True, type=Path)
    run.add_argument('--output', required=True, type=Path)
    sources = run.add_mutually_exclusive_group()
    sources.add_argument('--local-source', type=Path)
    sources.add_argument('--read-s3', action='store_true', help='Explicitly enable bounded GETs; requires boto3 and configured read credentials')
    run.add_argument('--retry-errors', action='store_true', help='Explicitly retry previously checkpointed read failures')
    run.add_argument('--profile', help='Named AWS credential profile; never pass keys on the command line')
    run.add_argument('--max-files', type=int, default=100)
    run.add_argument('--max-bytes', type=int, default=50_000_000)
    run.add_argument('--max-file-bytes', type=int, default=2_000_000)
    run.add_argument('--max-seconds', type=float, default=300)
    run.add_argument('--max-requests', type=int, default=100)
    run.add_argument('--delay', type=float, default=0.5)
    args = parser.parse_args()
    if args.command == 'export-sql':
        try:
            query = sample_sql(args.collection_id, args.hocr_media_use_id,
                               args.after_file_id, args.limit,
                               args.after_page_id, args.after_item_id)
        except ValueError as error:
            parser.error(str(error))
        print(query)
        return
    if args.command == 'inventory':
        if not args.source_bucket:
            parser.error('set --source-bucket or OCR_SOURCE_BUCKET')
        if not args.output_location:
            parser.error('set --output-location or OCR_INVENTORY_OUTPUT')
        try:
            rows = read_export(args.export_jsonl)
            evidence = json.loads(args.evidence_file.read_text())
            import boto3
            from botocore.config import Config
            session = boto3.Session(profile_name=args.profile, region_name=args.region)
            config = Config(connect_timeout=5, read_timeout=5,
                            retries={'total_max_attempts': 1})
            s3 = session.client('s3', config=config)
            result = build_inventory(rows, evidence, args.manifest_dir, s3,
                                     source_bucket=args.source_bucket,
                                     sample_size=args.canvas_sample_size)
            locations = write_inventory(result, args.output_location, s3)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            parser.error(str(error))
        print(json.dumps({'written': locations, 'reconciliation': result['reconciliation']}, indent=2))
        return
    if not 1 <= args.max_files <= 100:
        parser.error('initial discovery sample is limited to 1–100 files')
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    catalog = Catalog(args.output / 'checkpoint.sqlite')
    try:
        catalog.ingest(args.inventory, limit=100)
        if catalog.count() > 100:
            parser.error('use a separate output directory for each sample of at most 100 files')
        source = None
        if args.local_source:
            source = LocalSource(args.local_source)
        elif args.read_s3:
            import boto3
            from botocore.config import Config
            session = boto3.Session(profile_name=args.profile, region_name='us-east-1')
            config = Config(connect_timeout=5, read_timeout=5, retries={'total_max_attempts': 1})
            source = S3Source(session.client('s3', config=config))
        result = {'processed': 0, 'stop_reason': 'metadata_only'}
        if source:
            if args.retry_errors:
                catalog.retry_errors()
            result = audit(catalog, source, args.max_files, args.max_bytes, args.max_file_bytes,
                           args.max_seconds, args.max_requests, args.delay)
        reports(catalog, args.output, result)
    finally:
        catalog.close()


if __name__ == '__main__':
    main()
