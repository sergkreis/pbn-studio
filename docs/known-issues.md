# Known limitations

- Segmentation can lose meaningful detail, distort thin strokes or merge close colors.
- Source-contrast protection is heuristic; it can darken antialiased edges and does not understand objects.
- Smooth topology validation does not certify all illustrations. Exact pixel boundaries are an explicit fallback, not equivalent to smooth contours.
- A fixed readable-size policy can conflict with fine detail. Adaptive labels respect the explicit minimum; unresolved labels can block normal export.
- The manual editing toolbar is currently hidden; internal history remains.
- Long jobs and project loading still need performance work. Cancellation is cooperative.
- Some legacy tests depend on private artwork or historical experimental fixtures; those are not included in this public repository.
- Windows is the tested development platform. Clean-machine installation, other platforms and physical print quality require additional validation.
- No binary release is included in the initial public source publication. Dependency redistribution review remains necessary.

See [the contributor roadmap](ROADMAP.md). Please submit minimal, redistributable reproductions, not paid artwork or private projects.
