# Release implementation plan — v1.1.0 cutover and beyond

Created: 2026-08-06 (Asia/Kolkata)
Branch: `codex/pyside6-port-v1.1.0` (PR #10, draft)
Baseline commit: `e96d9bf` — "fix: make the v1.0.1 upgrade path survivable"

This plan covers what remains after the upgrade-path work landed. It is ordered so
that nothing user-visible happens until everything behind it is verified.

Status legend: **[ ]** not started · **[~]** partly done · **[x]** done

---

## Stage 0 — Already closed

- [x] Canonical repository retargeting (`GITHUB_REPOSITORY = "pparesh25/NSE_BSE_Downloader"`)
- [x] v1.0.1 → v1.1.0 notification bridge, covered by
      `tests/test_index_and_version.py:73-95`
- [x] Rollback references published: `archive/pyqt6-v1.0.1`,
      tag `pyqt6-pre-pyside6-port-2026-08-05`
- [x] Upgrade documentation (`UPGRADE.md` + README banner)
- [x] Python 3.10 interpreter guard reachable from Python 3.8
- [x] Qt requirement split: range for source installs, exact pin for release builds
- [x] Quality Gates de-duplicated; `package_release_artifact.py` added to mypy

---

## Stage 1 — Merge blockers

Everything here must be complete before PR #10 leaves draft.

### 1.1 Branch protection on `main` — effort S — **done 2026-08-06**

- [x] Pull request required (0 approvals — there is no second reviewer)
- [x] `enforce_admins: true`, so the rule applies to the owner too. With it false the
      protection would be decorative for a solo maintainer
- [x] Force-push and deletion blocked; `strict: true` so a branch must be current
- [x] Required checks: `Tests (Python 3.10)`, `Tests (Python 3.13)`,
      `Ruff, mypy and packaging dry run` — names taken from the live check-runs API
- [x] Dependabot alerts enabled

Verified by attempting a real direct push to `main` with the owner's token:

```
remote: - Changes must be made through a pull request.
remote: - 3 of 3 required status checks are expected.
 ! [remote rejected] HEAD -> main (protected branch hook declined)
```

To disable temporarily in an emergency, turn off "Do not allow bypassing the above
settings" in Settings → Branches, or `DELETE .../branches/main/protection`.

### 1.2 Update-artifact schema — effort M — **done**

- [x] Replaced the scalar `__update_url__` / `__update_sha256__` pair with a
      per-platform `__update_artifacts__` map keyed `<platform>-<architecture>`,
      matching the names `package_release_artifact.py` gives each archive
- [x] `_artifact_for_this_platform()` selects the running platform's entry and returns
      empty strings — keeping the notification-only path — when the map is absent,
      unparseable, not a mapping, missing this platform, or missing a string url/sha256
- [x] Remote metadata is read with `ast.literal_eval`, never executed
- [x] URLs containing `.` or `..` segments are rejected, so a dot segment can no longer
      satisfy the repository prefix test while resolving elsewhere
- [x] `__version__`, `__build_date__` and `VERSION_HISTORY` unchanged; the v1.0.1
      parser was replayed against the new file and still extracts 1.1.0 with 17
      features and 13 bug fixes
- [x] Six tests added in `tests/test_update_security.py`

**Decision taken 2026-08-06: v1.1.0 ships with an empty `__update_artifacts__` map —
notification-only.** The mechanism is implemented and tested; entries are populated in
1.1.1, after the release process has been exercised once. Do not re-open this without a
reason to.

Consequence handled: notification-only makes the update dialog the *only* route to a
download, so the dialog no longer shows a disabled "Verified Package Unavailable"
button. It now offers an enabled "Open Release Page" button
(`src/gui/update_dialog.py:337-347`, `open_release_page()`), with the URL supplied by
`UpdateChecker.release_page_url` so the repository is not hardcoded in the GUI.

### 1.2b Release builds tied to a version, not to every commit — effort S — **done**

`release-artifacts.yml` previously ran a full three-platform Nuitka compile on every
pull-request commit touching `src/**`. Measured duration: about 27 minutes per run.

- [x] Trigger is now `workflow_dispatch` plus `push` on `v*.*.*` tags
- [x] Quality Gates compensates by validating the packaging command for **all three**
      targets on every change instead of Linux only — verified that darwin, windows and
      linux dry runs all pass from one host

### 1.3 Build and publish the release — effort M

- [ ] Freeze the release commit
- [ ] Run `trusted-release-candidate.yml` (or `release-artifacts.yml`) at that exact SHA
- [ ] Record SHA-256 for every asset
- [ ] Rebuild `RELEASE_BUILD_EVIDENCE.md` at the release commit — it is currently pinned
      to `9dba2ac`, which is superseded. An integrity record that does not match the
      artifact is worse than none.
- [ ] Create tag `v1.1.0` and a GitHub Release with all assets plus `.sha256` sidecars
- [ ] Download every asset from the published Release on a clean machine and run it

### 1.4 Signing decision — **decided 2026-08-06: ship unsigned**

Full reasoning, cost comparison and the revisit condition are recorded in
[RELEASE_TRUST_ASSETS.md](RELEASE_TRUST_ASSETS.md) §Signing decision. Summary: the
build host has zero Developer ID identities and Gatekeeper rejected the ad-hoc
signature; the `release-signing` environment holds 0 of the 8 required secrets; and
$99–400/year is premature before anyone reports being blocked. Windows is the weaker
case still, since a new OV certificate keeps triggering SmartScreen until it earns
reputation.

- [x] Decision recorded in `RELEASE_TRUST_ASSETS.md`
- [x] `UPGRADE.md` documents the macOS right-click→Open and Windows
      More info→Run anyway steps, and routes Intel Macs to the source install
- [x] `trusted-release-candidate.yml` left in place and unconfigured — it is the
      finished signed path, waiting only on credentials. Do not delete it.
- [x] Both first-launch steps are in the drafted release notes
      ([RELEASE_NOTES_1.1.0.md](RELEASE_NOTES_1.1.0.md)). Users read release notes, not
      the repository.

### 1.5 Repository housekeeping — effort S — **done 2026-08-06**

- [x] `development` **archived, not deleted**. It was not fully merged: two commits
      (`b637dc6`, `a2458bf`) were unique to it, adding `DEVELOPMENT_WORKFLOW.md` and
      setting `__version__ = "1.0.2-dev"`. That version string is the hazard — the
      v1.0.1 client parses versions with `int(part)`, so `"2-dev"` raises `ValueError`
      and the notification silently stops for everyone. Preserved as
      `archive/development-v1.0.2-dev` at the same SHA, following the existing
      `archive/pyqt6-v1.0.1` convention, then the misleading `development` name was
      removed.
- [x] Hardcoded `/Users/paresh/miniforge3/...` paths replaced with `python` in
      `README.md` (build and test sections) and `PACKAGING.md`.
- [x] `Market Holidays` kept at the repo root. Only v1.0.1 clients read it; removing it
      would break them for no benefit. Revisit when v1.0.1 usage drops.

Open decision, deliberately not taken unilaterally:

- [ ] Twelve internal engineering records still carry `/Users/paresh` paths as build
      evidence (`PHASE_7_*`, `RELEASE_BUILD_EVIDENCE.md`, `CODE_REVIEW_REMEDIATION_PLAN.md`,
      `BSE_SME_STARTUP_SUFFIX_FINDINGS.md`, `STAGED_ONLY_REMOVAL_REPORT.md`,
      `PENDING_TASKS.md`, and this plan). They become public repository-root content on
      merge. Three are largely in Gujarati, against the English-only convention in
      `PENDING_TASKS.md` §Working convention. Options: leave as is (they are honest
      historical evidence and the convention exempts unrevised historical documents);
      move them under `docs/engineering/` so the root is clean for users; or translate
      the three. Recommendation: **move to `docs/engineering/`**, translate nothing —
      it is a pure `git mv`, keeps every record, and stops the root from reading like a
      workspace.

---

## Stage 2 — Cutover

**Completed 2026-08-09.** Executed in order; step 2.3 was the point of no return.

- [x] **2.1** Re-run all gates at the release commit in both environments —
      398 tests on Python 3.10 and 3.13, coverage 77.2%, Ruff, mypy, `--smoke-gui`
- [x] **2.2** Publish the Release (Stage 1.3) — tag `v1.1.0` at `059f497`, three
      platform assets plus sidecars, every digest verified from the published page
- [x] **2.3** Mark PR #10 ready and **merge with a merge commit** (not squash, not
      rebase — GitHub reports `rebaseable: false`, and the branch carries two
      multi-parent commits including the history-preserving import `8b338e3`)
      → merge commit `ed06dc8`; `main/version.py` now reads `1.1.0`, and the released
      commit is an ancestor of `main`, so the tag describes code that is on `main`
