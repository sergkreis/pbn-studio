# Contributing to PBN Studio

GitHub issues and pull requests are welcome at [sergkreis/pbn-studio](https://github.com/sergkreis/pbn-studio). This is an early developer preview. Improving visual fidelity and making results reproducible matters more than adding another option to the interface.

Start with the [roadmap](docs/ROADMAP.md). The highest-priority work is segmentation preservation, topology-safe smoothing, adaptive glyph placement, one-character labels, reproducible builds and UI performance.

## Choose a small, verifiable change

1. Check existing issues. For a substantial algorithm, dependency or file-format change, open an issue describing the problem and acceptance criteria before implementing it.
2. Work in a branch based on the public repository. Keep each PR focused; separate behavior changes from broad formatting or vendor updates.
3. Add a regression test for a bug fix. Prefer a small, generated fixture with an explicit expected result.
4. Run the relevant checks and describe what you actually tested. Include limitations and remaining failures.
5. Open a [GitHub pull request](https://github.com/sergkreis/pbn-studio/pulls), link the issue, and explain the user-visible change and risks. A maintainer reviews and merges accepted changes.

Public contributions are reviewed on GitHub. The initial source snapshot does not include private development history, and contributors do not need access to that history. Do not import it into public branches.

## Set up and test

Follow [the build guide](docs/build.md) for Python and the native worker, then [the testing guide](docs/testing.md) for available checks and fixture limitations. Python dependencies alone are not enough for native integration tests.

To request both application and engine tests, use PowerShell from the repository root:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
& ./.venv/Scripts/python.exe -m pytest tests tests_engine -ra
```

Plain `pytest` defaults to `tests` and does not include `tests_engine`. Some legacy tests depend on fixtures intentionally excluded from the public repository; the command above is not a promise that a clean checkout passes the full historical suite. Do not add private artwork to satisfy a test. Replace the dependency with a redistributable synthetic fixture, or report the blocker accurately.

A source smoke check exercises the native pipeline, project save/reopen and export:

```powershell
& ./.venv/Scripts/python.exe launch.py --smoke-test-dir reports/source-smoke-new
```

The output directory must be new. Keep generated reports local. Offscreen tests do not establish UI responsiveness, visual quality, physical print quality or clean-machine packaging.

In the PR, record:

- Tested commit or revision, and whether the working tree had additional changes.
- Windows, Python, Qt and relevant Rust/toolchain versions; worker hash when applicable.
- Exact commands, exit codes and actual passed/failed/skipped counts. Explain skips.
- A concise, sanitized result summary and what remains blocked or untested. Do not upload raw logs.

The historical RC2 result of 326 passed and 1 skipped belongs to an older local environment, not this public checkout or GitHub CI. Only an actual workflow run establishes remote status. Do not turn a missing fixture into an unexplained skip or weaken an invariant to get a green result.

## Review visual changes at the intended print size

For segmentation, smoothing and label work, include a minimal generated input, exact settings and before/after output you have reviewed for safe publication. Report the page dimensions in millimeters, preferred/minimum label size and line width.

- Preserve meaningful source details and color ownership. Fewer regions or fewer errors are not sufficient evidence of improvement.
- Check holes, shared edges, narrow regions, touching boundaries and multiple disconnected regions of one color.
- Validate the actual glyph footprint and clearance, not only the label center or its bounding box.
- Compare the UI, reopened project and SVG/PDF/PNG outputs when those paths are affected.
- Keep unresolved geometry or label problems visible. Do not bypass normal-export preflight, silently switch to pixel boundaries, lower the readable size floor or enlarge the page to hide failures.
- Test cancellation and closing during work when changing job lifecycle code. Cancellation is cooperative.

A regression test should fail for the old behavior and pass for the proposed fix where practical. For performance changes, measure the same input and settings before and after; name the machine and separate compute time from UI work and time waiting for confirmation.

## Keep the public repository clean

Do not commit or attach:

- User artwork, private projects, commercial coloring-book pages or research copies of third-party material.
- Decompiled software, proprietary implementations or code whose redistribution rights are unclear.
- Raw logs, diagnostic bundles, local reports, secrets, credentials, private network addresses or machine-specific paths.
- Built executables, release archives, caches, virtual environments or local toolchain configuration.

Use procedural shapes and small synthetic fixtures instead. Review images, metadata, filenames and text before sharing them. Project archives embed source images and are not harmless debugging attachments. `.gitignore` is not a substitute for reviewing the actual changes.

Keep text UTF-8 with LF endings. Follow nearby code style and avoid unrelated rewrites. Changes under `vendor/` need an explanation, an upstream reference where applicable, and preserved license/provenance information. Recheck dependency notices when adding or updating a dependency.

## Licensing and security

Submit only work you have the right to contribute. Original code contributions are accepted under the project's **GPL-3.0-only** license, not “GPL-3.0-or-later”. Third-party components keep their own licenses; do not relabel them as original project code.

The application license does not impose GPL licensing on user artwork or generated output. Any fixture or example you propose to distribute must nevertheless have clear redistribution permission and attribution where required.

For vulnerabilities, use [SECURITY.md](SECURITY.md), not an ordinary bug report with exploit details.
