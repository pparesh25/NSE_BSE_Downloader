# Engineering records

Internal working documents: audits, phase reports, benchmark evidence and release
process. They are kept because they record *why* decisions were taken and what was
measured, not because they describe how to use the application.

**If you are a user, you want [../../README.md](../../README.md) or
[../../UPGRADE.md](../../UPGRADE.md) instead.**

These are historical records. Several cite absolute paths and environment names from
the machine that produced the measurement — that is deliberate, since a benchmark
without its environment is not evidence. Per the working convention in
[PENDING_TASKS.md](PENDING_TASKS.md), documents are not rewritten for language or
formatting alone; three of the older audits are largely in Gujarati for that reason.

## Current work

| Document | What it is |
|---|---|
| [PENDING_TASKS.md](PENDING_TASKS.md) | Central index of approved but incomplete work |
| [CODE_DEFECT_REMEDIATION_PLAN.md](CODE_DEFECT_REMEDIATION_PLAN.md) | **Current focus.** Phased plan for the defects blocking a multi-year backfill |
| [PROJECT_REVIEW_2026-08-06.md](PROJECT_REVIEW_2026-08-06.md) | The review those defects came from, with evidence |
| [RELEASE_IMPLEMENTATION_PLAN.md](RELEASE_IMPLEMENTATION_PLAN.md) | Staged plan for the v1.1.0 cutover and after (paused) |
| [RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md) | Verified steps for branch protection, release build, signing |
| [RELEASE_NOTES_1.1.0.md](RELEASE_NOTES_1.1.0.md) | Drafted user-facing notes for the paused release |
| [CANONICAL_REPOSITORY_MIGRATION.md](CANONICAL_REPOSITORY_MIGRATION.md) | Record of the PySide6 port becoming canonical here |

## Release and packaging

| Document | What it is |
|---|---|
| [PACKAGING.md](PACKAGING.md) | Nuitka build, signing and fresh-user verification checklist |
| [RELEASE_TRUST_ASSETS.md](RELEASE_TRUST_ASSETS.md) | Signing credential policy and clean-machine acceptance gate |
| [RELEASE_BUILD_EVIDENCE.md](RELEASE_BUILD_EVIDENCE.md) | Recorded output of actual build attempts |

## Audits and findings

| Document | What it is |
|---|---|
| [CODE_REVIEW_REMEDIATION_PLAN.md](CODE_REVIEW_REMEDIATION_PLAN.md) | Findings from a full code review and their fixes |
| [DOWNLOAD_PIPELINE_ARCHITECTURE_PLAN.md](DOWNLOAD_PIPELINE_ARCHITECTURE_PLAN.md) | Evidence-based audit behind the Phase 7 pipeline work |
| [BSE_SME_STARTUP_SUFFIX_FINDINGS.md](BSE_SME_STARTUP_SUFFIX_FINDINGS.md) | BSE SME/Startup group classification research |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Original unified-downloader implementation plan |

## Phase 7 — staged download pipeline

| Document | What it is |
|---|---|
| [PHASE_7_0_BASELINE_REPORT.md](PHASE_7_0_BASELINE_REPORT.md) | Baseline telemetry before any pipeline change |
| [PHASE_7_4_TRANSACTIONAL_STATE_REPORT.md](PHASE_7_4_TRANSACTIONAL_STATE_REPORT.md) | Transactional pipeline state |
| [PHASE_7_5_BATCHED_HISTORY_REPORT.md](PHASE_7_5_BATCHED_HISTORY_REPORT.md) | Batched symbol-history writes |
| [PHASE_7_6_STAGED_OPTIMIZATION_REPORT.md](PHASE_7_6_STAGED_OPTIMIZATION_REPORT.md) | Staged finalization, and the deferred Polars evaluation |
| [PHASE_7_6_ROLLOUT_REPORT.md](PHASE_7_6_ROLLOUT_REPORT.md) | Rollout evidence |
| [PHASE_7_HANDOFF.md](PHASE_7_HANDOFF.md) | Handoff notes |
| [STAGED_ONLY_REMOVAL_REPORT.md](STAGED_ONLY_REMOVAL_REPORT.md) | Removal of the pre-staged code path |
