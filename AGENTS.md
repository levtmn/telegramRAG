# Project instructions

## Objective and scope

Build a commercial, customer-deployed product for private search and cited answers over Telegram, WhatsApp, Viber, and MAX message histories. Keep the first release focused on text imports, source inspection, grounded answers, repeat imports, deletion, and reliable deployment.

Start with one customer instance and one authenticated owner. Treat Linux x86-64 as the initial deployment assumption until the user chooses a target. Keep ARM64 compatibility where practical; measure hardware limits before publishing requirements. Multi-user sharing, live sync, media OCR/transcription, and agent actions need separate scope.

## Read before changing the project

- Read `docs/PRODUCT_READINESS.md` for the source-based review, proposed architecture, messenger limitations, and release gates.
- Use `.agents/skills/productize-messenger-rag/SKILL.md` for import, retrieval, privacy, deployment, and product-readiness work.
- Read `CLAUDE.md` for legacy implementation details. Its hosted-API and device assumptions describe the prototype, not the intended privacy contract.
- Inspect the current checkout and applicable nested instructions. Preserve unrelated changes. Distinguish implemented behavior from proposals.

## Current implementation

- `scripts/preprocess.py`: Telegram JSON parsing and reply-thread chunking.
- `scripts/index.py`: Cohere embeddings, per-chat pickle caches, embedded Qdrant, and BM25.
- `scripts/query.py`: hybrid retrieval, RRF, Cohere reranking, and Claude answers.
- `web/app.py`: FastAPI/SSE, archive upload, background indexing, SQLite answer history.
- `web/templates/index.html`: browser UI; currently uses external CDN assets.
- `deploy/`: Raspberry Pi systemd/nginx examples with machine-specific paths.
- Runtime exports, indexes, history, and secrets are private data. Existing storage is under `telegram-*/`, `db/`, `bm25/`, and `data/`.

## Privacy and security rules

- Use synthetic fixtures. Do not inspect customer exports, print message content, send it to development tools, or put it in commits, screenshots, logs, issues, or support bundles.
- Treat raw messages, names, embeddings, snippets, answers, and query history as sensitive.
- Make local processing the proposed default. In local mode, use only customer-controlled services and fail closed when they are unavailable; never fall back to a public API.
- Keep hosted AI optional. Before any customer-data transfer, require a product setting that discloses provider, purpose, data fields, and applicable retention. Use customer-owned keys and record the setting version.
- Enforce the boundary on the server, including ingestion, queries, reranking, notifications, telemetry, and browser dependencies. Bind internal services privately.
- Authenticate all data APIs. Resolve resource ownership on the server; a chat filter or browser-generated session UUID is not access control.
- Treat messages and model output as untrusted data. Prevent prompt instructions from invoking actions, and sanitize rendered Markdown/HTML.
- Bound imports by compressed size, expanded size, member count, and processing time. Resolve filesystem paths inside managed storage; reject links and unsupported formats.
- Deletion must cover source files and derived data, including cached excerpts in history. Document backup expiry and restore-time deletion handling.
- Do not promise air-gapped operation, encryption at rest, regulatory compliance, or zero retention without implemented controls and verification.

## Engineering conventions

- Preserve the hybrid retrieval design where evaluation supports it. Separate messenger adapters, normalization, storage, retrieval, and model providers.
- Use stable internal IDs namespaced by messenger/account/chat; display names are labels. Preserve original IDs, timestamps, reply links, import provenance, and schema versions.
- Report import coverage and ambiguity. Retain short meaningful messages and account for every parsed message.
- Apply ownership/chat/date restrictions before candidate limits and before any model call, in both dense and keyword retrieval.
- Version cache entries by content hash, preprocessing version, model identity/revision, dimensions, and embedding parameters.
- Serialize embedded Qdrant access and indexing safely. Publish matching dense/keyword index generations atomically; retain the previous generation until success.
- Avoid untrusted pickle loading. Use reproducible dependencies and verified artifacts; keep runtime storage outside source code.
- Keep UI messages useful to customers: import coverage, privacy mode, source evidence, failures, and recovery steps.

## Commands and verification

Legacy commands run from the repository root and can send data to hosted services:
`python3 scripts/preprocess.py`, `python3 scripts/index.py`, `python3 scripts/query.py "question"`, and `python3 web/app.py`.
Do not run indexing/query commands on customer data for development verification.
Install dependencies in a virtual environment; do not default to `--break-system-packages`.

The reviewed baseline has no test suite, dependency lock, or container build. Inspect the current checkout before deciding verification commands. Add focused tests with synthetic data for substantive import, privacy, access-control, retrieval, or lifecycle changes. Use mocks for public providers. Verify local operation under denied outbound network access. For documentation-only changes, verify links and skill metadata without claiming runtime tests passed.

A release is ready only after the deployment, privacy, data-lifecycle, and quality gates in `docs/PRODUCT_READINESS.md` pass on the stated hardware. Report changes, evidence, and remaining limitations. Do not merge or deploy merely because documentation has been added.
