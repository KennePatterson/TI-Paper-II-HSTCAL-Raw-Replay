#!/usr/bin/env python3

import argparse

import csv

import json

import os

import re

import sys

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

def root_from_name(path):

    name = os.path.basename(path)

    m = re.match(r"(.+?)_(?:raw|flt|flc)\.fits(?:\.gz)?$", name, re.IGNORECASE)

    return m.group(1) if m else None

def list_files(root, suffixes):

    out = []

    if not root or not os.path.exists(root):

        return out

    for dirpath, _, filenames in os.walk(root):

        for fn in filenames:

            low = fn.lower()

            if any(low.endswith(s.lower()) for s in suffixes):

                out.append(os.path.join(dirpath, fn))

    return sorted(out)

def build_reference_map(package_dir):

    cal_dir = os.path.join(package_dir, "data", "calibrated_reference")

    ref = {}

    for p in list_files(cal_dir, ["_flt.fits", "_flc.fits"]):

        r = root_from_name(p)

        if r:

            ref[r] = p

    return ref

def build_generated_map(generated_dir):

    gen = {}

    for p in list_files(generated_dir, ["_flt.fits", "_flc.fits"]):

        r = root_from_name(p)

        if r:

            gen[r] = p

    return gen

def get_ext_data(hdul, extname):

    for hdu in hdul:

        name = str(hdu.header.get("EXTNAME", "")).upper().strip()

        if name == extname and hdu.data is not None:

            return hdu.data

    return None

def exact_and_diff(gen_data, ref_data):

    if gen_data is None or ref_data is None:

        return False, None, None, None, None

    if gen_data.shape != ref_data.shape:

        return False, None, None, None, None

    if np.issubdtype(gen_data.dtype, np.floating) or np.issubdtype(ref_data.dtype, np.floating):

        equal = (gen_data == ref_data) | (np.isnan(gen_data) & np.isnan(ref_data))

        diff = np.abs(gen_data.astype(np.float64) - ref_data.astype(np.float64))

        diff[np.isnan(diff)] = 0.0

    else:

        equal = gen_data == ref_data

        diff = np.abs(gen_data.astype(np.int64) - ref_data.astype(np.int64)).astype(np.float64)

    changed = ~equal

    changed_count = int(np.count_nonzero(changed))

    total = int(gen_data.size)

    max_abs = float(np.max(diff)) if diff.size else None

    return changed_count == 0, changed, changed_count, total, max_abs

def dq_analysis(gen_dq, ref_dq):

    if gen_dq is None or ref_dq is None:

        return None

    if gen_dq.shape != ref_dq.shape:

        return None

    g = gen_dq.astype(np.uint64)

    r = ref_dq.astype(np.uint64)

    xor = np.bitwise_xor(g, r)

    mask = xor != 0

    mismatch_count = int(np.count_nonzero(mask))

    total = int(xor.size)

    if mismatch_count:

        xor_values, xor_counts = np.unique(xor[mask], return_counts=True)

    else:

        xor_values = np.array([], dtype=np.uint64)

        xor_counts = np.array([], dtype=np.int64)

    xor_list = sorted([int(v) for v in xor_values.tolist()]) if mismatch_count else []

    xor_hist = {str(int(v)): int(c) for v, c in zip(xor_values, xor_counts)}

    bit_rows = []

    for bit in range(16):

        bit_value = 1 << bit

        bit_mask = (xor & np.uint64(bit_value)) != 0

        c = int(np.count_nonzero(bit_mask))

        if c:

            bit_rows.append({

                "bit_index": bit,

                "bit_value": bit_value,

                "changed_pixel_count": c,

                "changed_pixel_fraction": float(c / total) if total else None,

            })

    ref_has_4096 = (r & np.uint64(4096)) != 0

    gen_has_4096 = (g & np.uint64(4096)) != 0

    ref_has_4096_gen_not = mask & ref_has_4096 & (~gen_has_4096)

    gen_has_4096_ref_not = mask & gen_has_4096 & (~ref_has_4096)

    both_have_4096_but_other_diff = mask & ref_has_4096 & gen_has_4096

    neither_has_4096_but_diff = mask & (~ref_has_4096) & (~gen_has_4096)

    return {

        "mask": mask,

        "exact": mismatch_count == 0,

        "mismatch_count": mismatch_count,

        "total": total,

        "fraction": float(mismatch_count / total) if total else None,

        "xor_values": xor_list,

        "xor_histogram": xor_hist,

        "xor_only_4096": bool(mismatch_count > 0 and xor_list == [4096]),

        "bit_rows": bit_rows,

        "ref_has_4096_gen_not_count": int(np.count_nonzero(ref_has_4096_gen_not)),

        "gen_has_4096_ref_not_count": int(np.count_nonzero(gen_has_4096_ref_not)),

        "both_have_4096_but_other_diff_count": int(np.count_nonzero(both_have_4096_but_other_diff)),

        "neither_has_4096_but_diff_count": int(np.count_nonzero(neither_has_4096_but_diff)),

    }

