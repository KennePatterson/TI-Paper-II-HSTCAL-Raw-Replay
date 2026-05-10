# TI Paper II Raw-to-Drizzled HLSP Reconstruction Runner

This bundle adds:

- `.github/workflows/ti_paperII_raw_to_drizzled_reconstruction.yml`

- `README_RAW_TO_DRIZZLED_RECONSTRUCTION.md`

Workflow name:

`TI Paper II Raw-to-Drizzled HLSP Reconstruction`

Expected artifact:

`ti-paperii-raw-to-drizzled-reconstruction-results`

Important:

This first runner is a raw-to-drizzled blocker/localization and bounded product-comparison runner. It does not claim full raw-to-drizzled closure by itself. Full closure requires a public HLSP reference product, WCS/grid contract, grouping, parameters, and numeric comparison outputs.

If `hlsp_reference_url` is blank, the workflow will emit a missing-HLSP blocker artifact rather than fail silently.

