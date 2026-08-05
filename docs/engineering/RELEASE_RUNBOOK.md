# Release runbook — steps 1.1, 1.3 and 1.4

Written 2026-08-06. Every command and file name below was verified against this
repository, not assumed. Companion to `RELEASE_IMPLEMENTATION_PLAN.md`.

---

## 1.1 — Branch protection on `main`

**Why this is first.** After the merge, `main/version.py` *is* the update channel: every
installed client reads it and decides whether to announce a new version. An accidental
`git push origin main` publishes an update to all of them. Protection is the only thing
standing between a typo and a notification you cannot recall.

Current state, verified:

```
gh api repos/pparesh25/NSE_BSE_Downloader/branches/main/protection
→ 404 "Branch not protected"
gh api repos/pparesh25/NSE_BSE_Downloader/rulesets
→ []
```

The repository is public, so branch protection costs nothing.

### The one decision that matters

GitHub's `enforce_admins` flag decides whether *you* are also subject to the rule.

| `enforce_admins` | `git push origin main` by you | Protects against your own mistake? |
|---|---|---|
| `false` | allowed | **No** — the protection is decorative for a solo owner |
| `true` | rejected | **Yes** |

You are the only admin. With `enforce_admins: false` the rule would never apply to
anyone, which defeats the purpose. **Set it to `true`.** You can turn it off for a
genuine emergency in about ten seconds, and turning it off is a deliberate act — which
is exactly the property you want.

Required approvals stay at **0**. You have no second reviewer, and a non-zero count
would make it impossible to merge your own pull request.

### Option A — via the API (fastest)

Your token already carries the `repo` scope, so this works as-is:

```bash
gh api -X PUT repos/pparesh25/NSE_BSE_Downloader/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=Tests (Python 3.10)" \
  -f "required_status_checks[contexts][]=Tests (Python 3.13)" \
  -f "required_status_checks[contexts][]=Ruff, mypy and packaging dry run" \
  -F "enforce_admins=true" \
  -F "required_pull_request_reviews[required_approving_review_count]=0" \
  -F "required_pull_request_reviews[dismiss_stale_reviews]=true" \
  -F "restrictions=null" \
  -F "allow_force_pushes=false" \
  -F "allow_deletions=false"
```

The three context names are the `name:` values of the jobs in
`.github/workflows/quality-gates.yml` — `Tests (Python ${{ matrix.python-version }})`
expands to two, plus the static static-analysis job name.

Verify:

```bash
gh api repos/pparesh25/NSE_BSE_Downloader/branches/main/protection --jq '{admins:.enforce_admins.enabled,force_push:.allow_force_pushes.enabled,checks:.required_status_checks.contexts}'
```

### Option B — via the web UI

**Settings → Branches → Add branch protection rule**

1. Branch name pattern: `main`
2. ☑ Require a pull request before merging — set **Required approvals: 0**
3. ☑ Require status checks to pass before merging → ☑ Require branches to be up to date
   → search and add the three checks named above
4. ☑ **Do not allow bypassing the above settings** ← this is `enforce_admins`
5. Leave "Allow force pushes" and "Allow deletions" unchecked
6. Create

### Also enable

**Settings → Code security → Dependabot alerts.** Currently
`gh api .../vulnerability-alerts` returns 404, meaning it is off.

---

## 1.3 — Building and publishing the release

### The gap you need to know about first

**Neither workflow publishes a GitHub Release.** Both end at
`actions/upload-artifact`, which stores files in the Actions run with
**14-day retention**. Verified:

```
grep -rn "softprops|gh release|create-release|releases/assets" .github/workflows/
→ no matches
```

So steps 4–6 below are manual today. That is fine for one release; say the word and it
can be automated before the next one.

### What gets built

`.github/workflows/release-artifacts.yml` — triggers: `workflow_dispatch`, and `push` on
tags matching `v[0-9]+.[0-9]+.[0-9]+`. Three jobs, 90-minute timeout each, about
27 minutes in practice:

| Job | Runner | Produces |
|---|---|---|
| macOS 15 ARM64 | `macos-15` | `NSE_BSE_Downloader-1.1.0-darwin-arm64.zip` |
| Windows 2022 x64 | `windows-2022` | `NSE_BSE_Downloader-1.1.0-windows-x64.zip` |
| Ubuntu 24.04 x64 | `ubuntu-24.04` | `NSE_BSE_Downloader-1.1.0-linux-x64.zip` |

Each also produces a `.zip.sha256` sidecar. The name is built by
`package_release_artifact.py:210-213` as
`{APP_NAME}-{__version__}-{target}-{architecture}`, and the sidecar is written at
`package_release_artifact.py:249-252` in standard `shasum -c` format:

