---
name: productize-messenger-rag
description: Review and develop telegramRAG for private deployment in a customer's environment. Use for Telegram, WhatsApp, Viber, or MAX history imports; messenger normalization; RAG quality; local or opt-in hosted model providers; data deletion; deployment packaging; and commercial release readiness in this repository.
---

# Productize Messenger RAG

## Establish the baseline

1. Read the repository's `AGENTS.md` and `docs/PRODUCT_READINESS.md`.
2. Inspect the current code, Git status, and scoped instructions. Treat the review's pinned commit as a historical baseline.
3. Identify the task's data flow, supported input format, privacy mode, and customer environment. Continue independent work while clarifying missing constraints.
4. Use synthetic data. Do not open private runtime exports or invoke hosted providers with customer content.

## Choose the workflow

### Import or normalization

- Verify current official messenger documentation before promising export/API access.
- Require a known format and version; report unsupported inputs explicitly.
- Keep platform parsers behind adapters that emit a common message schema.
- Preserve stable source identity, timestamps with timezone confidence, raw-text fidelity, reply links, and provenance. Do not invent missing IDs or relationships.
- Detect locale/date ambiguity, duplicates, edits, partial files, and unavailable media. Publish an import report with counts and coverage.
- Validate size/time limits and storage-path containment. Never deserialize uploaded pickle files.
- Exercise short messages, multiline text, equal display names, repeat imports, and format variation with synthetic fixtures.

### Retrieval or answers

- Apply customer ownership, chat, author, and date restrictions in both retrieval branches before candidate limits and external calls.
- Preserve reply context while grouping messages without replies by bounded temporal windows.
- Account for all eligible messages; keep short answers and oversized messages through explicit handling.
- Version embeddings and indexes by content and provider configuration; publish compatible generations together.
- Return citations pointing to original messages. Distinguish unsupported answers from facts present in source text.
- Treat source instructions as data, keep model calls free of action tools, and sanitize rendered answers.
- Evaluate retrieval, citations, abstention, and performance independently.

### Privacy or providers

- Trace imports, embeddings, retrieval, reranking, generation, history, logs, notifications, browser assets, backups, licensing, and support.
- Enforce local mode through a central policy; require customer-controlled endpoints and prohibit public fallback.
- Verify local mode without public API keys and with internet egress denied after provisioning.
- For hosted mode, show the customer exactly which content goes to each provider before indexing. Record opt-in and verify current terms for the actual account, model, and endpoint.
- Treat no-training commitments, retention, residency, and network isolation as separate properties.
- Verify deletion across raw data, vectors, keyword indexes, caches, answer history, and restore workflows.

### Deployment or release

- Choose one supported customer environment and package a reproducible, versioned release for it.
- Require authentication, private internal services, TLS for LAN exposure, least-privilege runtime storage, health checks, and bounded jobs.
- Check first setup, sample import, citations, restart, backup/restore, deletion, upgrade/rollback, and failure recovery.
- Benchmark actual hardware with the stated corpus; publish measured latency and resource limits.
- Review dependency/model distribution rights and support diagnostics without transmitting customer content.
- Keep Viber/MAX capabilities conditional until supported acquisition paths pass end-to-end tests.

## Deliver the result

Implement the authorized change when requested. Provide concrete findings with source paths, a prioritized next step, and verification evidence. Label proposed behavior separately from shipped features. For readiness reviews, identify release blockers before optional enhancements. Do not declare the product deployable from an architecture document alone.
