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
  find "${WORK_ROOT}/package_extract" -maxdepth 4 -type f | head -100 || true
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

  CAL_EXE=""
  for exe in calwf3.e calwf3; do
    if command -v "${exe}" >/dev/null 2>&1; then
      CAL_EXE="${exe}"
      echo "Found WFC3 calibration executable: ${CAL_EXE}"
      break
    fi
  done

  if [[ -z "${CAL_EXE}" ]]; then
    echo "ERROR: no calwf3.e or calwf3 executable found on PATH." >&2
    exit 4
  fi

  export CRDS_SERVER_URL="${CRDS_SERVER_URL:-https://hst-crds.stsci.edu}"
  export CRDS_PATH="${CRDS_PATH:-${PWD}/crds_cache}"
  export iref="${iref:-${CRDS_PATH}/references/hst/iref/}"
  mkdir -p "${CRDS_PATH}" "${iref}"

  echo "CRDS_SERVER_URL=${CRDS_SERVER_URL}"
  echo "CRDS_PATH=${CRDS_PATH}"
  echo "CRDS_CONTEXT=${CRDS_CONTEXT:-}"
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
  echo "Starting per-root calwf3 replay..."

  shopt -s nullglob
  for RAW in "${PACKAGE_DIR}"/data/raw/*_raw.fits "${PACKAGE_DIR}"/data/raw/*_raw.fits.gz; do
    BASE="$(basename "${RAW}")"
    ROOT="${BASE%%_raw.fits}"
    ROOT="${ROOT%%_raw.fits.gz}"
    WORK_DIR="${GENERATED_DIR}/${ROOT}"
    mkdir -p "${WORK_DIR}"
    cp -p "${RAW}" "${WORK_DIR}/${ROOT}_raw.fits"

    echo ""
    echo "================================================================================"
    echo "ROOT ${ROOT}"
    echo "================================================================================"
    echo "Raw: ${RAW}"
    echo "Work dir: ${WORK_DIR}"

    pushd "${WORK_DIR}" >/dev/null

    if command -v crds >/dev/null 2>&1; then
      echo "Running CRDS bestrefs for ${ROOT}..."
      crds bestrefs --files "${ROOT}_raw.fits" --sync-references=1 --update-bestrefs || true
    fi

    echo "Running ${CAL_EXE} -vt ${ROOT}_raw.fits"
    set +e
    "${CAL_EXE}" -vt "${ROOT}_raw.fits"
    STATUS=$?
    set -e
    echo "calwf3 status for ${ROOT}: ${STATUS}"

    if [[ "${STATUS}" -ne 0 ]]; then
      echo "WARNING: calwf3 nonzero status for ${ROOT}"
    fi

    ls -lah || true
    popd >/dev/null
  done

  echo ""
  echo "Running strict validator..."
  python scripts/validate_raw_to_flt.py \
    --package-dir "${PACKAGE_DIR}" \
    --generated-dir "${GENERATED_DIR}" \
    --output-json "${RESULTS_DIR}/validation_report.json" \
    --stenv-release "${STENV_RELEASE}" \
    --stenv-yaml-filename "${STENV_YAML_FILENAME}" \
    --stenv-yaml-sha256 "${STENV_YAML_SHA256}" \
    --calwf3-executable "${CAL_EXE}" \
    --calwf3-command-log "${MASTER_LOG}"

  echo ""
  echo "Validation report:"
  cat "${RESULTS_DIR}/validation_report.json"

} 2>&1 | tee "${MASTER_LOG}"

echo ""
echo "DONE. Upload or return:"
echo "${RESULTS_DIR}/validation_report.json"
echo "${MASTER_LOG}"