```
<64 hex characters>  NSE_BSE_Downloader-1.1.0-darwin-arm64.zip
```

Each job also runs `--smoke-gui` inside `package_release_artifact.py`, which starts the
real packaged GUI offscreen with a temporary home and data root, then closes it. A build
that cannot start is rejected before it is uploaded.

### Step 1 — Rehearse without a tag — **done 2026-08-06, all green**

Do this first. It exercises the whole build with nothing published and no tag to delete:

```bash
gh workflow run "Release Artifact Builds" --repo pparesh25/NSE_BSE_Downloader --ref codex/pyside6-port-v1.1.0
```

```bash
gh run list --repo pparesh25/NSE_BSE_Downloader --workflow "Release Artifact Builds" --limit 1
```

Wait for all three jobs to go green. If anything fails, fix it now — a tag makes the
failure public.

#### Rehearsal result — run 31054912467

27 minutes 15 seconds wall clock. All three jobs succeeded.

| Artifact | Size | Contents |
|---|---|---|
| `NSE_BSE_Downloader-1.1.0-darwin-arm64.zip` | 61 MB | `NSE BSE Data Downloader.app` (834 files) + `BUILD-METADATA.json` |
| `NSE_BSE_Downloader-1.1.0-windows-x64.zip` | 46 MB | `NSE_BSE_Downloader.exe` + `BUILD-METADATA.json` |
| `NSE_BSE_Downloader-1.1.0-linux-x64.zip` | 80 MB | `NSE_BSE_Downloader` + `BUILD-METADATA.json` |

Verified after downloading, not merely assumed from a green tick:

- `shasum -a 256 -c` on all three sidecars: **OK**. Sidecar format is
  `<64 hex>  <filename>`, which `shasum -c` reads directly.
- `BUILD-METADATA.json` records `"trust_status": "unsigned"` and
  `"git_commit": "0126ac0…"`, matching the branch head. The `SOURCE_COMMIT`
  fallback to `github.sha` therefore works on `workflow_dispatch`, where
  `github.event.pull_request.head.sha` is absent.
- Each archive has a single top-level directory named after the archive.
- The packaged macOS app was extracted and launched with `--smoke-gui` under a
  temporary `HOME`: **exit 0**.
- `codesign -dv` reports `Signature=adhoc`, `TeamIdentifier=not set`;
  `spctl --assess --type execute` reports **rejected**. This independently
  reproduces the Gatekeeper behaviour documented in `RELEASE_TRUST_ASSETS.md`, and
  is exactly what `UPGRADE.md` prepares users for.

Artifacts expire 2026-08-19. They are release *candidates*, not the release: the
published assets must be rebuilt from the tagged commit in step 3.

### Step 2 — Set the release date

`version.py` currently says `__build_date__ = "2026-08-04"` and `VERSION_HISTORY`
says `"release_date": "2026-07-31"`. Update both to the real release date, in the same
commit that you tag.

Do **not** change `__version__`, the `VERSION_HISTORY` key, or the shape of that
dictionary — installed v1.0.1 clients parse all three by regular expression, and
`tests/test_index_and_version.py` fails if the contract breaks.

### Step 3 — Tag

```bash
git tag -a v1.1.0 -m "NSE/BSE Data Downloader v1.1.0" && git push origin v1.1.0
```

The tag push triggers the three builds automatically.

*Reversible:* `git push --delete origin v1.1.0 && git tag -d v1.1.0`

### Step 4 — Download and verify the artifacts

```bash
mkdir -p ~/Downloads/nse-release-1.1.0 && cd ~/Downloads/nse-release-1.1.0
```

```bash
gh run download --repo pparesh25/NSE_BSE_Downloader --name darwin-arm64-release-candidate --name windows-x64-release-candidate --name linux-x64-release-candidate
```

Verify every checksum yourself rather than trusting the build:

```bash
find . -name "*.zip.sha256" -execdir shasum -a 256 -c {} \;
```

Every line must say `OK`.

### Step 5 — Create the Release

```bash
gh release create v1.1.0 --repo pparesh25/NSE_BSE_Downloader --title "v1.1.0" --notes-file RELEASE_NOTES_1.1.0.md $(find . -name "*.zip" -o -name "*.zip.sha256")
```

Write `RELEASE_NOTES_1.1.0.md` first. It must contain, at minimum:

- a link to `UPGRADE.md` at the top, because v1.0.1 users arrive here from a popup
- the PyQt6 → PySide6 dependency change and the Python 3.10 floor
- the macOS right-click→Open step and the Windows SmartScreen step (see 1.4)
- that Intel Macs have no build and should use the source route
- that existing 7-column data files are left untouched and symbol histories start empty

