#!/usr/bin/env python3

import argparse, json, os, sys

from datetime import datetime, timezone

REQUIRED = [

    "dq_exactness_attack_summary.json",

    "dq_exactness_attack_decision.json",

    "dq_exactness_by_root.csv",

    "dq_exactness_by_bit.csv",

    "science_overlap_audit.csv",

    "REPAIRED_DQ_DECISION.txt",

    "logs/dq_exactness_repaired.log",

]

def now():

    return datetime.now(timezone.utc).isoformat()

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument("--results-dir", required=True)

    ap.add_argument("--output-json", required=True)

    args = ap.parse_args()

    checks = []

    ok = True

    for rel in REQUIRED:

        p = os.path.join(args.results_dir, rel)

        exists = os.path.exists(p)

        size = os.path.getsize(p) if exists and os.path.isfile(p) else 0

        checks.append({"relative_path": rel, "path": p, "exists": exists, "size_bytes": size})

        if not exists or size == 0:

            ok = False

    out = {

        "timestamp_utc": now(),

        "results_dir": args.results_dir,

        "artifact_integrity_passed": ok,

        "checks": checks,

    }

    os.makedirs(os.path.dirname(args.output_json), exist_ok=True)

    with open(args.output_json, "w", encoding="utf-8") as f:

        json.dump(out, f, indent=2, sort_keys=True)

    print(json.dumps(out, indent=2, sort_keys=True))

    return 0 if ok else 2

if __name__ == "__main__":

    sys.exit(main())

