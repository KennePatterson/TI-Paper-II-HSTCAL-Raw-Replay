# Repaired DQ Literal Exactness Attack

Stage: `phase4z6_fix10_fix2_bfc1_repaired_literal_dq_exactness_github_runner_builder_01`

This repair exists because the prior DQ exactness GitHub run did not emit the required exactness JSON/CSV decision artifacts.

## Add these files

- `.github/workflows/ti_paperII_hstcal_dq_exactness_attack_repaired.yml`

- `scripts/dq_exactness_repaired.py`

- `scripts/assert_dq_exactness_artifacts.py`

- `README_DQ_LITERAL_EXACTNESS_ATTACK_REPAIRED.md`

## Run workflow

`TI Paper II HSTCAL DQ Literal Exactness Attack Repaired`

Expected artifact:

`ti-paperii-hstcal-dq-literal-exactness-attack-repaired-results`

## Required outputs

The artifact must contain:

- `dq_exactness_attack_summary.json`

- `dq_exactness_attack_decision.json`

- `dq_exactness_by_root.csv`

- `dq_exactness_by_bit.csv`

- `science_overlap_audit.csv`

- `artifact_integrity_report.json`

- logs

No semantics count. Only numeric extension exactness, root counts, XOR/bit-plane evidence, overlap audit, logs, and hashes count.

