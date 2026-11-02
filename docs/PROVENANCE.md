# Public source provenance

The initial public source snapshot is derived from the maintainer's private
PBN Studio 0.2.1-rc.2 source, followed by public licensing, documentation and
build-bootstrap changes. It intentionally does not import private Git history.

Native upstream: https://github.com/visioncortex/vtracer
Pinned source commit: `2500df76b5aea0ac22d15296b22722442f825c5b`.
Upstream source in `vendor/vtracer` is trimmed of artwork, marketing assets and
unneeded generated artifacts. Retained changes include a public face API
re-export and a Cargo example pointing to the separately owned adapter
`native/worker/main.rs`. Cargo.lock pins Rust dependencies.

Original Python application and Rust adapter: GPL-3.0-only.
Upstream and dependency licenses remain unchanged; see THIRD_PARTY_NOTICES.md.
No proprietary third-party implementation, decompiled files, commercial book
pages, private projects, credentials, diagnostic logs or native binaries are
included. The example artwork is procedurally generated from geometric shapes.

Historical local test counts describe the private fixture environment, not a
clean public clone. Public checks run their explicitly selected self-contained
suite plus real native project/export smoke. See GitHub Actions for actual
remote status; no parity with commercial converters or print readiness is
claimed.
