# Changelog

## Initial public source snapshot

Based on the private 0.2.1-rc.2 application. Public source licensing: GPL-3.0-only.
Private Git history, user artwork, logs, research copies and binaries are excluded.
Public documentation and standard-toolchain build support are maintained separately from the historical binary release.

### Existing capabilities
- Local image segmentation and shared-boundary tracing through a pinned Rust/VTracer adapter.
- Python/PySide6 desktop UI, palette and source/result views.
- Adaptive physical labels, project persistence and SVG/PDF/PNG exports.
- Cooperative cancellation, batch processing and local diagnostic logging.
- Heuristic source-contrast preservation through automatic simplification.

### Not yet solved
General visual fidelity, robust smooth topology, fine-detail/label readability tradeoffs, fully self-contained legacy tests and clean-machine/print acceptance. See docs/ROADMAP.md.