*Reversible:* `gh release delete v1.1.0`

### Step 6 — Record the evidence

`RELEASE_BUILD_EVIDENCE.md` is currently pinned to commit `9dba2ac`, which is
superseded — the branch head is now further along. An integrity record that does not
describe the shipped artifact is worse than none, because it invites you to trust a
stale hash.

Rewrite it against the tagged commit with the real SHA-256 of each published asset.

### Step 7 — Verify from the outside

Download each asset **from the Release page**, not from your build directory, and run it
on a clean machine. This is the last point at which nothing has been published to users.

### Step 8 — Merge

Only now mark PR #10 ready and merge it with a **merge commit**. See
`RELEASE_IMPLEMENTATION_PLAN.md` §2 for why merge and not squash.

**This is the point of no return.** The moment `main/version.py` says `1.1.0`, every
running v1.0.1 client announces it within three seconds of its next launch.

---

## 1.4 — Signing decision

### Recommendation: ship v1.1.0 unsigned

Recorded reasoning, so this does not get re-argued later.

**What the code says today.** `RELEASE_BUILD_EVIDENCE.md:58-61` records that Nuitka
applied an ad-hoc signature, `codesign --verify --deep --strict` passed, the host has
**zero** valid Developer ID identities, and **Gatekeeper rejected** the bundle.

**What signing would require.** `.github/workflows/trusted-release-candidate.yml` is
already written for the signed path — two jobs, `macos` and `windows`, both bound to the
`release-signing` environment, requiring eight secrets:

```
APPLE_CERTIFICATE_BASE64      APPLE_CERTIFICATE_PASSWORD
APPLE_KEYCHAIN_PASSWORD       APPLE_NOTARY_KEY_BASE64
APPLE_NOTARY_KEY_ID           APPLE_NOTARY_ISSUER_ID
WINDOWS_CERTIFICATE_BASE64    WINDOWS_CERTIFICATE_PASSWORD
```

Verified: `gh api .../environments/release-signing/secrets` returns
`{"total_count": 0}`. None exist. That workflow is `workflow_dispatch`-only, so it never
runs by itself and will not turn `main` red — it simply stays unused.

Note it has **no Linux job**, which is correct; Linux binaries are not signed this way.

**Cost of the alternative.**

| | macOS | Windows |
|---|---|---|
| Certificate | Apple Developer Program, **$99/year** | OV code-signing cert, **~$200–400/year** |
| Effort | Export cert, create notary key, add 6 secrets, run the trusted workflow | Export cert, add 2 secrets |
| Removes | Gatekeeper block | SmartScreen warning (only after reputation builds) |

Windows is the weaker case: a new OV certificate still shows SmartScreen warnings until
it accumulates reputation, so the money does not buy an immediately clean install.

**Why unsigned is defensible here.** This is a free GPL-3.0 tool with a small user base
who find it through GitHub. They are already downloading a binary from a GitHub Release
by a named author, and the `.sha256` sidecars let anyone verify integrity. The
first-launch friction is two extra clicks, documented in `UPGRADE.md`, which they will
already have read because the update popup sends them there.

Spending $99–500/year before knowing whether anyone is blocked by this is premature.

**What users will actually see.**

*macOS:* "…cannot be opened because it is from an unidentified developer." They
right-click (or Control-click) the app, choose **Open**, then **Open** in the dialog.
Once only. Already documented in `UPGRADE.md`.

*Windows:* "Windows protected your PC." They click **More info** → **Run anyway**.
Already documented in `UPGRADE.md`.

*Linux:* nothing unusual.

### Actions for this decision

- [ ] Record the decision in `RELEASE_TRUST_ASSETS.md`
- [ ] Confirm the release notes repeat the first-launch steps for both platforms —
      users read release notes, not the repository
- [ ] Use `release-artifacts.yml` for v1.1.0; leave `trusted-release-candidate.yml`
      untouched and unconfigured
- [ ] Revisit only if users actually report being blocked

### If you later decide to sign

Do not delete `trusted-release-candidate.yml` — it is the finished implementation. When
you have certificates: create the `release-signing` environment, require owner approval
for deployment, restrict it to `main`, add the eight secrets as **environment** secrets
(not repository secrets), then run the workflow with the exact 40-character commit SHA.

---

## Order of work

```
1.1 branch protection        ← do today, 5 minutes, protects everything after it
 ↓
1.4 record signing decision  ← 10 minutes, unblocks the release notes
 ↓
1.3 step 1: rehearse build   ← ~30 minutes, mostly waiting, nothing published
 ↓
1.5 housekeeping             ← while the build runs
 ↓
1.3 steps 2-7: real release  ← still fully reversible
 ↓
1.3 step 8: MERGE            ← point of no return
```
