#!/usr/bin/env python3

import argparse, csv, json, os, re, sys

from datetime import datetime, timezone

import numpy as np

from astropy.io import fits

SCIENCE_EXTENSIONS = ["SCI", "ERR", "SAMP", "TIME"]

ALL_EXTENSIONS = ["SCI", "ERR", "SAMP", "TIME", "DQ"]

def now_utc():

    return datetime.now(timezone.utc).isoformat()

def mkdirp(path):

    os.makedirs(path, exist_ok=True)

    return path

def write_json(path, obj):

    mkdirp(os.path.dirname(path))

    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:

        json.dump(obj, f, indent=2, sort_keys=True)

    os.replace(tmp, path)

def write_csv(path, rows, columns):

    mkdirp(os.path.dirname(path))

    with open(path, "w", newline="", encoding="utf-8") as f:

        w = csv.DictWriter(f, fieldnames=columns)

        w.writeheader()

        for row in rows:

            w.writerow({k: row.get(k, "") for k in columns})

def read_json(path):

    with open(path, "r", encoding="utf-8") as f:

        return json.load(f)

def safe_int(x, default=None):

    try:

        if x is None or x == "":

            return default

        return int(float(x))

    except Exception:

        return default

def root_from_name(path):

    name = os.path.basename(path)

    m = re.match(r"(.+?)_(?:raw|flt|flc)\.fits(?:\.gz)?$", name, re.I)

    return m.group(1) if m else None

def list_files(root, suffixes):

    out = []

    if not root or not os.path.exists(root):

        return out

    for dirpath, _, files in os.walk(root):

        for fn in files:

            low = fn.lower()

            if any(low.endswith(s.lower()) for s in suffixes):

                out.append(os.path.join(dirpath, fn))

    return sorted(out)

def build_map(root):

    m = {}

    for p in list_files(root, ["_flt.fits", "_flc.fits"]):

        r = root_from_name(p)

        if r:

            m[r] = p

    return m

def get_ext(hdul, ext):

    for h in hdul:

        if str(h.header.get("EXTNAME", "")).upper().strip() == ext and h.data is not None:

            return h.data

    return None

def exact_diff(g, r):

    if g is None or r is None:

        return False, None, None, None, None

    if g.shape != r.shape:

        return False, None, None, None, None

    if np.issubdtype(g.dtype, np.floating) or np.issubdtype(r.dtype, np.floating):

        eq = (g == r) | (np.isnan(g) & np.isnan(r))

        diff = np.abs(g.astype(np.float64) - r.astype(np.float64))

        diff[np.isnan(diff)] = 0

    else:

        eq = g == r

        diff = np.abs(g.astype(np.int64) - r.astype(np.int64)).astype(np.float64)

    changed = ~eq

    return bool(np.count_nonzero(changed) == 0), changed, int(np.count_nonzero(changed)), int(g.size), float(np.max(diff)) if diff.size else None

def dq_details(gdq, rdq):

    if gdq is None or rdq is None or gdq.shape != rdq.shape:

        return {

            "exact": False,

            "mismatch_count": None,

            "total": None,

            "xor_values": [],

            "xor_histogram": {},

            "xor_only_4096": False,

            "ref_has_4096_gen_not_count": None,

            "gen_has_4096_ref_not_count": None,

            "bit_rows": [],

            "mask": None,

        }

    g = gdq.astype(np.uint64)

    r = rdq.astype(np.uint64)

    xor = np.bitwise_xor(g, r)

    mask = xor != 0

    mismatch = int(np.count_nonzero(mask))

    total = int(xor.size)

    if mismatch:

        vals, counts = np.unique(xor[mask], return_counts=True)

        xor_values = [int(v) for v in vals.tolist()]

        hist = {str(int(v)): int(c) for v, c in zip(vals, counts)}

    else:

        xor_values, hist = [], {}

    bit_rows = []

    for bit in range(16):

        val = 1 << bit

        c = int(np.count_nonzero((xor & np.uint64(val)) != 0))

        if c:

            bit_rows.append({

                "bit_index": bit,

                "bit_value": val,

                "changed_pixel_count": c,

                "changed_pixel_fraction": float(c / total) if total else None,

            })

    ref4096 = (r & np.uint64(4096)) != 0

    gen4096 = (g & np.uint64(4096)) != 0

    return {

        "exact": mismatch == 0,

        "mismatch_count": mismatch,

        "total": total,

        "xor_values": sorted(xor_values),

        "xor_histogram": hist,

        "xor_only_4096": bool(mismatch > 0 and sorted(xor_values) == [4096]),

        "ref_has_4096_gen_not_count": int(np.count_nonzero(mask & ref4096 & (~gen4096))),

        "gen_has_4096_ref_not_count": int(np.count_nonzero(mask & gen4096 & (~ref4096))),

        "bit_rows": bit_rows,

        "mask": mask,

    }

