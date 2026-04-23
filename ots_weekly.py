#!/usr/bin/env python3
"""
Weekly OpenTimestamps stamping job.

Runs every Sunday 22:00 UK (launchd com.roma.paper_ots.plist). Produces a
single manifest file `.ots/<date>-tree.manifest` listing the current git
HEAD plus the state-tree hash for the week, then runs `ots stamp` on it,
producing `.ots/<date>-tree.manifest.ots`.

Usage:
  python3 track_record/ots_weekly.py [--date YYYY-MM-DD] [--push]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("ots_weekly")

BASE_DIR = Path(__file__).parent.resolve()
OTS_DIR = BASE_DIR / ".ots"

# Path to ots CLI installed into a dedicated venv
OTS_VENV_BIN = BASE_DIR.parent / ".venv-ots" / "bin" / "ots"
OTS_CANDIDATES = [str(OTS_VENV_BIN), "ots"]


def _find_ots() -> str:
    for cand in OTS_CANDIDATES:
        try:
            res = subprocess.run([cand, "--version"], capture_output=True, text=True)
            if res.returncode in (0, 1):  # --version prints on stderr sometimes
                return cand
        except FileNotFoundError:
            continue
    raise RuntimeError(
        "ots CLI not found. Install via: "
        "python3 -m venv .venv-ots && .venv-ots/bin/pip install opentimestamps-client"
    )


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    log.debug(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_week_manifest(date_str: str) -> Path:
    """Create a manifest file listing the week's content hashes.

    Format (one line per file):
        <sha256>  <relative-path>
    Followed by a final line with the git HEAD sha.
    """
    OTS_DIR.mkdir(exist_ok=True)
    manifest = OTS_DIR / f"{date_str}-tree.manifest"

    lines: list[str] = []
    # Hash every file in hashes.json (the chain authoritative list)
    hashes_path = BASE_DIR / "hashes.json"
    if hashes_path.exists():
        lines.append(f"{_sha256_file(hashes_path)}  hashes.json")
    # Hash the README/METHODOLOGY/LICENSE for completeness
    for fname in ("README.md", "METHODOLOGY.md", "LICENSE", "code_versions.md"):
        p = BASE_DIR / fname
        if p.exists():
            lines.append(f"{_sha256_file(p)}  {fname}")

    # Git HEAD sha
    try:
        head = _run(["git", "rev-parse", "HEAD"], cwd=BASE_DIR).stdout.strip()
        lines.append(f"git-head={head}")
    except Exception as e:
        log.warning(f"git rev-parse failed: {e}")

    lines.append(f"stamped-utc={dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")
    manifest.write_text("\n".join(lines) + "\n")
    return manifest


def stamp_manifest(manifest: Path) -> Path:
    ots = _find_ots()
    _run([ots, "stamp", str(manifest)])
    proof = manifest.with_suffix(manifest.suffix + ".ots")
    if not proof.exists():
        raise RuntimeError(f"ots stamp did not produce {proof}")
    log.info(f"Stamped: {proof.name}")
    return proof


def git_commit_stamp(date_str: str, push: bool) -> None:
    try:
        _run(["git", "add", ".ots"], cwd=BASE_DIR)
    except subprocess.CalledProcessError as e:
        log.error(f"git add failed: {e.stderr}")
        return
    status = _run(["git", "status", "--porcelain", ".ots"], cwd=BASE_DIR, check=False)
    if not status.stdout.strip():
        log.info("No .ots changes to commit.")
        return
    msg = f"OpenTimestamps stamp {date_str}"
    try:
        _run(["git", "commit", "-m", msg], cwd=BASE_DIR)
        log.info(f"Committed: {msg}")
    except subprocess.CalledProcessError as e:
        log.error(f"git commit failed: {e.stderr}")
        return
    if push:
        try:
            _run(["git", "push", "origin", "main"], cwd=BASE_DIR)
            log.info("Pushed .ots to origin/main.")
        except subprocess.CalledProcessError as e:
            log.error(f"git push failed: {e.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None,
                        help="YYYY-MM-DD (default: today)")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    date_str = date.strftime("%Y-%m-%d")
    push = args.push or os.environ.get("PAPER_TRACK_RECORD_PUSH") == "1"

    try:
        manifest = build_week_manifest(date_str)
        stamp_manifest(manifest)
        git_commit_stamp(date_str, push=push)
    except Exception as e:
        log.exception(f"ots_weekly failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
