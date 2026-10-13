#!/usr/bin/env python3
"""Fetch a dozen small atomic Syzygy files (3 to 5 men) for the identity session.

The files are checked against the sha256 the mirror's own manifest declares.
usage: fetch_tb.py <base url of the syzygy-345 mirror> <destination directory>
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

WANTED = ["KQvK", "KPvK", "KRvK", "KRvKR", "KQvKR", "KRNvKR"]
UA = {"User-Agent": "atomic-stockfish-ci (worker engine check)"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def main() -> int:
    base, dest = sys.argv[1].rstrip("/"), Path(sys.argv[2])
    dest.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(get(base + "/manifest.json"))
    names = {f"{m}.{ext}" for m in WANTED for ext in ("atbw", "atbz")}
    entries = [f for f in manifest["files"] if f["name"] in names]
    if len(entries) != len(names):
        print(f"manifest lists {len(entries)} of the {len(names)} wanted files")
        return 1
    for e in entries:
        data = get(f"{base}/{e['name']}")
        if len(data) != e["bytes"] or hashlib.sha256(data).hexdigest() != e["sha256"]:
            print(f"{e['name']}: does not match the manifest")
            return 1
        (dest / e["name"]).write_bytes(data)
    print(f"{len(entries)} tablebase files, {sum(e['bytes'] for e in entries)} bytes, in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
