# Private messenger RAG: review and release plan

Reviewed on 2026-10-03 against [telegramRAG commit b5db0f9](https://github.com/levtmn/telegramRAG/tree/b5db0f9ce50f19413a653027d07ab5c6ce7e7bda).
This is a source review and proposed product direction, not a penetration test or a deployment certification. Local shell execution and file writes were unavailable during this review; the application was not run.

## Contents

1. Product boundary and recommendation
2. Current method and findings
3. Messenger acquisition and support
4. Proposed architecture and privacy modes
5. Deployment and data lifecycle
6. Prioritized delivery plan
7. Release acceptance criteria
8. Commercial offer and open decisions

## 1. Product boundary and recommendation

Sell a customer-operated knowledge archive that searches imported messenger history and answers with inspectable original-message evidence. Keep data ownership, deployment control, import coverage, and reliable deletion central to the offer.

Keep the existing dense + BM25 retrieval and RRF design. Correct ingestion and retrieval defects, separate provider implementations, and establish an enforced local processing mode. Hosted AI can remain a customer-selected option; it changes the privacy contract.

The initial release should support one authenticated owner per instance, text history, manual import, repeat import, source browsing, search, cited answers, and deletion. Proposed first deployment target: customer-owned Linux x86-64 server/VM. Validate other targets separately. Telegram and WhatsApp are the strongest first import targets; Viber and MAX require narrower support definitions.

Exclude live account synchronization, arbitrary attachments, voice/image extraction, unattended account login, message sending, multi-tenant SaaS, and autonomous actions from this release. These introduce acquisition, privacy, and operational work beyond a history-search product.

## 2. Current method and findings

The current pipeline parses Telegram exports, groups reply threads, embeds text with Cohere, writes embedded Qdrant and BM25 indexes, fuses search results with RRF, reranks with Cohere, and sends selected excerpts to Claude. FastAPI provides upload, indexing, streamed answers, and SQLite answer history.

The useful foundations are metadata-bearing chunks, hybrid retrieval, cached indexing, source display, a small Python application, and ARM64-oriented deployment examples. Preserve these where measurements support them.

### Actual data flow

| Stage | Data involved | Current destination |
| --- | --- | --- |
| Import/preprocessing | Raw messages, sender names, timestamps, reply IDs | Local project folders and chunks.json |
| Document embeddings | Full indexed chunk text, including headers and authors | Cohere API |
| Query embedding | User's question | Cohere API |
| Reranking | Question and candidate chunk text | Cohere API |
| Generation | Question and selected excerpts, names, dates | Anthropic API |
| History | Questions, answers, copied source excerpts | Local SQLite history.db |
| Optional notification | Index counts or exception text | Public ntfy.sh topic |
| Browser dependencies | Browser requests and execution of remote JS/CSS | jsDelivr CDN |

The application and indexes are local; inference is external. The current `--no-llm` path still invokes Cohere embeddings and reranking, and imports initialize both API clients. It is not an offline privacy mode.

### Findings that block a private commercial release

| Priority | Evidence in reviewed source | Consequence | Required change |
| --- | --- | --- | --- |
| P0 | web/app.py routes have no authentication, CORS allows every origin, uvicorn binds 0.0.0.0 | Reachable clients can inspect history, upload, index, alter metadata, delete history, and consume API credits | Authenticate all data routes; restrict origins; bind privately; expose one protected TLS origin for LAN use |
| P0 | scripts/index.py and scripts/query.py create hosted clients at import time; web/app.py requires Anthropic key at import | External processing is mandatory and cannot support a strict customer-only promise | Lazy provider construction plus enforced local/hosted policy; no public fallback |
| P0 | web/templates/index.html inserts marked.parse output into HTML for live and saved answers | Untrusted generated HTML can reach the DOM; source text can influence generated output | Sanitize all generated HTML, restrict URL schemes, remove unsafe inline interpolation, and test malicious persisted output |
| P0 | Upload reads the entire archive and selected member without limits; metadata paths derive from supplied project names | Memory/disk exhaustion risk and insufficient managed-path validation | Stream/cap input; cap expanded bytes/members/time; reject links; validate project IDs and resolved paths |
| P0 | Only answer-history deletion is implemented | Removing a history session does not delete imported data or derived indexes | Dataset deletion and retention policy covering all copies and backup restore |
| P1 | preprocess.py makes each message without a reply its own thread, then drops chunks below 80 estimated tokens and messages below 10 characters | Many normal chat messages never become searchable; documented temporal fallback does not group unrelated roots | Temporal grouping for messages without reply links; preserve short meaningful messages and report all exclusions |
| P1 | Long individual messages are not split; DFS only starts from roots | Token limits are soft and malformed cyclic reply graphs can omit messages | Handle oversized messages and disconnected/cyclic records without silent loss |
| P1 | _load_cache accepts a cache solely when chunk counts match | An edit with the same chunk count reuses embeddings for old text | Content/model/preprocessing fingerprints, validated shapes, and atomic cache writes |
| P1 | web query retrieves global top-20 results, applies dates and truncates, then filters chat_name | Relevant messages in the requested chat/date range may never enter the candidate set; identical names are ambiguous | Stable chat IDs and constraints applied before candidate limits in both search branches |
| P1 | Qdrant collection is deleted/rebuilt before BM25 publication; embedded clients are opened across request/index paths | Partial or mismatched generations and overlapping access are operational risks | Single storage owner or serialized access; staged matching generations; atomic publication and recovery |
| P1 | _idx.running is set inside a scheduled task; readiness checks use file/directory existence | Concurrent jobs can race and projects can be labeled indexed without membership evidence | Acquire job ownership before scheduling; maintain a persistent import/index manifest |
| P1 | Sources shown by web API omit original message IDs; instructions and excerpts share a user prompt | Citations are hard to verify and retrieved instructions can distort answers | Message-level source resolver, explicit evidence IDs, untrusted-context handling, and abstention evaluation |
| P1 | requirements.txt is unpinned; systemd uses /home/user/claude/analyze-json; nginx serves only a landing page | Installations depend on a developer machine and do not protect the app on port 8080 | Lock dependencies; versioned package/container; configurable paths; protected proxy and private backend |
| P1 | Browser dependencies include remote JS and an unversioned marked URL; ntfy can send exception text | Offline operation fails and third-party code/network dependencies remain in the privacy boundary | Vendor pinned browser assets; disable external notifications in local mode; redact diagnostic events |

These findings come from static source inspection. Path handling, DOM payloads, storage contention, and job races need focused verification before claiming exploitability or exact failure rates. The upload code reads a selected archive member rather than extracting arbitrary archive paths; this review does not assert a demonstrated Zip Slip exploit.

Pickle caches are generated locally in the current design, not accepted directly through the archive uploader. Treat them as trusted-runtime artifacts only, and use non-executable serialization for a customer-facing backup/import format.

## 3. Messenger acquisition and support

Official documentation was checked on 2026-10-03. Formats, API access, and permissions can change. The current implementation supports only individual Telegram JSON chat exports; the rows below are proposed support boundaries.

| Messenger | Proposed acquisition | Product status | Limits to disclose |
| --- | --- | --- | --- |
| Telegram | User-selected Telegram Desktop JSON chat export | First release: harden existing adapter | Distinguish single-chat exports from full-account exports; support only validated variants; report partial/truncated inputs and media exclusions |
| WhatsApp | User-exported text transcript, directly or inside its export archive | First release: new adapter | Validate Android/iOS and locale variants; parse multiline text and system events; request date/timezone clarification when ambiguous; do not promise complete history or original IDs/reply links |
| Viber | A documented user-provided history file, if available and validated; otherwise a separately scoped local acquisition path | Discovery/experimental | Official personal-data download does not contain chat content; mobile backup is not automatically a readable history format |
| MAX | Customer-authorized bot access to eligible chats/channels, or a verified user-provided file | Conditional pilot | Official GET /messages by chat_id requires bot administrator status; it does not establish arbitrary personal-chat access or a universal export |

Telegram documents Desktop export to JSON or HTML and individual-chat export. Start with JSON and explicitly reject unsupported full-account/HTML variants until implemented. [Telegram export documentation](https://telegram.org/blog/export-and-more)

WhatsApp documents chat export as a text file, optionally with media. Build a direct parser for that export rather than requiring the customer to move messages through Telegram. [WhatsApp export documentation](https://faq.whatsapp.com/1180414079177245/?cms_platform=android&locale=pa_IN)

Viber explicitly excludes chat content from its personal-data download. Its backup workflow is tied to the mobile platform; neither fact supplies a portable transcript parser. Do not market account-data JSON as chat-history support. [Viber data download](https://help.viber.com/hc/en-us/articles/9062953104029-Control-your-personal-data), [Viber backup documentation](https://help.viber.com/hc/en-us/articles/9173576528413-Back-up-and-restore-your-chat-history)

For MAX, the documented messages endpoint supports eligible bot-admin chats/channels with time bounds and a maximum count per response. A pilot must verify pagination, oldest-message coverage, edits/deletions, membership changes, and applicable authorization. A saved local JSON snapshot can enter the common import pipeline after this validation. [MAX messages API](https://dev.max.ru/docs-api/methods/GET/messages)

For Viber/MAX files, establish formats from official specifications or synthetic samples modeled on legitimately available formats. Unsupported acquisition remains unsupported; do not silently import unrelated account metadata or require root/jailbreak extraction.

Every adapter should produce an import report: source messenger/client/format, supplied date range, parsed records, retained messages, ignored system events, duplicates, ambiguous records, errors, missing attachments, timezone confidence, and whether truncation recovery occurred. Report exported coverage rather than claiming the file represents the entire account.

## 4. Proposed architecture and privacy modes

Use a modular Python application rather than a distributed service fleet:

```text
Customer import
  -> bounded format detection and messenger adapter
  -> normalized message store + import manifest
  -> conversation/context builder
  -> keyword index + optional local dense index
  -> filtered retrieval + RRF + optional local reranker
  -> cited extractive results or configured local answer model
  -> source inspector and optional answer history
```

### Common message representation

Preserve `schema_version`, `source_messenger`, `source_account_id`, `source_chat_id`, `source_message_id` when present, internal `dataset_id/chat_id/message_id`, sender identity and display label, original text, original timestamp, UTC timestamp if resolvable, timezone/parse confidence, reply linkage if present, edit/deletion status when available, attachment references, and import provenance.

Keep identities namespaced by source/account/chat. Do not merge people by display name. For transcripts without native IDs, preserve record location and use documented conservative identity/deduplication rules; identical text/time/author alone may represent separate legitimate messages.

Retain normalized messages in SQLite for provenance, source inspection, deduplication, and deletion. Rebuildable chunk/index artifacts should reference those messages. Avoid duplicating full source text in many stores when IDs will suffice.

### Provider boundaries

Introduce interfaces for embedding, reranking, and answer generation. Keep model configuration and privacy enforcement out of messenger parsers. Store provider/model/revision/dimension/input parameters alongside index generations; changing an embedding space requires reindexing.

| Mode | Embeddings/reranking/answers | Intended promise |
| --- | --- | --- |
| Local search | Local keyword retrieval; optional local embeddings/reranker; extractive results | Customer-controlled search without a hosted AI service |
| Local answers | Customer-controlled embedding and inference services, optional reranker | Imported content and processing remain inside the configured customer boundary |
| Hosted opt-in | Customer-selected public providers with customer-owned credentials | External processing is disclosed and explicitly enabled |

In local mode, public API keys are unnecessary. Missing models/services produce clear setup errors or an explicitly configured local-search capability; they never trigger a hosted fallback.

Enforce a single outbound policy at the server, alongside network egress restrictions. Include browser assets, crash reporting, notifications, model downloads, update checks, and license activation in that policy. Provision models/images before bringing private data into an offline installation. A customer's external VPS is a different boundary from their own LAN; document the selected boundary.

Do not choose local models purely from parameter count. Compare multilingual retrieval/answer quality, Russian and mixed-language chat, license terms, memory, CPU/GPU throughput, and model artifact size. Publish a supported model/hardware profile only after benchmarking. The prototype's Raspberry Pi resource claims concern hosted inference, not a local generative model.

Cohere's current enterprise page distinguishes paying customers from trial-key users, provides a training opt-out, describes retention exceptions, and states its AI solutions are not intended for personal/family/household use. This is material if selling a personal archive with hosted Cohere. Verify the applicable agreement and intended customer use before offering that mode. [Cohere enterprise commitments](https://cohere.com/enterprise-data-commitments)

Anthropic documents API retention arrangements by model, feature, and agreement. Do not assume every endpoint or account provides zero retention. No-training, no-storage, residency, and data staying on the customer's machine are separate claims. [Anthropic API data handling](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)

Pseudonymization can reduce some exposure but free-text context can still identify people. It is not a substitute for a local processing boundary.

## 5. Deployment and data lifecycle

### Proposed first deployment package

Ship a versioned Linux container package with an application service, an authenticated TLS ingress when used over LAN, and optional customer-controlled model services. Keep SQLite and embedded Qdrant initially; use one application/storage owner and serialize indexing appropriately. Move to a separate vector server only when measured concurrency or capacity requires it.

- Provide locked dependencies, pinned image/model revisions, a release manifest/checksums, third-party notices, and a software bill of materials.
- Use configurable persistent storage outside the source tree, a dedicated non-root account, restrictive permissions, and customer-held secrets.
- Bind application/model ports to loopback or private container networks. Publish only the intended entry point.
- Include initial-owner setup, password/session handling, request limits, CSRF protection where cookie auth is used, query/job limits, and resource-specific authorization.
- Serve browser assets locally. Apply response security headers and sanitization that also cover saved answers.
- Preflight storage, memory, architecture, model availability, privacy mode, and deployment origin before import.
- Provide an offline synthetic demonstration dataset and an installation smoke check.
- Document backup, restore, upgrade, rollback, uninstall, and failure recovery. Systemd can remain a supported alternative if its paths, virtual environment, service user, and hardening are parameterized.

Docker packaging alone does not supply authentication, isolation, encryption, backups, or local inference. Windows/macOS desktop installation is a separate product package and support commitment.

### Data inventory, retention, and deletion

Treat source exports, normalized records, media references, chunks, embeddings, Qdrant/BM25 data, cache files, questions, answers, copied citations, logs, temporary upload files, backups, and support bundles as part of one data inventory.

Let the customer control source retention and answer-history storage. Restrict diagnostics to redacted operational events; omit chat names, query text, excerpts, credentials, and raw provider exceptions. Use local logs by default.

Use customer-controlled encrypted storage and encrypted backups where encryption at rest is part of the selected deployment profile. Document key custody and recovery; do not invent application cryptography.

Dataset deletion should block new jobs/queries for that dataset, remove raw and normalized content, remove/rebuild affected indexes and caches, remove or redact dependent saved answers/citations, clean temporary artifacts, and verify no searchable references remain. Record a minimal deletion marker without message content.

Backups need a defined expiry and restoration procedure that re-applies deletion markers before exposing restored data. Immediate physical erasure from SSDs, snapshots, or already-sent hosted requests cannot be promised by deleting a database row.

## 6. Prioritized delivery plan

| Order | Work | Exit evidence |
| --- | --- | --- |
| 1 | Secure prototype boundary: authentication, private binding, origin policy, upload limits, managed paths, output sanitization | Synthetic access-control, archive-abuse, and persisted-output checks; no bypass through the backend port |
| 2 | Separate providers and enforce local mode, including browser/notification egress | Import/index/query/UI work without public keys under denied internet access after provisioning; hosted opt-in prevents unauthorized calls |
| 3 | Correct Telegram coverage, stable source IDs, cache invalidation, filtered retrieval, and job/index publication | Short-message, unthreaded-chat, same-size-edit, chat/date restriction, and failed-reindex fixtures pass |
| 4 | Add normalized store, manifests, repeated import, dataset deletion, and backup restore handling | Import twice without unintended duplicates; edits handled; deletion survives restore |
| 5 | Add WhatsApp parser and user-facing import report | Android/iOS and agreed locale fixtures; multiline/ambiguous-date handling; documented coverage |
| 6 | Package and qualify one deployment/model profile | Fresh install, reboot, failure recovery, upgrade/rollback, backup/restore, measured quality and resource use |
| 7 | Pilot Viber/MAX acquisition paths independently | Verified legal/user-authorized acquisition and representative end-to-end format tests; supported boundaries published |

Complete the first six stages before describing the proposed first release as ready to deploy. Do not hold Telegram/WhatsApp delivery hostage to unverified Viber/MAX acquisition.

## 7. Release acceptance criteria

These are proposed gates. None were executed during this documentation review.

- **Privacy:** local profile imports, indexes, searches, answers, and renders the UI with external network access denied after provisioning. Public credentials are absent. There is no implicit cloud fallback or content-bearing telemetry.
- **Hosted processing:** calls are blocked before recorded opt-in; each embedding/rerank/generation destination and payload purpose is disclosed; disabling it prevents further calls.
- **Access:** an unauthenticated client cannot list, read, mutate, delete, upload, index, or query data. Session/resource IDs do not bypass ownership checks. Internal model/storage ports are not externally reachable.
- **Imports:** accounting reconciles parsed, retained, excluded, ambiguous, and duplicate records. Synthetic fixtures cover short replies, messages without replies, long text, cyclic/missing reply links, mixed languages, Unicode, timezone ambiguity, partial exports, repeat imports, and equal chat/sender labels.
- **Isolation and retrieval:** results obey selected chat/date/author scope before candidate limits and model transmission. Fixtures include a relevant message outside the global top-20. Message-level source references resolve correctly.
- **Caches and lifecycle:** changing text without changing chunk count invalidates embeddings. Provider/dimension changes cannot reuse incompatible vectors. Failed reindexing preserves a working prior generation.
- **Jobs and operations:** concurrent index requests cannot create conflicting jobs; state survives restart; failures have bounded retries and recoverable status. UI/API status uses manifest evidence.
- **Deletion:** a removed dataset is absent from source storage, normalized records, all retrieval paths, caches, and dependent history. Restore cannot resurrect deleted content for use.
- **Quality:** use a versioned synthetic evaluation set with factual, negative/unanswerable, temporal, exact-name/ID, and multilingual questions. Record retrieval recall, citation correctness, answer support, and abstention separately. Establish release thresholds from pilot requirements rather than unmeasured claims.
- **Performance:** publish p50/p95 query latency, indexing time, peak RAM, disk growth, supported corpus size, and concurrent workload for the actual hardware/model profile.
- **Distribution:** fresh install/restart/upgrade/rollback and backup/restore succeed. Dependency/model licenses and notices are checked, local model artifacts are reproducible, and support diagnostics contain no customer content.

## 8. Commercial offer and open decisions

Position the product as private messenger-history search with evidence. The differentiated work is reliable imports, measurable coverage, private operation, deployment support, and data lifecycle management; a generic chat-over-files UI alone is easy to replace.

A practical first offer is a paid customer-deployment package plus optional installation and maintenance. Use offline-capable licensing for a local deployment promise. Keep vendor support access off by default and make any diagnostics previewable by the customer.

Do not advertise all four messengers as fully supported while only Telegram ingestion is implemented. Publish a format-specific support matrix and mark pilots clearly. State that answers are based on supplied exports and may be incomplete or unsupported.

The repository currently carries an MIT license. Preserve its notice and assess dependencies and model artifacts separately before distribution. Commercial packaging/support can be sold, but do not present existing MIT-distributed code as exclusively proprietary. [Repository license](https://github.com/levtmn/telegramRAG/blob/b5db0f9ce50f19413a653027d07ab5c6ce7e7bda/LICENSE)

Before setting a release price or universal privacy claim, decide the initial buyer (personal owner or organization), operating system/hardware, corpus size, acceptable local-answer latency, offline requirement, supported language/locale set, and support model. Determine applicable data-processing responsibilities for the actual market and hosted arrangements rather than claiming blanket regulatory compliance.

Recommended next implementation milestone: an authenticated Telegram import/search path that runs under denied public egress, preserves message coverage, and exposes verifiable sources. Add local cited generation on a measured hardware profile, then WhatsApp. Validate Viber/MAX acquisition as separate pilots.