- [x] **2.4** Watch CI green on `main`
- [ ] **2.5** Watch issues for 48 hours

Rollback after 2.3: do **not** force-push `main`. Restore from `archive/pyqt6-v1.0.1` in
a normal revert commit. Users already notified cannot be un-notified.

---

## Stage 3 — First week after release

### 3.1 Archive the source repository — effort S

- [ ] Set `NSE_BSE_Downloader_PySide6` to archived/read-only, retitle it
      "Archived — development moved to NSE_BSE_Downloader", pin a link.
      **Never delete it**: that breaks existing clones, forks, issue links, and any
      installed build still polling it.
- [ ] Update the `Getbhavcopy-alternative` "Update checker" file — it still advertises
      a "PyQt6-based interface" and points at the codeload `main` zip. Both are wrong
      after the merge.

### 3.2 Project files users expect — effort S

- [ ] `CHANGELOG.md` (can be generated from `VERSION_HISTORY`)
- [ ] `SECURITY.md` — how to report a vulnerability
- [ ] `CONTRIBUTING.md`
- [ ] Issue templates (bug / feature)

### 3.3 Local hygiene — effort S

- [ ] Move or delete the stale checkout at
      `~/Documents/New project/NSE_BSE_Downloader 1.0.1 pyside6`. It is at `b7caca5`,
      still hardcodes the PySide6 repo as the update endpoint, and pushing from it
      would undo the canonical retargeting.
