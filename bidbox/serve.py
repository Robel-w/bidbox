"""Validated single-worker production entry point."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import uvicorn

from agent.llm import check_model_available
from bidbox.connectors.registry import load_registry
from bidbox.ingest import connect_store, db_path, ingest_page


async def startup_check():
    required = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "BIDBOX_ACCESS_PASSWORD", "APPROVAL_SIGNING_KEY")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Missing host secrets/settings: " + ", ".join(missing))
    if len(os.environ["APPROVAL_SIGNING_KEY"]) < 32:
        raise RuntimeError("APPROVAL_SIGNING_KEY requires at least 32 characters")
    await check_model_available()
    first_boot = not db_path().exists()
    if first_boot and os.getenv("OCDS_SOURCE_URL"):
        subprocess.run([sys.executable, "-m", "bidbox", "ingest", os.environ["OCDS_SOURCE_URL"]], check=True)
    else:
        with connect_store() as connection:
            if first_boot and os.getenv("BIDBOX_SANDBOX") == "1":
                fixture = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "releases.json"
                ingest_page(connection, fixture, str(fixture))
    registry = load_registry()
    if registry.refused():
        print("Refused non-conforming connectors: " + ", ".join(registry.refused()), file=sys.stderr)


def main():
    asyncio.run(startup_check())
    uvicorn.run("bidbox.web:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1)


if __name__ == "__main__":
    main()
