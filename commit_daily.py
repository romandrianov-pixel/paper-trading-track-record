#!/usr/bin/env python3
"""
Daily track-record commit hook.

Called by paper_runner.py after each daily run (gated by env var
PAPER_TRACK_RECORD=1 or config.yaml `track_record.enabled: true`).

Pipeline:
  1. Copy live state/*.json into track_record/state/<date>/
  2. Copy digest/<date>.md into track_record/digests/
  3. Append fresh row to track_record/daily/<strategy>.csv
  4. Copy raw price snapshots from cache into track_record/raw_prices/<date>/
  5. Update hashes.json with running SHA256 chain
  6. Update code_versions.md
  7. git add -A && git commit -m "Daily state <date>" [+ push if env flag]

Usage:
  python3 track_record/commit_daily.py [--date YYYY-MM-DD] [--push]

The `--push` flag (or env var PAPER_TRACK_RECORD_PUSH=1) enables pushing to
`origin main`.  Default is commit-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("commit_daily")

BASE_DIR = Path(__file__).parent.resolve()                # .../paper_trading/track_record
PAPER_DIR = BASE_DIR.parent                               # .../paper_trading
STATE_LIVE = PAPER_DIR / "state"
DIGEST_LIVE = PAPER_DIR / "digest"
LOGS_LIVE = PAPER_DIR / "logs"
CACHE_LIVE = PAPER_DIR / ".cache"
CBOE_CACHE = PAPER_DIR / "cache" / "vx1_cboe.json"
SNAPSHOT_RUNTIME = PAPER_DIR / "cache" / "snapshots"      # populated by data_sources

TR_STATE = BASE_DIR / "state"
TR_DAILY = BASE_DIR / "daily"
TR_DIGESTS = BASE_DIR / "digests"
TR_RAW = BASE_DIR / "raw_prices"
HASHES_PATH = BASE_DIR / "hashes.json"
CODE_VERSIONS_PATH = BASE_DIR / "code_versions.md"


# ---------------------------------------------------------------------------
# utilities
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_tree(root: Path) -> str:
    """SHA256 over sorted (relative_path, sha256(file_bytes)) pairs."""
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    rels = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rels.append((str(p.relative_to(root)), _sha256_file(p)))
    for rel, sha in rels:
        h.update(rel.encode())
        h.update(sha.encode())
    return h.hexdigest()


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    log.debug(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# copy helpers
# ---------------------------------------------------------------------------

def copy_state_snapshot(date_str: str) -> Path:
    """Copy state/*.json into track_record/state/<date>/ (including four_way legs)."""
    out = TR_STATE / date_str
    out.mkdir(parents=True, exist_ok=True)
    # Top-level per-strategy files
    for p in STATE_LIVE.glob("*.json"):
        shutil.copy2(p, out / p.name)
    # four_way per-leg subdir
    leg_dir = STATE_LIVE / "four_way"
    if leg_dir.exists():
        sub = out / "four_way"
        sub.mkdir(exist_ok=True)
        for p in leg_dir.glob("*.json"):
            shutil.copy2(p, sub / p.name)
    return out


def copy_digest(date_str: str) -> Path | None:
    src = DIGEST_LIVE / f"{date_str}.md"
    if src.exists():
        dst = TR_DIGESTS / src.name
        shutil.copy2(src, dst)
        return dst
    return None


def copy_raw_prices(date_str: str) -> Path:
    """Copy raw snapshots into track_record/raw_prices/<date>/.

    Sources:
      - /Users/tars/trading-lab/paper_trading/cache/snapshots/<date>/*
        (populated by data_sources.py when snapshot_dir kwarg is set)
      - /Users/tars/trading-lab/paper_trading/cache/vx1_cboe.json
        (copied as cboe_vx1.json)
    """
    out = TR_RAW / date_str
    out.mkdir(parents=True, exist_ok=True)

    # Runtime snapshots (if data_sources was hooked)
    src_snap = SNAPSHOT_RUNTIME / date_str
    if src_snap.exists():
        for p in src_snap.iterdir():
            if p.is_file():
                shutil.copy2(p, out / p.name)

    # CBOE VX1 live scrape
    if CBOE_CACHE.exists():
        shutil.copy2(CBOE_CACHE, out / "cboe_vx1.json")

    return out


def append_daily_csv_rows(date_str: str) -> None:
    """Mirror logs/<strategy>/daily.csv and logs/four_way/*.csv into track_record/daily/.

    We simply overwrite (files are append-only upstream, so the latest copy
    is the full history).
    """
    TR_DAILY.mkdir(parents=True, exist_ok=True)
    # Per-strategy daily.csv
    if LOGS_LIVE.exists():
        for strat_dir in LOGS_LIVE.iterdir():
            if not strat_dir.is_dir():
                continue
            for csv_file in strat_dir.glob("*.csv"):
                if strat_dir.name == "four_way":
                    out_dir = TR_DAILY / "four_way"
                    out_dir.mkdir(exist_ok=True)
                    shutil.copy2(csv_file, out_dir / csv_file.name)
                elif csv_file.name == "daily.csv":
                    shutil.copy2(csv_file, TR_DAILY / f"{strat_dir.name}.csv")


# ---------------------------------------------------------------------------
# hash chain
# ---------------------------------------------------------------------------

def update_hash_chain(date_str: str) -> dict:
    """Append today's state tree hash to hashes.json with link to prior day."""
    state_dir = TR_STATE / date_str
    state_tree_sha = _sha256_tree(state_dir)
    raw_tree_sha = _sha256_tree(TR_RAW / date_str)

    if HASHES_PATH.exists():
        chain = json.loads(HASHES_PATH.read_text())
    else:
        chain = {}

    # Find prior date entry for chain linking
    prior_dates = sorted([d for d in chain.keys() if d < date_str])
    prev_sha = chain[prior_dates[-1]]["state_tree_sha"] if prior_dates else None

    record = {
        "prev_date": prior_dates[-1] if prior_dates else None,
        "prev_sha": prev_sha,
        "state_tree_sha": state_tree_sha,
        "raw_tree_sha": raw_tree_sha,
        "committed_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    chain[date_str] = record
    HASHES_PATH.write_text(json.dumps(chain, indent=2, sort_keys=True) + "\n")
    return record


# ---------------------------------------------------------------------------
# code version tracking
# ---------------------------------------------------------------------------

def update_code_versions(date_str: str) -> None:
    """Append today's paper_runner commit SHA to code_versions.md."""
    try:
        sha = _run(["git", "describe", "--always", "--dirty"], cwd=PAPER_DIR).stdout.strip()
    except Exception as e:
        sha = f"unknown ({e})"

    if CODE_VERSIONS_PATH.exists():
        body = CODE_VERSIONS_PATH.read_text()
    else:
        body = (
            "# Code versions\n\n"
            "Mapping from track-record date → paper_runner git SHA. Used by "
            "`verify.py` to find the source at the time of the run.\n\n"
            "| Date | paper_runner SHA |\n"
            "|------|------------------|\n"
        )
    # Avoid duplicate lines if same date committed twice
    marker = f"| {date_str} |"
    if marker in body:
        # replace line
        lines = body.splitlines()
        new_lines = [ln for ln in lines if not ln.startswith(marker)]
        new_lines.append(f"{marker} `{sha}` |")
        body = "\n".join(new_lines) + "\n"
    else:
        body += f"{marker} `{sha}` |\n"
    CODE_VERSIONS_PATH.write_text(body)


# ---------------------------------------------------------------------------
# git commit / push
# ---------------------------------------------------------------------------

def git_commit_and_maybe_push(date_str: str, push: bool) -> None:
    """Run git add/commit (+ optional push) inside BASE_DIR."""
    # Check GPG signing preference
    sign_flag: list[str] = []
    try:
        res = _run(["git", "config", "--get", "commit.gpgsign"], cwd=BASE_DIR, check=False)
        if res.returncode == 0 and res.stdout.strip().lower() == "true":
            sign_flag = ["-S"]
    except Exception:
        pass

    try:
        _run(["git", "add", "-A"], cwd=BASE_DIR)
    except subprocess.CalledProcessError as e:
        log.error(f"git add failed: {e.stderr}")
        return

    # Check if there is anything to commit
    status = _run(["git", "status", "--porcelain"], cwd=BASE_DIR, check=False)
    if not status.stdout.strip():
        log.info("No changes to commit.")
        return

    msg = f"Daily state {date_str}"
    try:
        _run(["git", "commit", *sign_flag, "-m", msg], cwd=BASE_DIR)
        log.info(f"Committed: {msg}")
    except subprocess.CalledProcessError as e:
        # If signing failed, retry without -S
        if sign_flag and ("gpg" in (e.stderr or "").lower() or "signing" in (e.stderr or "").lower()):
            log.warning("GPG signing failed, retrying without -S")
            _run(["git", "commit", "-m", msg], cwd=BASE_DIR)
        else:
            log.error(f"git commit failed: {e.stderr}")
            return

    if push:
        try:
            _run(["git", "push", "origin", "main"], cwd=BASE_DIR)
            log.info("Pushed to origin/main.")
        except subprocess.CalledProcessError as e:
            log.error(f"git push failed: {e.stderr}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def commit_daily(date: dt.date | None = None, push: bool = False) -> None:
    if date is None:
        date = dt.date.today()
    date_str = date.strftime("%Y-%m-%d")
    log.info(f"track_record commit for {date_str} (push={push})")

    copy_state_snapshot(date_str)
    copy_digest(date_str)
    append_daily_csv_rows(date_str)
    copy_raw_prices(date_str)
    record = update_hash_chain(date_str)
    update_code_versions(date_str)

    log.info(
        f"state_tree_sha={record['state_tree_sha'][:12]} "
        f"prev_sha={(record['prev_sha'] or 'None')[:12]}"
    )

    git_commit_and_maybe_push(date_str, push=push)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None,
                        help="YYYY-MM-DD (default: today)")
    parser.add_argument("--push", action="store_true",
                        help="also push to origin/main (overrides env var)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    date = dt.date.fromisoformat(args.date) if args.date else None
    push = args.push or os.environ.get("PAPER_TRACK_RECORD_PUSH") == "1"

    try:
        commit_daily(date=date, push=push)
        return 0
    except Exception as e:
        log.exception(f"commit_daily failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
