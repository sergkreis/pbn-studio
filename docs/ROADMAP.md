# Roadmap: pages worth painting

PBN Studio has an end-to-end local pipeline. The next task is to make its results dependable at the intended print size. This is an early developer preview based on RC2, not a finished converter. The priorities below are contribution directions, not scheduled releases or claims of completed work.

## 1. Preserve the drawing through segmentation

Thin lines, small facial features and close colors can disappear during quantization and simplification. Existing source-contrast protection is heuristic; it does not understand objects and can darken antialiased edges.

Useful contributions:

- Small generated fixtures covering thin strokes, gaps, nearby colors and high-contrast details.
- Better criteria for preserving source-supported features without freezing noise into the result.
- Comparisons that measure changed pixels and region ownership alongside visual review.

**Acceptance:** show the preserved feature in the region map, rendered result and saved/reopened project. Report new artifacts and tradeoffs. Do not count erasing the feature as fixing a label or topology error.

## 2. Smooth shared boundaries without breaking topology

Smooth curves need to preserve region ownership, holes, adjacency and coverage. Current checks do not certify every illustration. The explicitly confirmed exact-pixel fallback is useful for investigation, but stepped boundaries are not equivalent to successful smoothing.

Useful contributions:

- Reproductions for self-intersections, shared-edge disagreement, slivers, holes and narrow connections.
- More robust shared-boundary fitting and validation with bounded deviation from the source.
- Efficient checks that catch overlaps or gaps across the full graph, not just individual curves.

**Acceptance:** preserve ownership and adjacency on public fixtures, validate the rendered/exported geometry, and keep failed cases blocked from normal export. Report the geometry mode explicitly.

## 3. Place readable glyphs inside difficult regions

Adaptive numeric labels already try sizes down to an explicit minimum. That does not solve all narrow or irregular regions, and placement can be expensive.

Useful contributions:

- Better candidate search using the actual glyph outline and physical clearance.
- Placement strategies for narrow areas, holes and disconnected components.
- Faster placement without stale caches or weaker containment checks.

**Acceptance:** demonstrate the final glyph at the intended page size across the preview, project round-trip and exports. Keep the minimum readable size explicit; do not silently shrink below it or enlarge the page.

## 4. Add a one-character label mode

Current palette labels are numeric and can occupy multiple characters. A configurable single-character alphabet is a proposed feature, not an existing UI option.

Useful contributions:

- A clear symbol policy that avoids confusing pairs and defines what happens when the palette exceeds the available alphabet.
- Stable symbol-to-color mapping in the page, legend, saved project and every export format.
- Schema migration and tests that preserve existing numeric projects.

**Acceptance:** each selected label is one character, remains legible in the chosen font, and maps unambiguously to its color. Test real glyph geometry rather than assuming all characters have equal width.

## 5. Make builds and checks reproducible

Source build scripts and dependency pins are a starting point. They do not establish bit-for-bit reproducibility, clean-machine operation or passing GitHub CI. Some historical tests depend on private fixtures that cannot be published.

Useful contributions:

- Replace external fixtures with small procedural cases that exercise the same invariants.
- Validate setup and native builds on a clean Windows x64 environment.
- Record toolchain versions and input/output hashes without publishing machine-specific paths or raw logs.
- Verify actual workflow runs and packaged smoke tests; document dependency redistribution requirements.

**Acceptance:** another contributor can follow [the build guide](build.md) from a fresh checkout and reproduce the documented checks. Keep claims about functional repeatability separate from byte-identical binaries. Historical local test totals are not a public CI baseline.

## 6. Keep the UI responsive during real work

Large jobs, project loading, label preparation and display updates still need performance work. Cancellation is cooperative; some library calls cannot be interrupted mid-call.

Useful contributions:

- Reproducible timing cases built from generated images.
- Separate measurement of compute, display preparation, UI updates and time spent awaiting user confirmation.
- Reduced main-thread work, bounded caches and safer cancellation/close behavior.

**Acceptance:** compare the same input, settings and machine before and after. Measure responsiveness as well as elapsed time. Check cancellation, closing during a job and save/reopen behavior; do not claim a universal speedup from one run.

## Before a stable release

A stable release needs evidence beyond automated tests: representative visual review, target-size paper prints, a clean Windows installation, verified packaging and dependency notices. Export validation must remain honest about unresolved geometry and labels.

For now, small changes with a public reproducer are more useful than broad claims of parity with other converters. See [CONTRIBUTING.md](../CONTRIBUTING.md) and [open an issue](https://github.com/sergkreis/pbn-studio/issues) to discuss a focused contribution.
