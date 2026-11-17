"""Command-line entry for importing publisher data without fixture fallback."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import ijson

from .ingest import (
    FISCAL_YEARS, IngestError, connect_store,
    ingest_fiscal_year, ingest_page, save_state,
)


def main():
    parser = argparse.ArgumentParser(prog="bidbox")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Import real OCDS JSON, JSONL or gzip")
    ingest.add_argument("source", nargs="?", help="Exact publisher URL or local OCDS file")
    years = ingest.add_mutually_exclusive_group()
    years.add_argument("--fy", choices=FISCAL_YEARS)
    years.add_argument("--all-fy", action="store_true")
    ingest.add_argument("--source-url", help="Exact publisher page URL for a previously downloaded local file")
    ingest.add_argument("--refresh", action="store_true", help="Re-download completed fiscal years")
    measure = commands.add_parser("measure", help="Measure actual downloaded live-data field coverage")
    measure.add_argument("--output", type=Path)
    connector = commands.add_parser("connector", help="Scaffold or test an institution connector")
    connector_sub = connector.add_subparsers(dest="connector_command", required=True)
    connector_new = connector_sub.add_parser("new", help="Scaffold connectors/<name>/ from a template")
    connector_new.add_argument("name", help="lower_snake_case connector name")
    connector_new.add_argument("--kind", required=True,
                               choices=("data_source", "messaging", "payments", "records", "registry", "file_drop"))
    connector_test = connector_sub.add_parser("test", help="Run the health check plus one sample read")
    connector_test.add_argument("name", help="Connector folder name")
    args = parser.parse_args()
    if args.command == "connector":
        from .connectors_cli import connector_new, connector_test_command

        if args.connector_command == "new":
            connector_new(args.name, args.kind)
        else:
            connector_test_command(args.name)
        return
    if args.command == "measure":
        from .data import measure_coverage

        with connect_store(read_only=True) as connection:
            result = measure_coverage(connection)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        print(text)
        return
    if not (args.source or args.fy or args.all_fy):
        parser.error("ingest needs a URL/file, --fy or --all-fy")
    if args.source and args.all_fy:
        parser.error("a single source file cannot represent --all-fy")
    remote = bool(args.source and args.source.startswith(("https://", "http://")))
    if args.source_url and (not args.source or remote):
        parser.error("--source-url is only for a local downloaded file")
    if args.source_url and (urlsplit(args.source_url).scheme not in ("http", "https") or urlsplit(args.source_url).username):
        parser.error("--source-url must be an HTTP(S) URL without credentials")
    errors = []
    with connect_store() as connection:
        if args.source and not remote:
            path = Path(args.source)
            url = args.source_url or str(path.resolve())
            try:
                result = ingest_page(connection, path, url, args.fy, checkpoint_source=url if args.fy else None)
                print(json.dumps({"source_url": url, **result}), flush=True)
            except (ValueError, OSError, EOFError, ijson.JSONError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if args.fy:
                    save_state(connection, args.fy, url, url, "failed", False, error=errors[-1])
        else:
            try:
                with httpx.Client(headers={"User-Agent": "BidBox/0.1 (public OCDS importer; sequential GET)", "Accept": "application/json"},
                                  timeout=httpx.Timeout(60, connect=15), follow_redirects=True) as client:
                    if args.source:
                        parsed = urlsplit(args.source)
                        if parsed.scheme not in ("http", "https") or parsed.username:
                            parser.error("source must be a local file or HTTP(S) URL without credentials")
                        print(json.dumps(ingest_fiscal_year(connection, args.fy, client, args.refresh, source_url=args.source)), flush=True)
                    else:
                        for index, fiscal_year in enumerate(FISCAL_YEARS if args.all_fy else [args.fy]):
                            if index:
                                time.sleep(0.5)
                            try:
                                print(json.dumps(ingest_fiscal_year(connection, fiscal_year, client, args.refresh)), flush=True)
                            except IngestError as exc:
                                errors.append(str(exc))
                                print(str(exc), file=sys.stderr, flush=True)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        rows = connection.execute("SELECT origin, count(*) FROM procurement_records GROUP BY origin ORDER BY origin").fetchall()
        print(json.dumps({"records_by_origin": dict(rows), "errors": errors, "sandbox_fallback_used": False}), flush=True)
    if errors:
        raise SystemExit(1)
