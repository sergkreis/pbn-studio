# Security and privacy

## Support status

PBN Studio is an early developer preview based on 0.2.1-rc.2. There is no guaranteed security response time, stable-version support window or backport policy. Reports about the current public source are welcome. Include the affected revision and, for a binary, its origin and SHA256.

## Report a vulnerability privately

Do not publish exploit details, malicious files, credentials or private data in an issue or pull request.

If GitHub private vulnerability reporting is enabled, use the repository's [Security page](https://github.com/sergkreis/pbn-studio/security) and choose **Report a vulnerability**. This document does not claim that the feature is enabled.

If that option is unavailable, open a minimal issue asking maintainer [@sergkreis](https://github.com/sergkreis) for a private reporting channel. State only that you need to report a security concern. Wait for a private channel before sending technical details or attachments; do not post them publicly if a reply is delayed.

A private report should include:

- Affected version or source revision and relevant environment details.
- Impact, prerequisites and minimal steps to reproduce.
- A safe synthetic reproducer, if possible, rather than a user project or real personal data.
- Any proposed mitigation and whether the issue has already been disclosed elsewhere.

Ordinary bugs without a security impact belong in [GitHub issues](https://github.com/sergkreis/pbn-studio/issues).

## Local processing is not a sandbox

Image processing runs locally and does not require a cloud processing service. That does not make untrusted files safe. Images and project archives are parsed by Python, Qt, native code and their dependencies. The native subprocess and archive validation are not a security boundary against hostile input.

- Run the application without administrator privileges.
- Use a disposable environment for untrusted files. Keep original artwork backed up separately.
- Obtain source and any future releases from [the public repository](https://github.com/sergkreis/pbn-studio). The initial public publication is source-only.
- Where a release supplies hashes, verify them. A matching hash is not a digital signature and does not establish the trustworthiness of the download source.
- Do not assume a Windows executable is signed or clean-machine validated unless the particular release provides that evidence.

## Projects and diagnostics can contain private data

Saved `.pbn`/`.pbnx` projects include the source image and document state. Treat them as user data, not as generic bug-report attachments. Do not publish private projects, user artwork or paid book pages.

Local diagnostic logs normally live under `%LOCALAPPDATA%/PBNStudio/logs`; a fallback `logs` directory can be used if needed. Set `PBN_DIAGNOSTICS=0` **before launching** to disable diagnostics. See [diagnostics](docs/diagnostics.md).

Do not upload raw logs or diagnostic bundles to this repository. Share a minimal, sanitized description instead. Check filenames, paths, environment details, metadata and screenshots before publishing any reproduction. Automatic filtering is not a guarantee that sensitive data is absent.

If a secret is exposed, revoke or rotate it first. Notify the maintainer privately about repository cleanup; deleting it in a later commit does not remove it from history or copies already made.
