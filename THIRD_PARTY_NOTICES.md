# Third-party software

Original application code is GPL-3.0-only (see LICENSE and COPYRIGHT.md). This source repository contains no proprietary Recraft, Vectorizer or Раскраска implementation. Third-party sources retain their upstream terms.

- VTracer 1.0.0-alpha.4, pinned upstream `2500df76b5aea0ac22d15296b22722442f825c5b`: MIT OR Apache-2.0; upstream MIT text retained in `vendor/vtracer/LICENSE`. Local face API re-export and worker adapter are identified in native provenance.
- visioncortex and Rust dependencies: see Cargo.lock and `assets/worker/licenses/`; flo_curves is Apache-2.0, not MIT. Some recovered standard license texts are distinguished from upstream notices.
- PySide6/Qt and Shiboken: applicable LGPL/GPL/commercial terms. Development uses dynamically linked libraries; corresponding-source and redistribution obligations require final review before public distribution.
- NumPy/SciPy/Pillow: respective upstream license texts included with the Windows package. Versions pinned in requirements-dev.txt.
- Arial is an installed Windows prerequisite, not bundled.

The repository includes source and third-party notices, not build caches or entire toolchain distributions. Vendor artwork/examples are excluded. Test binary downloads belong to prereleases, not Git history. A complete distributable SBOM and clean-Windows/legal release audit remain tracked work.
