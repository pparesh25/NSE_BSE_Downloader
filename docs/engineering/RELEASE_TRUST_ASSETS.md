# Release Trust and Assets

> **Still the position as of v1.1.1, and now tracked as approved future work:**
> [PENDING_TASKS.md](PENDING_TASKS.md) §4 covers signing both platforms, a Windows
> installer and a macOS .dmg, to be taken up after the rest of that board is closed.

## Signing decision for v1.1.0

**Decided 2026-08-06: v1.1.0 ships unsigned.** Recorded here so it is not
re-argued each release.

Evidence at the time of the decision:

- `RELEASE_BUILD_EVIDENCE.md` records that Nuitka applied an ad-hoc signature and
  `codesign --verify --deep --strict` passed, but the build host held **zero**
  valid Developer ID identities and **Gatekeeper rejected** the bundle.
- `gh api .../environments/release-signing/secrets` returns
  `{"total_count": 0}`. None of the eight required secrets exist.
- `Trusted Release Candidate` is `workflow_dispatch`-only, so it never runs by
  itself and cannot turn `main` red while it stays unconfigured.

Cost of the alternative, for a free GPL-3.0 tool with a small user base:

| | macOS | Windows |
|---|---|---|
| Certificate | Apple Developer Program, $99/year | OV code-signing, roughly $200-400/year |
| Removes | The Gatekeeper block | The SmartScreen warning, but only after the certificate accumulates reputation |

Windows is the weaker case: a new OV certificate still triggers SmartScreen until
it builds reputation, so the money does not buy an immediately clean install.

What users experience instead, both documented in `UPGRADE.md` and repeated in
the release notes:

- **macOS** — "cannot be opened because it is from an unidentified developer."
  Right-click (or Control-click) the app, choose **Open**, then **Open** again.
  Once only.
- **Windows** — "Windows protected your PC." Click **More info** →
  **Run anyway**.
- **Linux** — nothing unusual.

Integrity is still verifiable: every published asset ships a `.zip.sha256`
sidecar in `shasum -c` format.

**Revisit when** users actually report being blocked, or the user base grows
enough that first-launch friction costs more than the certificates. Do not delete
`.github/workflows/trusted-release-candidate.yml` in the meantime — it is the
finished implementation, waiting only on credentials.

## Status

The application now has project-owned cross-platform icon assets and two
separate release pipelines:

- `Release Artifact Builds` produces unsigned, reproducible candidates on macOS
  ARM64, Windows x64, and Linux x64. It runs on `v*.*.*` tag pushes and on
  demand; a full three-platform compile takes about 27 minutes, so it is tied to
  a version rather than to every commit. `Quality Gates` validates the packaging
  command for all three targets on every change instead.
- `Trusted Release Candidate` is manual-only, requires an exact 40-character
  source commit, and uses the protected `release-signing` environment. It does
  not create a GitHub Release, tag, or updater notification.

The trusted workflow is intentionally credential-gated. It cannot produce a
trusted candidate until the owner supplies Apple and/or Windows signing
credentials through GitHub environment secrets.

Neither workflow publishes a GitHub Release. Both end at
`actions/upload-artifact` with 14-day retention, so creating the Release and
uploading assets is a manual step today. See `RELEASE_RUNBOOK.md` §1.3.

## Application icon

The restrained icon combines a download arrow with market-data bars. It uses a
deep navy tile, white foreground, and one muted amber bar. It contains no text,
currency symbol, exchange logo, or third-party trademark.

Committed release assets:

- `src/gui/resources/icon.png`: 1024x1024 RGBA source and Linux icon.
- `src/gui/resources/icon.ico`: Windows icon containing 16, 24, 32, 48, 64,
  128, and 256 pixel variants.
- `src/gui/resources/icon.icns`: macOS application icon container.

Packaging validation now rejects missing or malformed icon containers. The Qt
application also loads the PNG explicitly, so source runs and packaged windows
share the same identity.

The source artwork was generated with the built-in image generation tool and
locally converted from a flat chroma-key background to RGBA. The production
prompt specified a minimal desktop market-data downloader mark, a deep navy
rounded-square tile, a white download arrow integrated with simple data bars,
a muted amber accent, no text or trademarks, and no decorative effects.

## macOS trust path

The manual workflow:

1. Imports one Developer ID Application certificate into an ephemeral keychain.
2. Builds with Nuitka's explicit signing identity and notarization/hardened
   runtime mode.
