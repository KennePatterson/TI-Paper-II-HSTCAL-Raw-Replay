#!/usr/bin/env python3

import argparse

import json

import os

import shutil

from datetime import datetime, timezone

from pathlib import Path

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

def list_fits(root):

    out = []

    if not os.path.exists(root):

        return out

    for dirpath, _, filenames in os.walk(root):

        for fn in filenames:

            low = fn.lower()

            if low.endswith("_flt.fits") or low.endswith("_flc.fits"):

                out.append(os.path.join(dirpath, fn))

    return sorted(out)

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument("--generated-dir", required=True)

    ap.add_argument("--output-dir", required=True)

    args = ap.parse_args()

    out_dir = args.output_dir

    work_dir = os.path.join(out_dir, "astrodrizzle_dq_update_work")

    candidate_dir = os.path.join(out_dir, "generated_after_astrodrizzle_dq")

    log_summary_path = os.path.join(out_dir, "astrodrizzle_update_attempt_summary.json")

    if os.path.exists(work_dir):

        shutil.rmtree(work_dir)

    if os.path.exists(candidate_dir):

        shutil.rmtree(candidate_dir)

    mkdirp(work_dir)

    mkdirp(candidate_dir)

    input_files = list_fits(args.generated_dir)

    copied_files = []

    for p in input_files:

        dst = os.path.join(work_dir, os.path.basename(p))

        shutil.copy2(p, dst)

        copied_files.append(dst)

    summary = {

        "timestamp_utc": now_utc(),

        "script": "scripts/run_astrodrizzle_dq_update_attempt.py",

        "generated_dir": args.generated_dir,

        "output_dir": out_dir,

        "work_dir": work_dir,

        "candidate_dir": candidate_dir,

        "input_file_count": len(input_files),

        "copied_file_count": len(copied_files),

        "astrodrizzle_import_ok": False,

        "astrodrizzle_run_attempted": False,

        "astrodrizzle_run_ok": False,

        "error": None,

    }

    try:

        from drizzlepac import astrodrizzle

        summary["astrodrizzle_import_ok"] = True

    except Exception as e:

        summary["error"] = "drizzlepac import failed: " + repr(e)

        for p in copied_files:

            shutil.copy2(p, os.path.join(candidate_dir, os.path.basename(p)))

        write_json(log_summary_path, summary)

        return 2

    try:

        os.chdir(work_dir)

        input_string = ",".join([os.path.basename(p) for p in copied_files])

        summary["astrodrizzle_run_attempted"] = True

        summary["input_string"] = input_string

        astrodrizzle.AstroDrizzle(

            input=input_string,

            output="dq_exactness_drizzle_trial",

            build=True,

            clean=True,

            preserve=False,

            skysub=True,

            driz_cr=True,

            driz_cr_corr=True,

            final_wcs=True,

        )

        summary["astrodrizzle_run_ok"] = True

    except Exception as e:

        summary["error"] = "astrodrizzle run failed: " + repr(e)

    for p in list_fits(work_dir):

        # Copy possibly updated FLT/FLC inputs and any relevant products, but exactness

        # script will only consume FLT/FLC by root name.

        shutil.copy2(p, os.path.join(candidate_dir, os.path.basename(p)))

    summary["candidate_file_count"] = len(list_fits(candidate_dir))

    write_json(log_summary_path, summary)

    return 0 if summary["astrodrizzle_run_ok"] else 3

if __name__ == "__main__":

    raise SystemExit(main())

