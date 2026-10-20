# Muse iteration changes

## 0.2.0 — 20 October 2026

- Ship a self-contained iteration with its own pinned Codex 0.2.0 core snapshot.
- Keep the scoped host adapter separate from the Claude courier and Codex relay.
- Add a configurable receiving helper with privately pinned endpoint, recipient,
  envelope hash and receiving paths; no live deployment is implied.
- Add offline RSA receiving-key generation and scoped-envelope preparation.
- Publish human setup instructions, JSON workflow and schemas, dependency pins,
  explicit file manifests and synthetic test receipts without private material.
- Preserve one-attempt transport, server-side observation/expiry guards, token
  echo suppression and conservative handling of uncertain or unknown delivery.
- Explain retained receiving keys, continuing service availability, restart
  recovery and the need for an awake remote conversation/task/job.

The preserved core changelog is in `provenance/codex-CHANGELOG.md`.
