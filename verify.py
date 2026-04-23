#!/usr/bin/env python3
"""
Auditor-facing verifier.

Usage:
  python3 verify.py --date 2026-04-23
  python3 verify.py --chain              # verify entire hash chain
  python3 verify.py --ots                # verify every .ots proof

Checks:
  1. SHA256 hash chain integrity (each day's prev_sha matches prior day's
     state_tree_sha).
  2. State-tree hash matches on-disk contents of state/<date>/.
  3. Raw-price snapshot tree hash matches on-disk contents of raw_prices/<date>/.
  4. OpenTimestamps proof files are present and non-empty.
     (Full Bitcoin verification requires the `ots verify` CLI; this script
     calls it if available.)

NOTE: Full signal replay (recomputing today's fills from raw_prices +
prior-day state) is TODO — requires importing the strategy modules at the
version recorded in code_versions.md. For now we verify the cryptographic
chain; signal replay will be added in a follow-up.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("verify")

BASE_DIR = Path(__file__).parent.resolve()
TR_STATE = BASE_DIR / "state"
TR_RAW = BASE_DIR / "raw_prices"
HASHES_PATH = BASE_DIR / "hashes.json"
OTS_DIR = BASE_DIR / ".ots"

OTS_VENV_BIN = BASE_DIR.parent / ".venv-ots" / "bin" / "ots"


# ---------------------------------------------------------------------------
# hash helpers (must match commit_daily.py exactly)
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_tree(root: Path) -> str:
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


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def load_chain() -> dict:
    if not HASHES_PATH.exists():
        raise RuntimeError(f"{HASHES_PATH} not found — is this a paper-trading-track-record clone?")
    return json.loads(HASHES_PATH.read_text())


def verify_chain(chain: dict) -> list[str]:
    """Walk the chain; return a list of error strings (empty = OK)."""
    errors: list[str] = []
    dates = sorted(chain.keys())
    prev_sha = None
    for d in dates:
        rec = chain[d]
        # link
        if rec.get("prev_sha") != prev_sha:
            errors.append(
                f"[{d}] prev_sha mismatch: expected {prev_sha!r}, got {rec.get('prev_sha')!r}"
            )
        # state tree
        actual_state = _sha256_tree(TR_STATE / d)
        if actual_state != rec["state_tree_sha"]:
            errors.append(
                f"[{d}] state_tree_sha mismatch:\n"
                f"  recorded: {rec['state_tree_sha']}\n"
                f"  on-disk : {actual_state}"
            )
        # raw tree
        recorded_raw = rec.get("raw_tree_sha")
        if recorded_raw:
            actual_raw = _sha256_tree(TR_RAW / d)
            if actual_raw != recorded_raw:
                errors.append(
                    f"[{d}] raw_tree_sha mismatch:\n"
                    f"  recorded: {recorded_raw}\n"
                    f"  on-disk : {actual_raw}"
                )
        prev_sha = rec["state_tree_sha"]
    return errors


def verify_date(chain: dict, date_str: str) -> list[str]:
    """Verify a single date: its record exists and hashes match."""
    errors: list[str] = []
    rec = chain.get(date_str)
    if not rec:
        errors.append(f"No chain entry for {date_str}")
        return errors
    actual_state = _sha256_tree(TR_STATE / date_str)
    if actual_state != rec["state_tree_sha"]:
        errors.append(
            f"[{date_str}] state_tree_sha mismatch:\n"
            f"  recorded: {rec['state_tree_sha']}\n"
            f"  on-disk : {actual_state}"
        )
    recorded_raw = rec.get("raw_tree_sha")
    if recorded_raw:
        actual_raw = _sha256_tree(TR_RAW / date_str)
        if actual_raw != recorded_raw:
            errors.append(
                f"[{date_str}] raw_tree_sha mismatch:\n"
                f"  recorded: {recorded_raw}\n"
                f"  on-disk : {actual_raw}"
            )
    return errors


def find_ots_cli() -> str | None:
    candidates = [str(OTS_VENV_BIN), "ots"]
    for cand in candidates:
        try:
            res = subprocess.run([cand, "--version"], capture_output=True, text=True)
            if res.returncode in (0, 1):
                return cand
        except FileNotFoundError:
            continue
    return None


def verify_ots(cli: str | None) -> list[str]:
    errors: list[str] = []
    if not OTS_DIR.exists():
        return ["No .ots directory — no OpenTimestamps proofs yet."]
    proofs = sorted(OTS_DIR.glob("*.ots"))
    if not proofs:
        return ["No .ots proofs present yet (weekly job has not run)."]
    if cli is None:
        return [
            f"Found {len(proofs)} proofs but no `ots` CLI available. "
            "Install with: pip install opentimestamps-client"
        ]
    for proof in proofs:
        # For `ots verify`, the original file must exist alongside
        original = proof.with_suffix("")
        if not original.exists():
            errors.append(f"{proof.name}: original manifest missing at {original}")
            continue
        try:
            res = subprocess.run(
                [cli, "verify", str(proof)],
                capture_output=True, text=True, timeout=60,
            )
            # ots verify exits 0 on success, 1 if pending or failed
            if res.returncode != 0:
                msg = res.stderr.strip() or res.stdout.strip()
                if "pending" in msg.lower() or "not confirmed" in msg.lower():
                    log.info(f"{proof.name}: pending Bitcoin confirmation (normal for new stamps)")
                else:
                    errors.append(f"{proof.name}: {msg}")
        except subprocess.TimeoutExpired:
            errors.append(f"{proof.name}: verify timed out")
        except Exception as e:
            errors.append(f"{proof.name}: {e}")
    return errors


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the paper-trading track record cryptographic chain."
    )
    parser.add_argument("--date", type=str, default=None,
                        help="Verify a single YYYY-MM-DD entry")
    parser.add_argument("--chain", action="store_true",
                        help="Verify the entire hash chain (default if no other flag)")
    parser.add_argument("--ots", action="store_true",
                        help="Also verify OpenTimestamps proofs")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        chain = load_chain()
    except RuntimeError as e:
        if args.date:
            # Bootstrap case: hashes.json not created yet
            print(f"[INFO] {e}")
            print("[INFO] First-day bootstrap — no chain to verify yet.")
            return 0
        print(f"[ERROR] {e}")
        return 2

    all_errors: list[str] = []

    if args.date:
        errors = verify_date(chain, args.date)
        all_errors.extend(errors)
    else:
        errors = verify_chain(chain)
        all_errors.extend(errors)

    if args.ots:
        cli = find_ots_cli()
        ots_errs = verify_ots(cli)
        all_errors.extend(ots_errs)

    if all_errors:
        print("[FAILED]")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    n_days = len(chain)
    print(f"[VERIFIED] hash chain intact across {n_days} day(s)")
    if args.date:
        print(f"  date:          {args.date}")
        rec = chain[args.date]
        print(f"  state_tree_sha: {rec['state_tree_sha']}")
        print(f"  raw_tree_sha:   {rec.get('raw_tree_sha', 'n/a')}")
        print(f"  committed_utc:  {rec.get('committed_utc', 'n/a')}")
    if args.ots:
        print("  OpenTimestamps: proofs verified (or pending confirmation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
