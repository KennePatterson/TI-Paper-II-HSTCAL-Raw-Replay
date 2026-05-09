# TI Paper II DQ Literal Exactness Attack

Stage: `phase4z6_fix10_bfc1_literal_dq_exactness_attack_github_runner_builder_01`

Goal: force or falsify literal DQ exactness before moving to raw-to-drizzled.

Current locked state before this run:

- SCI/ERR/SAMP/TIME exact 20/20

- DQ exact 5/20

- DQ mismatch 15/20

- every DQ mismatch is XOR=4096 only

- no science-array overlap

This run does not accept that as final. It tries to force DQ 20/20.

## Files to add to GitHub

Copy these into repo:

- `.github/workflows/ti_paperII_hstcal_dq_exactness_attack.yml`

- `scripts/dq_exactness_attack.py`

- `scripts/run_astrodrizzle_dq_update_attempt.py`

- `README_DQ_LITERAL_EXACTNESS_ATTACK.md`

Then run GitHub Actions workflow:

`TI Paper II HSTCAL DQ Literal Exactness Attack`

Use default inputs.

Expected artifact:

`ti-paperii-hstcal-dq-literal-exactness-attack-results`

## Decision outcomes

- `dq_exactness_attack_literal_dq_exact_20of20_supported`

  - Literal DQ 20/20 is achieved.

- `dq_exactness_attack_literal_dq_exact_blocked_4096_provenance_only_no_science_overlap`

  - DQ exactness remains blocked, but the blocker is only 4096/no-overlap provenance.

- `dq_exactness_attack_literal_dq_exact_blocked_non4096_or_incomplete`

  - A real DQ problem or incomplete run remains.

No semantics count. Only arrays, XOR histograms, bit-plane rows, overlap audit, logs, and hashes count.

