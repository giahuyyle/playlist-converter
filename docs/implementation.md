# Implementation status

The original `plan.md` is preserved. This document distinguishes implemented behavior from real-account experiments that still require credentials and consent.

| Plan phase | Delivered | Verification |
| --- | --- | --- |
| 1. Provider experiments | Google/Spotify OAuth + PKCE, Apple MusicKit, list/get/search/create/add adapters, read-only probe CLI | Mock HTTP contract tests; live provider authorization and writes remain unverified |
| 2. Normalization | Shared Track model; YouTube title/channel/duration parsing; Apple library/catalog IDs; Spotify unavailable/local tracks | Unit tests with provider response fixtures; real playlist corpus still needed |
| 3. Matching | Unicode/noise normalization, title/artist/duration/album weights, ISRC, recording-version safeguards, official/music signals, top-five review candidates | 100 synthetic cases plus negative version/duration/metadata tests; not a production accuracy benchmark |
| 4–5. Both conversion directions | Generic analyze → review → confirm → transfer services | All six ordered provider pairs pass against the isolated demo catalog |
| 6. Persistence | Users, encrypted accounts, OAuth state, conversions, matches, scoped mappings, jobs, migrations, history | SQLite and PostgreSQL suites; migration upgrade/schema checks |
| 7. RabbitMQ | Transactional outbox, relay, confirms, durable queue, acknowledgement, dead-letter exchange, checkpoint recovery, advisory locks | Actual RabbitMQ + PostgreSQL smoke with two workers and duplicate deliveries |
| 8. Live progress | SSE snapshots with polling fallback, progress/result UI | API event test and browser transfer walkthrough |
| 9. Spotify | Third adapter and OAuth flow using current playlist item endpoints | Mock contracts and bidirectional demo tests; real Spotify app access remains unverified |

## Boundaries

`api/` owns HTTP validation and user ownership. `services/` owns matching, token refresh, and conversion state transitions. `providers/` owns provider schemas and HTTP calls. `models/` owns persisted records and the normalized Track. SQLAlchemy sessions are supplied by `core/database.py`. `workers/runner.py` owns queue delivery, distributed conversion exclusion, and failure policy. The frontend handles MusicKit, review, confirmation, history, and progress.

The conversion service intentionally stays synchronous internally; workers invoke it outside the HTTP request lifecycle. This retains the plan's incremental design without exposing a long-running public synchronous transfer endpoint.

## State transitions

```text
queued → matching → review_required → queued → transferring → completed
            ↓                              ↓
       queued (retry)                 queued (retry)
            ↓                              ↓
          failed ← exhausted/permanent/uncertain failure
```

Every unresolved track requires an explicit candidate or skip decision. High-confidence matches are selected automatically but can still be changed before confirmation. Confirm locks the conversion row and commits a job in the same transaction. Repeated confirmations do not enqueue another transfer.

A match checkpoint commits after each search. A destination write intent commits before playlist creation or each individual append; the response ID/completed flag commits immediately after success. An uncertain result blocks writes until reconciliation observes the expected remote state. This avoids falsely claiming exactly-once behavior from external APIs that do not offer idempotency keys.

Retry delays are durable `jobs.available_at` values, not a sleeping worker or a RabbitMQ TTL queue. This keeps workers available and makes retry state visible in PostgreSQL. A relay outage delays work but does not lose it. A publisher crash can cause duplicate delivery, handled by the conversion lock and persisted checkpoints. Unexpected internal exceptions become failed jobs without leaking provider credentials in error messages.

Mappings are user/storefront scoped and expire after 30 days. Track occurrences remain separate rows with a unique position; repeated songs are preserved. Search results and selected track metadata are stored as JSON. Transfers insert one occurrence at a time to make partial-write recovery precise.

## External validation still required

Connect real test accounts and use the probe and UI to verify:

1. Full pagination and normalized track retrieval from one real YouTube playlist and one real Apple library playlist.
2. MusicKit authorization with the configured signing key, storefront, account eligibility, and browser domain.
3. Creating and adding tracks in each provider with the app's approved OAuth scopes.
4. A labeled real-world corpus of roughly 100 tracks, including remixes, live recordings, feature credits, non-Latin titles, and unavailable catalog items. Measure top-one accuracy and false auto-accepts before tuning thresholds.
5. Provider-specific quota/account-access behavior, including Spotify's developer-mode restrictions.

Those checks cannot be replaced by mocked APIs or demo data. No real account playlist has been created or changed during implementation.

## Verification performed

- 132 tests pass on SQLite, including the 100-case synthetic matching corpus.
- The same suite passes on PostgreSQL 17 in disposable Docker infrastructure.
- The actual RabbitMQ smoke test runs two workers with duplicate deliveries and verifies one destination playlist containing exactly five tracks.
- Alembic upgrade and schema consistency checks pass on SQLite and PostgreSQL.
- Frontend ESLint and production build pass; Python Ruff checks pass.
- Both Docker images build. The Nginx → API → PostgreSQL proxy/session/playlist smoke check passes.
- A browser walkthrough completes connection, playlist selection, analysis, manual review, skip, confirmation, live progress, and the five-track demo result. The layout was inspected at desktop size and 390px width.
- The test runtime emits two upstream Starlette/httpx/AnyIO deprecation warnings; no test failures.