3. Rewrites PySide6 plugin framework references to Nuitka's bundled flat Qt
   library layout, re-signs only the modified plugins and outer bundle, and
   verifies the repaired bundle with `codesign`.
4. Submits an exact ZIP of the signed app through `xcrun notarytool` using an
   App Store Connect API key.
5. Requires an `Accepted` notarization response, staples the ticket, validates
   it, and runs a Gatekeeper assessment.
6. Runs both CLI and isolated full-GUI smoke tests before packaging.
7. Records `trust_status: notarized` and the exact source commit in immutable
   build metadata before creating the SHA-256 sidecar.

Required `release-signing` environment secrets:

- `APPLE_CERTIFICATE_BASE64`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_KEYCHAIN_PASSWORD`
- `APPLE_NOTARY_KEY_BASE64`
- `APPLE_NOTARY_KEY_ID`
- `APPLE_NOTARY_ISSUER_ID`

The certificate secret is a base64-encoded Developer ID Application `.p12`.
The notarization key secret is a base64-encoded App Store Connect API `.p8`
private key. Private key material is written only under the ephemeral runner
directory and removed in an always-run cleanup step.

## Windows trust path

The manual workflow:

1. Decodes the code-signing PFX only under the ephemeral runner directory.
2. Builds the Windows x64 Nuitka executable.
3. Applies an Authenticode SHA-256 signature and RFC 3161 SHA-256 timestamp.
4. Verifies the signature under the Authenticode policy.
5. Runs both CLI and isolated full-GUI smoke tests before packaging.
6. Records `trust_status: signed` and the exact source commit before creating
   the SHA-256 sidecar.

Required `release-signing` environment secrets:

- `WINDOWS_CERTIFICATE_BASE64`
- `WINDOWS_CERTIFICATE_PASSWORD`

Required environment variable:

- `WINDOWS_TIMESTAMP_URL`: the RFC 3161 endpoint approved by the certificate
  issuer.

## GitHub environment policy

Create an environment named `release-signing` and configure it before adding
credentials:

1. Require owner approval for every deployment.
2. Restrict deployment branches to `main` once this pull request is merged.
3. Store credentials as environment secrets, not repository files or workflow
   inputs.
4. Rotate a credential immediately if it is copied to a log, issue, commit, or
   chat message.

The trusted workflow validates every required value before building and fails
closed when a credential or timestamp endpoint is absent.

The packaging compiler helpers and the tested Qt runtime are both exact-pinned
in `requirements-build.txt`. This prevents a previously installed compiler
release candidate or an unvalidated Qt minor release from producing a different
binary than a clean CI runner.

`requirements.txt` deliberately keeps a Qt *range* instead, because an exact pin
there would make source installation impossible on operating systems that have
no wheel for the newest Qt minor. Every release build installs both files
together, so pip resolves the intersection to the single validated version and
the reproducibility guarantee above is unchanged.

## Clean-machine acceptance gate

Every automated artifact build now executes a hidden `--smoke-gui` mode. It
starts the real packaged GUI with Qt's offscreen backend, an isolated temporary
home, and a temporary market-data root, then closes automatically. This catches
missing Qt plugins, icons, bundled config, GUI imports, and startup-time resource
errors without touching the developer's `~/NSE_BSE_Data` directory.

On macOS, the build helper also repairs a PySide6/Nuitka layout mismatch found
by this smoke gate: PyPI Qt plugins reference framework-style `@rpath` names,
while Nuitka 4.1.3 places the included Qt libraries beside the app executable.
Only links with a matching bundled library are changed, and code signatures are
restored and verified after the rewrite.

Before public release, also perform these interactive checks on fresh physical
or virtual machines:

- Verify the downloaded ZIP against its published SHA-256 sidecar.
- Confirm the native signature and, on macOS, the stapled notarization ticket.
- Launch from the normal download location so Gatekeeper/SmartScreen behavior is
  exercised.
- Confirm the icon in Finder/Dock or Explorer/taskbar.
- Use a temporary configured data root and run one recent-day download for each
  supported segment.
- Confirm progress, cancellation, Donate QR rendering, update check behavior,
  and symbol-history output.
- Confirm that no files appear in the production data root during the test.

## Publication gate

Do not merge release metadata or enable the updater until all of the following
are true:

- The trusted candidate was built from the exact intended commit.
- macOS notarization and Windows signature verification succeeded.
- Clean-machine interactive acceptance passed.
- The final artifacts are attached to an immutable GitHub Release.
- The release asset URL and SHA-256 are reviewed together before updating
  `version.py`.