def analyze_mode(mode_name, ref_map, gen_map, expected):

    by_root = []

    by_bit = []

    overlap_rows = []

    failures = []

    roots = sorted(set(ref_map.keys()) | set(gen_map.keys()))

    ext_counts = {ext: 0 for ext in ALL_EXTENSIONS}

    dq_exact_roots = []

    dq_mismatch_roots = []

    dq_xor_only_4096_roots = []

    dq_non_4096_roots = []

    roots_with_science_overlap = []

    roots_processed = 0

    for root in roots:

        ref_path = ref_map.get(root)

        gen_path = gen_map.get(root)

        if not ref_path or not gen_path:

            failures.append({

                "mode": mode_name,

                "root": root,

                "reason": "missing_reference_or_generated",

                "reference_present": bool(ref_path),

                "generated_present": bool(gen_path),

            })

            continue

        roots_processed += 1

        with fits.open(ref_path, memmap=False) as ref_hdul, fits.open(gen_path, memmap=False) as gen_hdul:

            row = {"mode": mode_name, "root": root, "reference_path": ref_path, "generated_path": gen_path}

            gen_dq = get_ext_data(gen_hdul, "DQ")

            ref_dq = get_ext_data(ref_hdul, "DQ")

            dqa = dq_analysis(gen_dq, ref_dq)

            dq_mask = dqa["mask"] if dqa else None

            science_overlap = False

            for ext in ALL_EXTENSIONS:

                gen_data = get_ext_data(gen_hdul, ext)

                ref_data = get_ext_data(ref_hdul, ext)

                exact, changed_mask, changed_count, total, max_abs = exact_and_diff(gen_data, ref_data)

                row[f"{ext}_exact"] = exact

                row[f"{ext}_changed_pixel_count"] = changed_count

                row[f"{ext}_total_pixel_count"] = total

                row[f"{ext}_max_abs_diff"] = max_abs

                if exact:

                    ext_counts[ext] += 1

                if ext in SCIENCE_EXTENSIONS and dq_mask is not None and changed_mask is not None:

                    if changed_mask.shape == dq_mask.shape:

                        overlap = changed_mask & dq_mask

                        overlap_count = int(np.count_nonzero(overlap))

                        if overlap_count > 0:

                            science_overlap = True

                        dq_count = int(np.count_nonzero(dq_mask))

                        overlap_rows.append({

                            "mode": mode_name,

                            "root": root,

                            "extension": ext,

                            "science_exact": exact,

                            "dq_mismatch_pixels": dq_count,

                            "science_diff_pixels_total": changed_count,

                            "science_diff_pixels_overlap_dq_mismatch": overlap_count,

                            "overlap_fraction_of_dq_mismatch": float(overlap_count / dq_count) if dq_count else 0.0,

                            "overlap_fraction_of_science_diff": float(overlap_count / changed_count) if changed_count else 0.0,

                        })

            if dqa:

                row["DQ_mismatch_pixel_count"] = dqa["mismatch_count"]

                row["DQ_total_pixel_count"] = dqa["total"]

                row["DQ_mismatch_fraction"] = dqa["fraction"]

                row["DQ_xor_values"] = json.dumps(dqa["xor_values"])

                row["DQ_xor_histogram"] = json.dumps(dqa["xor_histogram"], sort_keys=True)

                row["DQ_xor_only_4096"] = dqa["xor_only_4096"]

                row["ref_has_4096_gen_not_count"] = dqa["ref_has_4096_gen_not_count"]

                row["gen_has_4096_ref_not_count"] = dqa["gen_has_4096_ref_not_count"]

                row["both_have_4096_but_other_diff_count"] = dqa["both_have_4096_but_other_diff_count"]

                row["neither_has_4096_but_diff_count"] = dqa["neither_has_4096_but_diff_count"]

                if dqa["exact"]:

                    dq_exact_roots.append(root)

                else:

                    dq_mismatch_roots.append(root)

                if dqa["xor_only_4096"]:

                    dq_xor_only_4096_roots.append(root)

                elif not dqa["exact"]:

                    dq_non_4096_roots.append(root)

                for br in dqa["bit_rows"]:

                    br = dict(br)

                    br["mode"] = mode_name

                    br["root"] = root

                    by_bit.append(br)

            row["science_diff_overlap_DQ_mismatch"] = science_overlap

            if science_overlap:

                roots_with_science_overlap.append(root)

            by_root.append(row)

    science_exact = all(ext_counts.get(ext) == expected for ext in SCIENCE_EXTENSIONS)

    dq_exact = ext_counts.get("DQ") == expected

    all_4096 = len(dq_mismatch_roots) > 0 and sorted(dq_mismatch_roots) == sorted(dq_xor_only_4096_roots)

    no_overlap = len(roots_with_science_overlap) == 0

    mode_summary = {

        "mode": mode_name,

        "expected_root_count": expected,

        "roots_processed": roots_processed,

        "reference_root_count": len(ref_map),

        "generated_root_count": len(gen_map),

        "failure_count": len(failures),

        "failures": failures,

        "extension_exact_counts": ext_counts,

        "science_exact_all_roots": science_exact,

        "dq_exact_all_roots": dq_exact,

        "dq_exact_roots": sorted(dq_exact_roots),

        "dq_mismatch_roots": sorted(dq_mismatch_roots),

        "dq_mismatch_count": len(dq_mismatch_roots),

        "dq_xor_only_4096_roots": sorted(dq_xor_only_4096_roots),

        "dq_non_4096_roots": sorted(dq_non_4096_roots),

        "all_dq_mismatch_roots_xor_only_4096": all_4096,

        "roots_with_science_overlap": sorted(roots_with_science_overlap),

        "no_science_overlap": no_overlap,

        "literal_dq_exact_20of20_supported": dq_exact,

    }

    return mode_summary, by_root, by_bit, overlap_rows

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument("--package-dir", required=True)

    ap.add_argument("--baseline-generated-dir", required=True)

    ap.add_argument("--astrodrizzle-generated-dir", required=False, default="")

    ap.add_argument("--output-dir", required=True)

    ap.add_argument("--expected-root-count", type=int, default=20)

    ap.add_argument("--stenv-release", default="unknown")

    ap.add_argument("--crds-context", default="unknown")

    args = ap.parse_args()

    mkdirp(args.output_dir)

    ref_map = build_reference_map(args.package_dir)

    baseline_map = build_generated_map(args.baseline_generated_dir)

    astro_map = build_generated_map(args.astrodrizzle_generated_dir) if args.astrodrizzle_generated_dir else {}

    mode_summaries = []

    all_root_rows = []

    all_bit_rows = []

    all_overlap_rows = []

    for mode_name, gen_map in [

        ("baseline_calwf3", baseline_map),

        ("astrodrizzle_dq_update_attempt", astro_map),

    ]:

        if not gen_map:

            mode_summaries.append({

                "mode": mode_name,

                "skipped": True,

                "reason": "no generated products found for mode",

            })

            continue

        summary, rows, bits, overlaps = analyze_mode(mode_name, ref_map, gen_map, args.expected_root_count)

        mode_summaries.append(summary)

        all_root_rows.extend(rows)

        all_bit_rows.extend(bits)

        all_overlap_rows.extend(overlaps)

    literal_modes = [m for m in mode_summaries if m.get("literal_dq_exact_20of20_supported") is True]

    baseline = next((m for m in mode_summaries if m.get("mode") == "baseline_calwf3"), {})

    best_mode = None

    if literal_modes:

        best_mode = literal_modes[0].get("mode")

        decision = "literal_dq_exact_20of20_supported"

        decision_certificate = "dq_exactness_attack_literal_dq_exact_20of20_supported"

        claim_ceiling = "Literal DQ exactness is supported in at least one public replay mode. SCI/ERR/SAMP/TIME/DQ exact 20/20."

    elif baseline.get("science_exact_all_roots") and baseline.get("all_dq_mismatch_roots_xor_only_4096") and baseline.get("no_science_overlap"):

        decision = "literal_dq_exact_still_blocked_by_4096_bitplane_provenance"

        decision_certificate = "dq_exactness_attack_literal_dq_exact_blocked_4096_provenance_only_no_science_overlap"

        claim_ceiling = (

            "Literal DQ exact 20/20 remains blocked. The blocker is isolated to DQ XOR=4096 with no science-array overlap. "

            "This supports provenance but not literal DQ exactness."

        )

    else:

        decision = "literal_dq_exact_blocked_by_non4096_or_science_overlap_or_incomplete"

        decision_certificate = "dq_exactness_attack_literal_dq_exact_blocked_non4096_or_incomplete"

        claim_ceiling = "Literal DQ exact 20/20 is blocked by non-4096, science-overlap, missing products, or incomplete replay."

    result = {

        "timestamp_utc": now_utc(),

        "script": "scripts/dq_exactness_attack.py",

        "expected_root_count": args.expected_root_count,

        "stenv_release": args.stenv_release,

        "crds_context": args.crds_context,

        "package_dir": args.package_dir,

        "baseline_generated_dir": args.baseline_generated_dir,

        "astrodrizzle_generated_dir": args.astrodrizzle_generated_dir,

        "mode_summaries": mode_summaries,

        "best_literal_dq_exact_mode": best_mode,

        "decision": decision,

        "decision_certificate": decision_certificate,

        "claim_ceiling": claim_ceiling,

        "forbidden_claims": [

            "full public dark-matter closure from this DQ exactness run alone",

            "raw-to-drizzled HLSP reconstruction unless a separate raw-to-drizzled gate passes",

            "literal DQ exactness if no mode reaches DQ 20/20",

            "semantic evidence as closure evidence",

        ],

    }

    write_json(os.path.join(args.output_dir, "dq_exactness_attack_summary.json"), result)

    write_json(os.path.join(args.output_dir, "dq_exactness_attack_decision.json"), {

        "timestamp_utc": now_utc(),

        "decision": decision,

        "decision_certificate": decision_certificate,

        "best_literal_dq_exact_mode": best_mode,

        "claim_ceiling": claim_ceiling,

    })

    write_csv(

        os.path.join(args.output_dir, "dq_exactness_by_root.csv"),

        all_root_rows,

        [

            "mode", "root",

            "SCI_exact", "ERR_exact", "SAMP_exact", "TIME_exact", "DQ_exact",

            "DQ_mismatch_pixel_count", "DQ_total_pixel_count", "DQ_mismatch_fraction",

            "DQ_xor_values", "DQ_xor_histogram", "DQ_xor_only_4096",

            "ref_has_4096_gen_not_count", "gen_has_4096_ref_not_count",

            "both_have_4096_but_other_diff_count", "neither_has_4096_but_diff_count",

            "science_diff_overlap_DQ_mismatch",

            "SCI_changed_pixel_count", "ERR_changed_pixel_count", "SAMP_changed_pixel_count", "TIME_changed_pixel_count", "DQ_changed_pixel_count",

            "SCI_max_abs_diff", "ERR_max_abs_diff", "SAMP_max_abs_diff", "TIME_max_abs_diff", "DQ_max_abs_diff",

            "reference_path", "generated_path",

        ],

    )

    write_csv(

        os.path.join(args.output_dir, "dq_4096_direction_by_root.csv"),

        [

            {

                "mode": r.get("mode"),

                "root": r.get("root"),

                "DQ_exact": r.get("DQ_exact"),

                "DQ_xor_values": r.get("DQ_xor_values"),

                "DQ_xor_only_4096": r.get("DQ_xor_only_4096"),

                "ref_has_4096_gen_not_count": r.get("ref_has_4096_gen_not_count"),

                "gen_has_4096_ref_not_count": r.get("gen_has_4096_ref_not_count"),

                "both_have_4096_but_other_diff_count": r.get("both_have_4096_but_other_diff_count"),

                "neither_has_4096_but_diff_count": r.get("neither_has_4096_but_diff_count"),

            }

            for r in all_root_rows

        ],

        [

            "mode", "root", "DQ_exact", "DQ_xor_values", "DQ_xor_only_4096",

            "ref_has_4096_gen_not_count", "gen_has_4096_ref_not_count",

            "both_have_4096_but_other_diff_count", "neither_has_4096_but_diff_count",

        ],

    )

    write_csv(

        os.path.join(args.output_dir, "dq_exactness_by_bit.csv"),

        all_bit_rows,

        ["mode", "root", "bit_index", "bit_value", "changed_pixel_count", "changed_pixel_fraction"],

    )

    write_csv(

        os.path.join(args.output_dir, "science_overlap_audit.csv"),

        all_overlap_rows,

        [

            "mode", "root", "extension", "science_exact", "dq_mismatch_pixels",

            "science_diff_pixels_total", "science_diff_pixels_overlap_dq_mismatch",

            "overlap_fraction_of_dq_mismatch", "overlap_fraction_of_science_diff",

        ],

    )

    print(json.dumps(result, indent=2, sort_keys=True))

    if decision == "literal_dq_exact_20of20_supported":

        return 0

    if decision == "literal_dq_exact_still_blocked_by_4096_bitplane_provenance":

        return 3

    return 4

if __name__ == "__main__":

    sys.exit(main())

