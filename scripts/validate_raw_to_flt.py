#!/usr/bin/env python3
"""
TI Paper II strict raw-to-FLT/FLC validator.

This validator compares generated HSTCAL FLT/FLC outputs to packaged public calibrated
reference products for the same raw roots.

Claim ceiling:
- Tier 1 raw-to-calibrated FLT/FLC detector replay only.
- No raw-to-drizzled HLSP reconstruction claim.
"""

import os
import json
import argparse
import hashlib
import platform
import subprocess
from datetime import datetime, timezone

import numpy as np
from astropy.io import fits


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def run_cmd(cmd):
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True,
            timeout=120,
        )
        return {
            "cmd": cmd,
            "returncode": p.returncode,
            "stdout_tail": (p.stdout or "")[-8000:],
        }
    except Exception as e:
        return {
            "cmd": cmd,
            "returncode": None,
            "stdout_tail": "",
            "error": repr(e),
        }


def root_from_filename(path):
    name = os.path.basename(str(path))
    low = name.lower()
    for suffix in [
        "_raw.fits",
        "_raw.fits.gz",
        "_flt.fits",
        "_flc.fits",
        "_flt.fits.gz",
        "_flc.fits.gz",
    ]:
        if low.endswith(suffix):
            return name[: len(name) - len(suffix)]
    return os.path.splitext(name)[0]


def first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def header_identity(path):
    keys = [
        "ROOTNAME",
        "ASN_ID",
        "ASN_TAB",
        "PROPOSID",
        "INSTRUME",
        "DETECTOR",
        "FILTER",
        "FILTER1",
        "FILTER2",
        "TARGNAME",
        "EXPSTART",
        "EXPEND",
        "EXPTIME",
        "DATE-OBS",
        "TIME-OBS",
        "CRDS_CTX",
        "CAL_VER",
        "DARKFILE",
        "NLINFILE",
        "PFLTFILE",
        "DFLTFILE",
        "LFLTFILE",
        "BPIXTAB",
        "IDCTAB",
        "NPOLFILE",
        "D2IMFILE",
    ]
    out = {}
    with fits.open(path, memmap=False) as hdul:
        for hdu in hdul:
            for k in keys:
                if k not in out and hdu.header.get(k) is not None:
                    v = hdu.header.get(k)
                    out[k] = str(v) if not isinstance(v, (int, float, bool)) else v
    return out


def numeric_hdus(path):
    rows = []
    with fits.open(path, memmap=False) as hdul:
        for i, hdu in enumerate(hdul):
            data = getattr(hdu, "data", None)
            if data is None:
                continue
            arr = np.asarray(data)
            if arr.size == 0 or not np.issubdtype(arr.dtype, np.number):
                continue
            extname = str(hdu.header.get("EXTNAME", f"HDU{i}"))
            extver = hdu.header.get("EXTVER")
            rows.append(
                {
                    "index": i,
                    "extname": extname,
                    "extver": int(extver) if isinstance(extver, (int, np.integer)) else extver,
                    "shape": list(arr.shape),
                    "dtype": str(arr.dtype),
                    "array": arr,
                }
            )
    return rows