- [ ] The `Getbhavcopy-alternative` working tree has no `.gitignore` and currently
      contains a whole nested git repository. One `git add -A` commits a repo into a
      repo. Add a `.gitignore` or move the checkout out.

---

## Stage 4 — v1.1.1: upgrade friction found in review

These are real user-facing issues, none of them release blockers.

### 4.1 First-run date range — effort S, high user impact

`config.yaml:64` still has `base_start_date: "2025-07-15"`. An existing user who enables
a segment they never used before silently queues ~13 months of downloads, now with four
extra per-date stages enabled by default.

- [ ] Show the computed date count before starting and confirm above ~30 days
- [ ] Consider defaulting a newly enabled segment to "recent only"

### 4.2 Update-dialog usability — effort S

- [ ] Add an "Open Release Page" button — the notification-only dialog currently has no
      way to reach the download
- [ ] Branch the dialog text for packaged versus source installs; the same
      "manually replace the files" instruction is shown to both today
- [ ] Make manual "Check for Updates" say something when already up to date, and when
      the network call fails (both are silent now)

### 4.3 Mixed-format data — effort M

Old 7-column and new 9-column daily files coexist permanently with no marker.

- [ ] Add a "re-download date range" action so users can convert deliberately
- [ ] Document the sniffing rule for downstream scripts (already in `UPGRADE.md`;
      consider a machine-readable marker file)

### 4.4 Symbol-history backfill — effort M

Per-symbol files start empty at upgrade; the headline v1.1 feature looks broken on day
one for existing users.

- [ ] Build histories from existing daily files instead of requiring a re-download

### 4.5 Retention — effort S — **done 2026-08-06**

`.state/backups` was measured at 8,250 files / 36 MB with no pruning.

- [x] Add a retention policy for `.state/backups`, `.state/raw_revisions`, quarantine

Done as part of [CODE_DEFECT_REMEDIATION_PLAN.md](CODE_DEFECT_REMEDIATION_PLAN.md)
§2.2, which also stopped writing `.state/backups` in the first place.

---

## Stage 5 — Deferred, decision required

Tracked in `PENDING_TASKS.md`; not scheduled.

- BSE SME/Startup naming: `TS` group is still dropped. Inherited from v1.0.1, so not a
  regression, but it is silent data loss. Needs the product decision recorded in
  `PENDING_TASKS.md` §1 before implementation.
- pandas → Polars: correctly deferred. The measured bottleneck is history-file
  publication, not DataFrame computation, so this would not pay off yet.
- Engineering documents in Gujarati (`DOWNLOAD_PIPELINE_ARCHITECTURE_PLAN.md` and
  others) become public canonical content on merge, against the English-only convention
  in `PENDING_TASKS.md`. Either translate them or move them out of the public tree.
  Not a blocker — historical documents are explicitly exempt until substantively revised.

---

## Critical path

```
1.1 branch protection  ─┐
1.2 artifact schema    ─┼─→ 1.3 build & publish ─→ 2.1 gates ─→ 2.2 release ─→ 2.3 MERGE
1.4 signing decision   ─┤                                                         │
1.5 housekeeping       ─┘                                                         ▼
                                                                    3.x archive & hygiene
                                                                    4.x v1.1.1 follow-ups
```

Stage 1 is roughly one focused day, most of it waiting for builds. Stage 2 is under an
hour. Everything after that is unhurried.
