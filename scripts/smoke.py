"""Exercise a deployment without inventing evidence or human approval."""

import argparse
import os
import time
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    url = urlsplit(base)
    if url.username or url.password or (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1"))):
        parser.error("Use HTTPS outside localhost and do not put credentials in the URL")
    required = ("SMOKE_QUESTION", "SMOKE_APPROVER_NAME", "SMOKE_APPROVER_ROLE", "SMOKE_REASON")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        parser.error("Human-supplied settings required: " + ", ".join(missing))
    password = os.getenv("BIDBOX_ACCESS_PASSWORD")
    with httpx.Client(base_url=base, auth=("bidbox", password) if password else None, timeout=30) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()
        assert get("/health")["status"] == "ok"
        print("PASS health")
        run_id = os.getenv("SMOKE_RUN_ID")
        if not run_id:
            response = client.post("/api/cases", json={"question": os.environ["SMOKE_QUESTION"]})
            if response.status_code != 202:
                raise SystemExit("BLOCKED: case did not start; use one exact award-bearing OCID and a configured provider")
            run_id = response.json()["run_id"]
        print("Case: " + run_id)
        prefix = "/api/runs/" + run_id
        deadline = time.monotonic() + 180
        while True:
            detail = get(prefix)
            if detail["phase"] not in ("queued", "running", "filing"):
                break
            if time.monotonic() > deadline:
                raise SystemExit("BLOCKED: timed out; inspect this case before retrying with SMOKE_RUN_ID")
            time.sleep(2)
        if detail["phase"] != "awaiting_approval":
            raise SystemExit("FAIL: case is not awaiting approval; inspect its recorded limitations")
        print("PASS awaiting approval")
        if detail["signed_approval_required"] and not os.getenv("SMOKE_APPROVAL_TOKEN"):
            raise SystemExit("BLOCKED: human must review the case, then supply the invitation token as SMOKE_APPROVAL_TOKEN and reuse SMOKE_RUN_ID")
        response = client.post(prefix + "/approve", json={
            "approver_name": os.environ["SMOKE_APPROVER_NAME"], "approver_role": os.environ["SMOKE_APPROVER_ROLE"],
            "decision": "approve", "reason": os.environ["SMOKE_REASON"], "report_hash": detail["report_hash"],
            "request_seq": detail["request"]["seq"], "invitation_token": os.getenv("SMOKE_APPROVAL_TOKEN"),
            "independent_reviewer": True,
        })
        response.raise_for_status()
        deadline = time.monotonic() + 120
        while get(prefix)["phase"] == "filing":
            if time.monotonic() > deadline:
                raise SystemExit("FAIL: filing not confirmed; do not repeat approval")
            time.sleep(2)
        assert get(prefix)["file_created"]
        get(prefix + "/report/download")
        assert get(prefix + "/verify")["valid"]
        print("PASS approved file and audit integrity")


if __name__ == "__main__":
    main()