def validation_fallback(validation_report_path):

    if not validation_report_path or not os.path.exists(validation_report_path):

        return {}

    try:

        v = read_json(validation_report_path)

    except Exception as e:

        return {"read_error": repr(e)}

    return {

        "pass": v.get("pass"),

        "claim_tier_supported": v.get("claim_tier_supported"),

        "raw_root_count_expected": v.get("raw_root_count_expected"),

        "raw_root_count_processed": v.get("raw_root_count_processed"),

        "passed_root_count": v.get("passed_root_count"),

        "failed_root_count": v.get("failed_root_count"),

        "raw_package_sha256_matches_expected": v.get("raw_package_sha256_matches_expected"),

        "top_level_keys": sorted(list(v.keys())),

    }

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument("--package-dir", required=True)

    ap.add_argument("--generated-dir", required=True)

    ap.add_argument("--validation-report", required=True)

    ap.add_argument("--output-dir", required=True)

    ap.add_argument("--expected-root-count", type=int, default=20)

    ap.add_argument("--stenv-release", default="unknown")

    ap.add_argument("--crds-context", default="unknown")

    args = ap.parse_args()

    mkdirp(args.output_dir)

    logs_dir = os.path.join(args.output_dir, "logs")

    mkdirp(logs_dir)

    ref_dir = os.path.join(args.package_dir, "data", "calibrated_reference")

    ref_map = build_map(ref_dir)

    gen_map = build_map(args.generated_dir)

    roots = sorted(set(ref_map.keys()) | set(gen_map.keys()))

    by_root = []

    by_bit = []

    overlap_rows = []

    failures = []

    ext_counts = {e: 0 for e in ALL_EXTENSIONS}

    dq_exact_roots, dq_mismatch_roots, dq_xor4096_roots, dq_non4096_roots = [], [], [], []

    roots_with_overlap = []

    for root in roots:

        ref = ref_map.get(root)

        gen = gen_map.get(root)

        row = {"root": root, "reference_path": ref or "", "generated_path": gen or ""}

        if not ref or not gen:

            failures.append({"root": root, "reason": "missing_reference_or_generated", "reference_present": bool(ref), "generated_present": bool(gen)})

            by_root.append(row)

            continue

        with fits.open(ref, memmap=False) as rh, fits.open(gen, memmap=False) as gh:

            rdq = get_ext(rh, "DQ")

            gdq = get_ext(gh, "DQ")

            dqd = dq_details(gdq, rdq)

            dqmask = dqd["mask"]

            science_overlap = False

            for ext in ALL_EXTENSIONS:

                exact, changed, changed_count, total, max_abs = exact_diff(get_ext(gh, ext), get_ext(rh, ext))

                row[f"{ext}_exact"] = exact

                row[f"{ext}_changed_pixel_count"] = changed_count

                row[f"{ext}_total_pixel_count"] = total

                row[f"{ext}_max_abs_diff"] = max_abs

                if exact:

                    ext_counts[ext] += 1

                if ext in SCIENCE_EXTENSIONS and changed is not None and dqmask is not None and changed.shape == dqmask.shape:

                    ov = int(np.count_nonzero(changed & dqmask))

                    if ov:

                        science_overlap = True

                    overlap_rows.append({

                        "root": root,

                        "extension": ext,

                        "science_exact": exact,

                        "dq_mismatch_pixels": dqd["mismatch_count"],

                        "science_diff_pixels_total": changed_count,

                        "science_diff_pixels_overlap_dq_mismatch": ov,

                    })

            row["DQ_mismatch_pixel_count"] = dqd["mismatch_count"]

            row["DQ_total_pixel_count"] = dqd["total"]

            row["DQ_xor_values"] = json.dumps(dqd["xor_values"])

            row["DQ_xor_histogram"] = json.dumps(dqd["xor_histogram"], sort_keys=True)

            row["DQ_xor_only_4096"] = dqd["xor_only_4096"]

            row["ref_has_4096_gen_not_count"] = dqd["ref_has_4096_gen_not_count"]

            row["gen_has_4096_ref_not_count"] = dqd["gen_has_4096_ref_not_count"]

            row["science_diff_overlap_DQ_mismatch"] = science_overlap

            if row.get("DQ_exact") is True:

                dq_exact_roots.append(root)

            else:

                dq_mismatch_roots.append(root)

                if dqd["xor_only_4096"]:

                    dq_xor4096_roots.append(root)

                else:

                    dq_non4096_roots.append(root)

            if science_overlap:

                roots_with_overlap.append(root)

            for br in dqd["bit_rows"]:

                br = dict(br)

                br["root"] = root

                by_bit.append(br)

        by_root.append(row)

    science_exact_all = all(ext_counts[e] == args.expected_root_count for e in SCIENCE_EXTENSIONS)

    literal_dq20 = ext_counts["DQ"] == args.expected_root_count

    all_dq_xor4096 = bool(dq_mismatch_roots) and sorted(dq_mismatch_roots) == sorted(dq_xor4096_roots)

    no_overlap = len(roots_with_overlap) == 0

    if literal_dq20 and science_exact_all:

        decision = "literal_dq_exact_20of20_supported"

        decision_certificate = "dq_repaired_literal_dq_exact_20of20_supported"

    elif science_exact_all and all_dq_xor4096 and no_overlap:

        decision = "literal_dq_exact_blocked_by_4096_only_no_science_overlap"

        decision_certificate = "dq_repaired_literal_dq_exact_blocked_4096_only_no_science_overlap"

    elif failures:

        decision = "literal_dq_exact_incomplete_missing_products"

        decision_certificate = "dq_repaired_literal_dq_exact_incomplete_missing_products"

    else:

        decision = "literal_dq_exact_blocked_by_non4096_or_science_overlap"

        decision_certificate = "dq_repaired_literal_dq_exact_blocked_non4096_or_science_overlap"

    summary = {

        "timestamp_utc": now_utc(),

        "script": "scripts/dq_exactness_repaired.py",

        "stenv_release": args.stenv_release,

        "crds_context": args.crds_context,

        "expected_root_count": args.expected_root_count,

        "reference_root_count": len(ref_map),

        "generated_root_count": len(gen_map),

        "roots_seen_count": len(roots),

        "failure_count": len(failures),

        "failures": failures,

        "extension_exact_counts": ext_counts,

        "science_exact_all_20": science_exact_all,

        "literal_dq_exact_20of20_supported": literal_dq20,

        "dq_exact_count": ext_counts["DQ"],

        "dq_mismatch_count": len(dq_mismatch_roots),

        "dq_exact_roots": dq_exact_roots,

        "dq_mismatch_roots": dq_mismatch_roots,

        "dq_xor_only_4096_roots": dq_xor4096_roots,

        "dq_non_4096_roots": dq_non4096_roots,

        "all_dq_mismatch_roots_xor_only_4096": all_dq_xor4096,

        "roots_with_science_overlap": roots_with_overlap,

        "no_science_overlap": no_overlap,

        "decision": decision,

        "decision_certificate": decision_certificate,

        "validation_report_fallback": validation_fallback(args.validation_report),

    }

    write_json(os.path.join(args.output_dir, "dq_exactness_attack_summary.json"), summary)

    write_json(os.path.join(args.output_dir, "dq_exactness_attack_decision.json"), {

        "timestamp_utc": now_utc(),

        "decision": decision,

        "decision_certificate": decision_certificate,

        "literal_dq_exact_20of20_supported": literal_dq20,

        "science_exact_all_20": science_exact_all,

        "dq_exact_count": ext_counts["DQ"],

        "dq_mismatch_count": len(dq_mismatch_roots),

    })

    write_csv(

        os.path.join(args.output_dir, "dq_exactness_by_root.csv"),

        by_root,

        [

            "root", "SCI_exact", "ERR_exact", "SAMP_exact", "TIME_exact", "DQ_exact",

            "SCI_changed_pixel_count", "ERR_changed_pixel_count", "SAMP_changed_pixel_count", "TIME_changed_pixel_count", "DQ_changed_pixel_count",

            "DQ_mismatch_pixel_count", "DQ_total_pixel_count", "DQ_xor_values", "DQ_xor_histogram", "DQ_xor_only_4096",

            "ref_has_4096_gen_not_count", "gen_has_4096_ref_not_count", "science_diff_overlap_DQ_mismatch",

            "reference_path", "generated_path",

        ],

    )

    write_csv(os.path.join(args.output_dir, "dq_exactness_by_bit.csv"), by_bit, ["root", "bit_index", "bit_value", "changed_pixel_count", "changed_pixel_fraction"])

    write_csv(os.path.join(args.output_dir, "science_overlap_audit.csv"), overlap_rows, ["root", "extension", "science_exact", "dq_mismatch_pixels", "science_diff_pixels_total", "science_diff_pixels_overlap_dq_mismatch"])

    # Always emit a one-line status.

    with open(os.path.join(args.output_dir, "REPAIRED_DQ_DECISION.txt"), "w", encoding="utf-8") as f:

        f.write(decision_certificate + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))

    if decision == "literal_dq_exact_20of20_supported":

        return 0

    if decision == "literal_dq_exact_blocked_by_4096_only_no_science_overlap":

        return 3

    return 4

if __name__ == "__main__":

    sys.exit(main())

