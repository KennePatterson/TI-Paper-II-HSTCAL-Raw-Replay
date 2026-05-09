#!/usr/bin/env bash
set -euo pipefail

RAW_PACKAGE_ZIP="${1:-TI_PAPER_II_TRUE_SCIENCE_RAW_EXPOSURE_SET_REPLAY_PACKAGE_UPDATED.zip}"
WORK_ROOT="${2:-ti_hstcal_replay_work}"
STENV_RELEASE="${STENV_RELEASE:-2026.04.14}"
STENV_YAML_FILENAME="${STENV_YAML_FILENAME:-}"
STENV_YAML_SHA256="${STENV_YAML_SHA256:-}"

echo "TI Paper II HSTCAL crash-forensics raw-to-FLT/FLC replay runner"
echo "UTC: $(date -u)"
echo "Raw package ZIP: ${RAW_PACKAGE_ZIP}"
echo "Work root: ${WORK_ROOT}"

if [[ ! -f "${RAW_PACKAGE_ZIP}" ]]; then
  echo "ERROR: raw package ZIP not found: ${RAW_PACKAGE_ZIP}" >&2
  exit 2
fi

rm -rf "${WORK_ROOT}"
mkdir -p "${WORK_ROOT}"
unzip -q "${RAW_PACKAGE_ZIP}" -d "${WORK_ROOT}/package_extract"

PACKAGE_DIR="$(find "${WORK_ROOT}/package_extract" -maxdepth 5 -type f -name true_science_input_manifest.json -printf '%h\n' | head -n 1 || true)"
if [[ -z "${PACKAGE_DIR}" || ! -d "${PACKAGE_DIR}" ]]; then
  PACKAGE_DIR="$(find "${WORK_ROOT}/package_extract" -maxdepth 5 -type d -name data -printf '%h\n' | head -n 1 || true)"
fi
if [[ -z "${PACKAGE_DIR}" || ! -d "${PACKAGE_DIR}" ]]; then
  echo "ERROR: could not locate extracted true-science package directory." >&2
  find "${WORK_ROOT}/package_extract" -maxdepth 5 -type f | head -300 || true
  exit 3
fi

RESULTS_DIR="${WORK_ROOT}/results"
GENERATED_DIR="${RESULTS_DIR}/generated"
LOG_DIR="${RESULTS_DIR}/logs"
NORMALIZED_DIR="${RESULTS_DIR}/normalized_package"
NORMALIZED_RAW_DIR="${NORMALIZED_DIR}/data/raw"
NORMALIZED_CAL_DIR="${NORMALIZED_DIR}/data/calibrated_reference"

mkdir -p "${RESULTS_DIR}" "${GENERATED_DIR}" "${LOG_DIR}" "${NORMALIZED_RAW_DIR}" "${NORMALIZED_CAL_DIR}"

MASTER_LOG="${LOG_DIR}/calwf3_command_log.txt"

