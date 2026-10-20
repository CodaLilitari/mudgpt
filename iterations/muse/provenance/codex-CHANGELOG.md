# Codex iteration 0.2.0

- Broker lifetime identifiers and guarded sends bind a command to its original observation and expiry.
- The manual MCP relay uses durable decision admission and preserves uncertainty across failures and restarts.
- Observation helpers preserve original versus pager receipts and retain send evidence when later observation fails.
- Structured protocol payload redaction is uniform after secret entry; connection status reports completed connection setup accurately.
- Color spans and CLI/MCP control handoffs remain part of the Codex client. Claude and Muse couriers remain separate iterations.

Baseline: public MudGPT commit `060e50c85b0c65f6dc088d5941d0863961dad254`. Source and helper bytes are unchanged from the reviewed local Codex candidate; only package version metadata and public documentation differ.
