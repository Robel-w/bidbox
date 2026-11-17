"""ocds_source adapter: reads the local OCDS ingest store (live publisher rows or sandbox fixtures)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bidbox.connectors.base import Connector, HealthResult


def build(manifest, folder: Path) -> Connector:
    return OcdsSource(manifest, folder)


class OcdsSource(Connector):
    def health(self) -> HealthResult:
        from bidbox.data import data_status

        status = data_status()
        counts = status.get("counts", {})
        live = int(counts.get("live", 0) or 0)
        sandbox = int(counts.get("sandbox", 0) or 0)
        if live:
            return HealthResult(status="live", detail=f"{live} ingested publisher records; data as of {status.get('data_as_of') or 'unknown'}")
        if sandbox:
            return HealthResult(status="sandbox", detail=f"{sandbox} sandbox fixture records; no live ingest yet")
        return HealthResult(status="not_configured", detail="no OCDS store on disk; run `bidbox ingest` first")

    def read(self, action: str, params: dict[str, Any]) -> Any:
        from bidbox.data import get_process, search_processes

        if action == "search_awards":
            result = search_processes(
                str(params.get("query") or ""), int(params.get("limit") or 20),
                sandbox=False, awards_only=True,
            )
            return result
        if action == "get_award":
            ocid = str(params.get("ocid") or "").strip()
            if not ocid:
                from bidbox.connectors.errors import ConnectorError
                raise ConnectorError("invalid_request", "get_award requires an ocid")
            process = get_process(ocid, origin="live")
            if process is None:
                process = get_process(ocid, origin="sandbox")
            if process is None:
                from bidbox.connectors.errors import ConnectorError
                raise ConnectorError("invalid_request", f"ocid {ocid!r} not found in the ingest store; nothing is guessed")
            record = dict(process)
            raw = record.pop("record", None)
            return {"ocid": ocid, "summary": {k: record.get(k) for k in
                    ("title", "buyer_name", "award_count", "contract_count", "tender_amount",
                     "currency", "procurement_method", "release_date")},
                    "fiscal_years": record.get("fiscal_years", []), "record": raw,
                    "source_url": record.get("source_url"), "origin": record.get("origin")}
        from bidbox.connectors.errors import ConnectorError
        raise ConnectorError("invalid_request", f"unknown read action {action!r}")