{
  echo "Package dir: ${PACKAGE_DIR}"
  echo "Host: $(uname -a || true)"
  echo "Python: $(python --version || true)"
  echo "Date UTC: $(date -u)"
  echo ""

  echo "Original package inventory:"
  find "${PACKAGE_DIR}" -maxdepth 6 -type f | sort | sed 's#^#  #' || true
  echo ""

  echo "Normalizing mixed package layout..."
  find "${PACKAGE_DIR}" -type f -name '*_raw.fits' -print0 | while IFS= read -r -d '' RAW_FILE; do
    cp -p "${RAW_FILE}" "${NORMALIZED_RAW_DIR}/$(basename "${RAW_FILE}")"
  done

  find "${PACKAGE_DIR}" -type f \( -name '*_flt.fits' -o -name '*_flc.fits' \) -print0 | while IFS= read -r -d '' CAL_FILE; do
    cp -p "${CAL_FILE}" "${NORMALIZED_CAL_DIR}/$(basename "${CAL_FILE}")"
  done

  echo ""
  echo "Normalized raw files:"
  find "${NORMALIZED_RAW_DIR}" -maxdepth 1 -type f -name '*_raw.fits*' -print | sort || true
  echo ""

  echo "Normalized calibrated reference files:"
  find "${NORMALIZED_CAL_DIR}" -maxdepth 1 -type f \( -name '*_flt.fits*' -o -name '*_flc.fits*' \) -print | sort || true
  echo ""

  RAW_COUNT="$(find "${NORMALIZED_RAW_DIR}" -maxdepth 1 -type f -name '*_raw.fits*' | wc -l | tr -d ' ')"
  CAL_COUNT="$(find "${NORMALIZED_CAL_DIR}" -maxdepth 1 -type f \( -name '*_flt.fits*' -o -name '*_flc.fits*' \) | wc -l | tr -d ' ')"

  echo "Normalized raw count: ${RAW_COUNT}"
  echo "Normalized calibrated reference count: ${CAL_COUNT}"

  if [[ "${RAW_COUNT}" -eq 0 ]]; then
    echo "ERROR: no raw FITS files found after normalization." >&2
    exit 5
  fi

  if [[ "${CAL_COUNT}" -eq 0 ]]; then
    echo "ERROR: no calibrated reference FITS files found after normalization." >&2
    exit 6
  fi

  CAL_EXES=()
  if command -v calwf3.e >/dev/null 2>&1; then
    CAL_EXES+=("calwf3.e")
  fi
  if command -v calwf3 >/dev/null 2>&1; then
    CAL_EXES+=("calwf3")
  fi

  if [[ "${#CAL_EXES[@]}" -eq 0 ]]; then
    echo "ERROR: no calwf3.e or calwf3 executable found on PATH." >&2
    exit 7
  fi

  echo ""
  echo "Detected calibration executables:"
  for exe in "${CAL_EXES[@]}"; do
    echo "  ${exe}: $(command -v "${exe}")"
    "${exe}" --version || "${exe}" -r || true
    echo "ldd for ${exe}:"
    ldd "$(command -v "${exe}")" || true
  done

  export CRDS_SERVER_URL="${CRDS_SERVER_URL:-https://hst-crds.stsci.edu}"
  export CRDS_PATH="${CRDS_PATH:-${PWD}/crds_cache}"
  export CRDS_CONTEXT="${CRDS_CONTEXT:-hst_1313.pmap}"

  IREF_CANDIDATES=(
    "${CRDS_PATH}/references/hst/wfc3/"
    "${CRDS_PATH}/references/hst/iref/"
  )

  export OMP_NUM_THREADS=1
  export OPENBLAS_NUM_THREADS=1
  export MKL_NUM_THREADS=1
  export NUMEXPR_NUM_THREADS=1
  ulimit -c 0 || true

  echo ""
  echo "CRDS_SERVER_URL=${CRDS_SERVER_URL}"
  echo "CRDS_PATH=${CRDS_PATH}"
  echo "CRDS_CONTEXT=${CRDS_CONTEXT}"
  echo ""

  if command -v crds >/dev/null 2>&1; then
    crds list --status || true
  else
    echo "WARNING: crds command not found; replay may fail."
  fi

  echo ""
  echo "Raw package SHA256:"
  sha256sum "${RAW_PACKAGE_ZIP}" || true
  export TI_RAW_PACKAGE_ZIP="$(cd "$(dirname "${RAW_PACKAGE_ZIP}")" && pwd)/$(basename "${RAW_PACKAGE_ZIP}")"

  shopt -s nullglob
  RAW_FILES=( "${NORMALIZED_RAW_DIR}"/*_raw.fits "${NORMALIZED_RAW_DIR}"/*_raw.fits.gz )

  echo ""
  echo "Starting per-root crash-forensics replay..."
  echo "Will process ${#RAW_FILES[@]} normalized raw files."

  for RAW in "${RAW_FILES[@]}"; do
    BASE="$(basename "${RAW}")"
    ROOT="${BASE%%_raw.fits}"
    ROOT="${ROOT%%_raw.fits.gz}"
    ROOT_RESULT_DIR="${GENERATED_DIR}/${ROOT}"
    mkdir -p "${ROOT_RESULT_DIR}"

    echo ""
    echo "================================================================================"
    echo "ROOT ${ROOT}"
    echo "================================================================================"
    echo "Raw: ${RAW}"
    echo "Root result dir: ${ROOT_RESULT_DIR}"

    python - <<PY || true
from astropy.io import fits
p = r"${RAW}"
keys = ["ROOTNAME","ASN_ID","ASN_TAB","PROPOSID","INSTRUME","DETECTOR","FILTER","FILTER1","FILTER2","TARGNAME","EXPSTART","EXPEND","EXPTIME","CRDS_CTX","CAL_VER","DARKFILE","NLINFILE","PFLTFILE","DFLTFILE","LFLTFILE","BPIXTAB","IDCTAB","NPOLFILE","D2IMFILE"]
print("HEADER PREFLIGHT", p)
with fits.open(p, memmap=False) as hdul:
    print("HDU count", len(hdul))
    for k in keys:
        for h in hdul:
            if k in h.header:
                print(f"{k}={h.header.get(k)}")
                break
PY

    SUCCESS=0
    MODES=(
      "bestrefs_context_plain"
      "no_bestrefs_plain"
      "bestrefs_context_vt"
      "no_bestrefs_vt"
      "gdb_bestrefs_plain"
    )

    for IREF_VALUE in "${IREF_CANDIDATES[@]}"; do
      if [[ "${SUCCESS}" -eq 1 ]]; then
        break
      fi

      export iref="${IREF_VALUE}"
      mkdir -p "${iref}"
      echo "Trying iref=${iref}"

      for MODE in "${MODES[@]}"; do
        if [[ "${SUCCESS}" -eq 1 ]]; then
          break
        fi

        for CAL_EXE in "${CAL_EXES[@]}"; do
          if [[ "${SUCCESS}" -eq 1 ]]; then
            break
          fi

          SAFE_EXE="${CAL_EXE//./_}"
          SAFE_IREF="$(echo "${IREF_VALUE}" | sed 's#[/:]#_#g')"
          WORK_DIR="${ROOT_RESULT_DIR}/${MODE}_${SAFE_EXE}_${SAFE_IREF}"
          rm -rf "${WORK_DIR}"
          mkdir -p "${WORK_DIR}"
          cp -p "${RAW}" "${WORK_DIR}/${ROOT}_raw.fits"

          echo ""
          echo "---- Attempt ROOT=${ROOT} IREF=${iref} MODE=${MODE} EXE=${CAL_EXE} ----"
          echo "Work dir: ${WORK_DIR}"

          pushd "${WORK_DIR}" >/dev/null

          if [[ "${MODE}" == bestrefs_context_* || "${MODE}" == gdb_bestrefs_plain ]]; then
            if command -v crds >/dev/null 2>&1; then
              echo "Running CRDS bestrefs with CRDS_CONTEXT=${CRDS_CONTEXT} for ${ROOT}..."
              crds bestrefs --files "${ROOT}_raw.fits" --new-context "${CRDS_CONTEXT}" --sync-references=1 --update-bestrefs || true
            else
              echo "CRDS command not found; skipping bestrefs."
            fi
          else
            echo "Skipping bestrefs; using raw header reference keywords as packaged."
          fi

          set +e
          if [[ "${MODE}" == gdb_bestrefs_plain && -x "$(command -v gdb || true)" ]]; then
            echo "Running gdb backtrace: ${CAL_EXE} ${ROOT}_raw.fits"
            gdb -batch -ex run -ex bt --args "$(command -v "${CAL_EXE}")" "${ROOT}_raw.fits"
            STATUS=$?
          elif [[ "${MODE}" == *_vt ]]; then
            echo "Running ${CAL_EXE} -vt ${ROOT}_raw.fits"
            "${CAL_EXE}" -vt "${ROOT}_raw.fits"
            STATUS=$?
          else
            echo "Running ${CAL_EXE} ${ROOT}_raw.fits"
            "${CAL_EXE}" "${ROOT}_raw.fits"
            STATUS=$?
          fi
          set -e

          echo "HSTCAL status for ${ROOT} iref=${iref} mode=${MODE} exe=${CAL_EXE}: ${STATUS}"
          ls -lah || true

          FOUND_OUTPUT=""
          if [[ -f "${ROOT}_flt.fits" ]]; then
            FOUND_OUTPUT="${WORK_DIR}/${ROOT}_flt.fits"
          elif [[ -f "${ROOT}_flc.fits" ]]; then
            FOUND_OUTPUT="${WORK_DIR}/${ROOT}_flc.fits"
          else
            CAND="$(ls *_flt.fits *_flc.fits 2>/dev/null | head -n 1 || true)"
            if [[ -n "${CAND}" && -f "${CAND}" ]]; then
              FOUND_OUTPUT="${WORK_DIR}/${CAND}"
            fi
          fi

          if [[ -n "${FOUND_OUTPUT}" && -f "${FOUND_OUTPUT}" ]]; then
            echo "SUCCESS: generated calibrated output ${FOUND_OUTPUT}"
            cp -p "${FOUND_OUTPUT}" "${ROOT_RESULT_DIR}/$(basename "${FOUND_OUTPUT}")"
            echo "${MODE}" > "${ROOT_RESULT_DIR}/winning_mode.txt"
            echo "${CAL_EXE}" > "${ROOT_RESULT_DIR}/winning_executable.txt"
            echo "${iref}" > "${ROOT_RESULT_DIR}/winning_iref.txt"
            SUCCESS=1
          else
            echo "No generated FLT/FLC output for ${ROOT} in mode=${MODE} exe=${CAL_EXE} iref=${iref}"
          fi

          popd >/dev/null
        done
      done
    done

    if [[ "${SUCCESS}" -ne 1 ]]; then
      echo "ROOT ${ROOT}: all HSTCAL attempts failed to produce FLT/FLC."
    fi
  done

  echo ""
  echo "Generated product inventory:"
  find "${GENERATED_DIR}" -type f | sort | sed 's#^#  #' || true

  echo ""
  echo "Running strict validator..."
  python scripts/validate_raw_to_flt.py \
    --package-dir "${NORMALIZED_DIR}" \
    --generated-dir "${GENERATED_DIR}" \
    --output-json "${RESULTS_DIR}/validation_report.json" \
    --stenv-release "${STENV_RELEASE}" \
    --stenv-yaml-filename "${STENV_YAML_FILENAME}" \
    --stenv-yaml-sha256 "${STENV_YAML_SHA256}" \
    --calwf3-executable "${CAL_EXES[0]}" \
    --calwf3-command-log "${MASTER_LOG}"

  echo ""
  echo "Validation report:"
  cat "${RESULTS_DIR}/validation_report.json"

} 2>&1 | tee "${MASTER_LOG}"

echo ""
echo "DONE. Upload or return:"
echo "${RESULTS_DIR}/validation_report.json"
echo "${MASTER_LOG}"