def compare_products(generated_path, reference_path):
    generated_hdus = numeric_hdus(generated_path)
    reference_hdus = numeric_hdus(reference_path)

    hdu_results = []
    hard_failures = []

    for g in generated_hdus:
        candidates = [
            r
            for r in reference_hdus
            if r["extname"] == g["extname"]
            and r["extver"] == g["extver"]
            and tuple(r["shape"]) == tuple(g["shape"])
        ]

        if not candidates:
            candidates = [
                r
                for r in reference_hdus
                if r["extname"] == g["extname"]
                and tuple(r["shape"]) == tuple(g["shape"])
            ]

        if not candidates:
            hard_failures.append(
                {
                    "reason": "missing_matching_reference_hdu",
                    "generated_hdu": {
                        "index": g["index"],
                        "extname": g["extname"],
                        "extver": g["extver"],
                        "shape": g["shape"],
                    },
                }
            )
            continue

        r = candidates[0]
        ga = np.asarray(g["array"])
        ra = np.asarray(r["array"])

        if ga.shape != ra.shape:
            hard_failures.append(
                {
                    "reason": "shape_mismatch",
                    "extname": g["extname"],
                    "generated_shape": list(ga.shape),
                    "reference_shape": list(ra.shape),
                }
            )
            continue

        finite = np.isfinite(ga) & np.isfinite(ra)
        if finite.sum() == 0:
            hard_failures.append(
                {
                    "reason": "no_finite_overlap",
                    "extname": g["extname"],
                    "extver": g["extver"],
                }
            )
            continue

        gv = ga[finite].astype(float)
        rv = ra[finite].astype(float)
        diff = gv - rv
        absdiff = np.abs(diff)

        rmse = float(np.sqrt(np.mean(diff ** 2)))
        ref_rms = float(np.sqrt(np.mean(rv ** 2))) if np.mean(rv ** 2) > 0 else None
        relative_nrmse = float(rmse / ref_rms) if ref_rms and ref_rms > 0 else None

        denom = float(np.sqrt(np.dot(gv, gv)) * np.sqrt(np.dot(rv, rv)))
        weighted_cosine = float(np.dot(gv, rv) / denom) if denom > 0 else None

        exact_equal = bool(np.array_equal(ga, ra))
        near_machine_precision = bool(np.allclose(gv, rv, rtol=1e-7, atol=1e-7, equal_nan=True))

        scale = float(np.nanmedian(np.abs(rv))) + 1e-12
        changed_pixel_fraction = float(np.mean(absdiff > max(1e-7, 1e-6 * scale)))

        extname_upper = str(g["extname"]).upper()
        dq_like = extname_upper == "DQ"
        science_relevant = extname_upper in ["SCI", "ERR", "DQ"]

        if dq_like:
            current_context_stability_pass = exact_equal
        else:
            current_context_stability_pass = True
            if relative_nrmse is None or relative_nrmse > 0.01:
                current_context_stability_pass = False
            if weighted_cosine is None or weighted_cosine < 0.999:
                current_context_stability_pass = False
            if changed_pixel_fraction > 0.001:
                current_context_stability_pass = False

        hdu_results.append(
            {
                "generated_hdu": {
                    "index": g["index"],
                    "extname": g["extname"],
                    "extver": g["extver"],
                    "shape": g["shape"],
                },
                "reference_hdu": {
                    "index": r["index"],
                    "extname": r["extname"],
                    "extver": r["extver"],
                    "shape": r["shape"],
                },
                "finite_count": int(finite.sum()),
                "exact_equal": exact_equal,
                "near_machine_precision": near_machine_precision,
                "rmse": rmse,
                "reference_rms": ref_rms,
                "relative_nrmse": relative_nrmse,
                "weighted_cosine": weighted_cosine,
                "median_abs_diff": float(np.median(absdiff)),
                "p99_abs_diff": float(np.percentile(absdiff, 99)),
                "p999_abs_diff": float(np.percentile(absdiff, 99.9)),
                "max_abs_diff": float(np.max(absdiff)),
                "changed_pixel_fraction": changed_pixel_fraction,
                "dq_like": dq_like,
                "science_relevant": science_relevant,
                "current_context_stability_pass": bool(current_context_stability_pass),
            }
        )

    science_results = [x for x in hdu_results if x.get("science_relevant")]
    exact_context_pass = bool(
        science_results
        and all(x["exact_equal"] or x["near_machine_precision"] for x in science_results)
    )
    current_context_stability_pass = bool(
        science_results and all(x["current_context_stability_pass"] for x in science_results)
    )

    return {
        "hdu_results": hdu_results,
        "hard_failures": hard_failures,
        "exact_context_pass": exact_context_pass,
        "current_context_stability_pass": current_context_stability_pass,
        "pass": bool(not hard_failures and (exact_context_pass or current_context_stability_pass)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package-dir", required=True)
    ap.add_argument("--generated-dir", required=True)
    ap.add_argument("--output-json", required=True)
    ap.add_argument("--stenv-release", default="")
    ap.add_argument("--stenv-yaml-filename", default="")
    ap.add_argument("--stenv-yaml-sha256", default="")
    ap.add_argument("--calwf3-executable", default="")
    ap.add_argument("--calwf3-command-log", default="")
    args = ap.parse_args()

    package_dir = os.path.abspath(args.package_dir)
    generated_dir = os.path.abspath(args.generated_dir)
    output_json = os.path.abspath(args.output_json)

    raw_dir = os.path.join(package_dir, "data", "raw")
    ref_dir = os.path.join(package_dir, "data", "calibrated_reference")

    raw_files = []
    if os.path.isdir(raw_dir):
        for name in sorted(os.listdir(raw_dir)):
            if name.lower().endswith(("_raw.fits", "_raw.fits.gz")):
                raw_files.append(os.path.join(raw_dir, name))

    per_root_results = []
    hard_failures = []

    for raw_path in raw_files:
        root = root_from_filename(raw_path)

        generated_path = first_existing(
            [
                os.path.join(generated_dir, root, f"{root}_flt.fits"),
                os.path.join(generated_dir, root, f"{root}_flc.fits"),
                os.path.join(generated_dir, f"{root}_flt.fits"),
                os.path.join(generated_dir, f"{root}_flc.fits"),
            ]
        )

        reference_path = first_existing(
            [
                os.path.join(ref_dir, f"{root}_flt.fits"),
                os.path.join(ref_dir, f"{root}_flc.fits"),
            ]
        )

        rec = {
            "root": root,
            "raw": raw_path,
            "reference": reference_path,
            "generated": generated_path,
            "raw_sha256": sha256_file(raw_path) if os.path.isfile(raw_path) else None,
            "reference_sha256": sha256_file(reference_path) if reference_path and os.path.isfile(reference_path) else None,
            "generated_sha256": sha256_file(generated_path) if generated_path and os.path.isfile(generated_path) else None,
            "raw_header_identity": header_identity(raw_path) if os.path.isfile(raw_path) else None,
            "reference_header_identity": header_identity(reference_path) if reference_path and os.path.isfile(reference_path) else None,
            "generated_header_identity": header_identity(generated_path) if generated_path and os.path.isfile(generated_path) else None,
            "hard_failures": [],
            "pass": False,
        }

        if not reference_path:
            rec["hard_failures"].append("missing_public_calibrated_reference")
        if not generated_path:
            rec["hard_failures"].append("missing_generated_calibrated_product")

        if not rec["hard_failures"]:
            comp = compare_products(generated_path, reference_path)
            rec["comparison"] = comp
            rec["exact_context_pass"] = comp["exact_context_pass"]
            rec["current_context_stability_pass"] = comp["current_context_stability_pass"]
            rec["pass"] = comp["pass"]
            rec["hard_failures"].extend(comp["hard_failures"])

            gen_root = str((rec["generated_header_identity"] or {}).get("ROOTNAME", "")).lower()
            ref_root = str((rec["reference_header_identity"] or {}).get("ROOTNAME", "")).lower()
            if gen_root and ref_root and gen_root != ref_root:
                rec["hard_failures"].append(
                    {
                        "reason": "generated_reference_rootname_mismatch",
                        "generated": gen_root,
                        "reference": ref_root,
                    }
                )
                rec["pass"] = False

        if rec["hard_failures"]:
            hard_failures.append({"root": root, "hard_failures": rec["hard_failures"]})

        per_root_results.append(rec)

    processed = len(per_root_results)
    passed = sum(1 for r in per_root_results if r.get("pass") is True)
    failed = processed - passed

    environment_audit = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "calwf3_executable": args.calwf3_executable,
        "crds_server_url": os.environ.get("CRDS_SERVER_URL"),
        "crds_path": os.environ.get("CRDS_PATH"),
        "crds_context_effective": os.environ.get("CRDS_CONTEXT"),
        "iref_value": os.environ.get("iref"),
        "crds_status": run_cmd("crds list --status || true"),
        "calwf3_version_probe": run_cmd(f"{args.calwf3_executable} --version || {args.calwf3_executable} -r || true")
        if args.calwf3_executable
        else None,
    }

    report = {
        "timestamp_utc": now_iso(),
        "stage_identity": "external_hstcal_stenv_raw_to_flt_replay",
        "runner_identity": "TI Paper II GitHub Actions / official stenv runner",
        "stenv_release": args.stenv_release,
        "stenv_yaml_filename": args.stenv_yaml_filename,
        "stenv_yaml_sha256": args.stenv_yaml_sha256,
        "python_version": platform.python_version(),
        "hstcal_version": environment_audit["calwf3_version_probe"],
        "calwf3_executable": args.calwf3_executable,
        "crds_version": run_cmd("python -c \"import crds; print(getattr(crds, '__version__', 'unknown'))\" || true"),
        "crds_server_url": os.environ.get("CRDS_SERVER_URL"),
        "crds_context_effective": os.environ.get("CRDS_CONTEXT"),
        "iref_value": os.environ.get("iref"),
        "package_input_sha256": sha256_file(os.environ.get("TI_RAW_PACKAGE_ZIP", ""))
        if os.environ.get("TI_RAW_PACKAGE_ZIP") and os.path.isfile(os.environ.get("TI_RAW_PACKAGE_ZIP"))
        else None,
        "raw_root_count_expected": len(raw_files),
        "raw_root_count_processed": processed,
        "per_root_results": per_root_results,
        "aggregate_results": {
            "raw_root_count_expected": len(raw_files),
            "raw_root_count_processed": processed,
            "passed_root_count": passed,
            "failed_root_count": failed,
            "all_roots_pass": bool(processed > 0 and failed == 0),
            "claim_tier_supported": "tier1_raw_to_flt" if processed > 0 and failed == 0 else "none",
            "hard_failures": hard_failures,
        },
        "hard_failures": hard_failures,
        "environment_audit": environment_audit,
        "claim_tier_supported": "tier1_raw_to_flt" if processed > 0 and failed == 0 else "none",
        "claim_ceiling": "Tier 1 raw-to-calibrated FLT/FLC detector replay only; no raw-to-drizzled HLSP reconstruction claim.",
        "pass": bool(processed > 0 and failed == 0),
    }

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    raise SystemExit(0 if report["pass"] else 20)


if __name__ == "__main__":
    main()
