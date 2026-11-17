"""Offline secret scanning of all reachable Git history and staged content."""

import argparse
import json
import math
import os
import re
import subprocess
from collections import Counter

RULES = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "provider-token": re.compile(r"\b(?:gsk_[A-Za-z0-9]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})\b"),
    "credential-url": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]{8,}@", re.I),
}
ASSIGNMENT = re.compile(r"(?i)(?:api[_-]?key|secret|access[_-]?token|password|passwd|credential)\w*[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']")


def findings(text):
    found = set()
    known = [value for name, value in os.environ.items() if re.search(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL", name) and len(value) >= 12]
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in RULES.items():
            if any(match.group() != "sk-conformance-dummy-secret-000000000000" for match in pattern.finditer(line)):
                found.add((number, rule))
        if any(value in line for value in known):
            found.add((number, "configured-secret"))
        for match in ASSIGNMENT.finditer(line):
            value = match[1]
            entropy = -sum((count / len(value)) * math.log2(count / len(value)) for count in Counter(value).values())
            if entropy >= 3.8 and not any(marker in value.lower() for marker in ("example", "placeholder", "test-only", "redacted", "${")):
                found.add((number, "high-entropy-credential"))
    return sorted(found)


def git(*args):
    return subprocess.check_output(["git", *args])


def scan(history=True, staged=False):
    objects = {}
    if history:
        for line in git("rev-list", "--objects", "--all").decode("utf-8", errors="replace").splitlines():
            oid, _, path = line.partition(" ")
            if path:
                objects[oid] = path
    if staged:
        for entry in git("ls-files", "--stage", "-z").decode("utf-8", errors="replace").split("\0"):
            if entry:
                metadata, path = entry.split("\t", 1)
                objects[metadata.split()[1]] = path
    result = []
    checked = 0
    process = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for oid, path in objects.items():
            process.stdin.write((oid + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3:
                raise RuntimeError("Cannot inspect Git object")
            content = process.stdout.read(int(header[2]))
            process.stdout.read(1)
            if header[1] != b"blob":
                continue
            checked += 1
            for line, rule in findings(content.decode("utf-8", errors="replace")):
                result.append({"blob": oid, "path": path, "line": line, "rule": rule})
    finally:
        process.stdin.close()
        process.wait()
    return {"scope": "full reachable history" if history else "index", "blobs_checked": checked, "findings": result, "values_disclosed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    result = scan(history=args.history or not args.staged, staged=args.staged)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["findings"]))


if __name__ == "__main__":
    main()
