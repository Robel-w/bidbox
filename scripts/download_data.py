"""Fetch real OCDS sample data with offline fallback."""

import json
from pathlib import Path

import httpx

# Public OCDS sample URL
SAMPLE_URL = "https://raw.githubusercontent.com/open-contracting/sample-data/master/fictional-example/1.1/ocds-213czf-000-00001.json"


def download_sample(url: str = SAMPLE_URL, out_dir: str = "data/samples") -> bool:
    """Download real OCDS sample data, falling back gracefully if offline."""
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "kenya_sample.json"

    print(f"Attempting to download OCDS sample from {url}...")
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Saved real OCDS sample to {target_file}")
        return True
    except (httpx.RequestError, httpx.HTTPStatusError, OSError, ValueError) as exc:
        print(
            f"Notice: External OCDS download failed ({exc}). "
            "Offline mode active: BidBox relies on deterministic fixtures in data/fixtures/."
        )
        return False


def main() -> None:
    """Entry point for sample downloader."""
    download_sample()


if __name__ == "__main__":
    main()
