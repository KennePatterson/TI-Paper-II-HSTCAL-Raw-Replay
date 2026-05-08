#!/usr/bin/env bash
set -euo pipefail

RAW_PACKAGE_ZIP="${1:-TI_PAPER_II_TRUE_SCIENCE_RAW_EXPOSURE_SET_REPLAY_PACKAGE_UPDATED.zip}"
WORK_ROOT="${2:-ti_hstcal_replay_work}"
STENV_RELEASE="${STENV_RELEASE:-2026.04.14}"
STENV_YAML_FILENAME="${STENV_YAML_FILENAME:-}"
STENV_YAML_SHA256="${STENV_YAML_SHA256:-}"

echo "TI Paper II HSTCAL raw-to-FLT/FLC replay runner"
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

PACKAGE_DIR="$(find "${WORK_ROOT}/package_extract" -maxdepth 4 -type f -name true_science_input_manifest.json -printf '%h\n' | head -n 1 || true)"
if [[ -z "${PACKAGE_DIR}" || ! -d "${PACKAGE_DIR}" ]]; then
  PACKAGE_DIR="$(find "${WORK_ROOT}/package_extract" -maxdepth 4 -type d -name data -printf '%h\n' | head -n 1 || true)"
fi
if [[ -z "${PACKAGE_DIR}" || ! -d "${PACKAGE_DIR}" ]]; then
  echo "ERROR: could not locate extracted true-science package directory." >&2
  find "${WORK_ROOT}/package_extract" -maxdepth 5 -type f | head -200 || true
  exit 3
fi

RESULTS_DIR="${WORK_ROOT}/results"
GENERATED_DIR="${RESULTS_DIR}/generated"
LOG_DIR="${RESULTS_DIR}/logs"
mkdir -p "${RESULTS_DIR}" "${GENERATED_DIR}" "${LOG_DIR}"

MASTER_LOG="${LOG_DIR}/calwf3_command_log.txt"

{
  echo "Package dir: ${PACKAGE_DIR}"
  echo "Host: $(uname -a || true)"
  echo "Python: $(python --version || true)"
  echo "Date UTC: $(date -u)"
  echo ""

  echo "Package inventory:"
  find "${PACKAGE_DIR}" -maxdepth 4 -type f | sort | sed 's#^#  #'
  echo ""

  echo "Raw files:"
  find "${PACKAGE_DIR}/data/raw" -maxdepth 1 -type f -name '*_raw.fits*' -print | sort || true
  echo ""

  echo "Reference calibrated files:"
  find "${PACKAGE_DIR}/data/calibrated_reference" -maxdepth 1 -type f \( -name '*_flt.fits*' -o -name '*_flc.fits*' \) -print | sort || true
  echo ""

  # Prefer calwf3.e, but also keep calwf3 as a fallback.
  CAL_EXES=()
  if command -v calwf3.e >/dev/null 2>&1; then
    CAL_EXES+=("calwf3.e")
  fi
  if command -v calwf3 >/dev/null 2>&1; then
    CAL_EXES+=("calwf3")
  fi

  if [[ "${#CAL_EXES[@]}" -eq 0 ]]; then
    echo "ERROR: no calwf3.e or calwf3 executable found on PATH." >&2
    exit 4
  fi

  echo "Detected calibration executables:"
  for exe in "${CAL_EXES[@]}"; do
    echo "  ${exe}: $(command -v "${exe}")"
    "${exe}" --version || "${exe}" -r || true
  done

  # Critical repair: default to the CRDS context used by the packaged references.
  # First run used current/default context hst_1328.pmap; package headers show hst_1313.pmap.
  export CRDS_SERVER_URL="${CRDS_SERVER_URL:-https://hst-crds.stsci.edu}"
  export CRDS_PATH="${CRDS_PATH:-${PWD}/crds_cache}"
  export CRDS_CONTEXT="${CRDS_CONTEXT:-hst_1313.pmap}"
  export iref="${iref:-${CRDS_PATH}/references/hst/iref/}"

  mkdir -p "${CRDS_PATH}" "${iref}"

  # Make native threaded libraries conservative in CI.
  export OMP_NUM_THREADS=1
  export OPENBLAS_NUM_THREADS=1
  export MKL_NUM_THREADS=1
  export NUMEXPR_NUM_THREADS=1
  ulimit -c 0 || true

  echo ""
  echo "CRDS_SERVER_URL=${CRDS_SERVER_URL}"
  echo "CRDS_PATH=${CRDS_PATH}"
  echo "CRDS_CONTEXT=${CRDS_CONTEXT}"
  echo "iref=${iref}"
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

  echo ""
  echo "Starting per-root calwf3 repair replay..."

  shopt -s nullglob
  RAW_FILES=( "${PACKAGE_DIR}"/data/raw/*_raw.fits "${PACKAGE_DIR}"/data/raw/*_raw.fits.gz )

  if [[ "${#RAW_FILES[@]}" -eq 0 ]]; then
    echo "ERROR: no raw FITS files found under ${PACKAGE_DIR}/data/raw" >&2
    exit 5
  fi

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

    SUCCESS=0

    # Modes:
    # 1. bestrefs_context_plain: hst_1313 context + plain calwf3 call
    # 2. no_bestrefs_plain: use package header refs as-is, plain call
    # 3. bestrefs_context_vt: hst_1313 context + -vt
    # 4. no_bestrefs_vt: header refs as-is + -vt
    #
    # The first failed run crashed in mode like current-context + -vt.
    # We deliberately try plain calls first.
    MODES=(
      "bestrefs_context_plain"
      "no_bestrefs_plain"
      "bestrefs_context_vt"
      "no_bestrefs_vt"
    )

    for MODE in "${MODES[@]}"; do
      if [[ "${SUCCESS}" -eq 1 ]]; then
        break
      fi

      for CAL_EXE in "${CAL_EXES[@]}"; do
        if [[ "${SUCCESS}" -eq 1 ]]; then
          break
        fi

        WORK_DIR="${ROOT_RESULT_DIR}/${MODE}_${CAL_EXE}"
        rm -rf "${WORK_DIR}"
        mkdir -p "${WORK_DIR}"
        cp -p "${RAW}" "${WORK_DIR}/${ROOT}_raw.fits"

        echo ""
        echo "---- Attempt MODE=${MODE} EXE=${CAL_EXE} ----"
        echo "Work dir: ${WORK_DIR}"

        pushd "${WORK_DIR}" >/dev/null

        if [[ "${MODE}" == bestrefs_context_* ]]; then
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
        if [[ "${MODE}" == *_vt ]]; then
          echo "Running ${CAL_EXE} -vt ${ROOT}_raw.fits"
          "${CAL_EXE}" -vt "${ROOT}_raw.fits"
        else
          echo "Running ${CAL_EXE} ${ROOT}_raw.fits"
          "${CAL_EXE}" "${ROOT}_raw.fits"
        fi
        STATUS=$?
        set -e

        echo "HSTCAL status for ${ROOT} mode=${MODE} exe=${CAL_EXE}: ${STATUS}"
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
          SUCCESS=1
        else
          echo "No generated FLT/FLC output for ${ROOT} in mode=${MODE} exe=${CAL_EXE}"
        fi

        popd >/dev/null
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
    --package-dir "${PACKAGE_DIR}" \
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
