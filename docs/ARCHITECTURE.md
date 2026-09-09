# Felix architecture

This is the on-demand technical reference for Felix. The engineering invariants that govern changes live in [`CLAUDE.md`](../CLAUDE.md); setup and first-run instructions live in [`README.md`](../README.md).

## System at a glance

Felix has a Next.js browser application deployed on Vercel, a FastAPI backend deployed on Railway with Nixpacks, and Supabase for authentication and PostgreSQL. The Railway backend deliberately runs as a single Uvicorn process and a single replica. APScheduler, conversational session state, caches, and fire-and-forget work all live in that process; horizontal scaling is not currently a supported deployment mode.

Supabase Auth establishes the Felix user identity. A separate backend-managed Google OAuth connection grants each user Gmail and Calendar access. The browser reads and mutates durable application state through authenticated REST calls, receives draft generation over server-sent events, and uses separate WebSockets for conversational voice and meeting capture.

Gmail remains the mail system of record. Felix mirrors selected inbound and sent messages into PostgreSQL so triage, drafting, search, follow-ups, commitments, relationships, memory, meeting context, and job tracking can share one per-user data model.

## Runtime components and boundaries

### Frontend

- The frontend is Next.js 14 App Router with React 18, TypeScript, Tailwind, and SWR. Vercel is the production host.
- Supabase SSR middleware refreshes auth cookies. The authenticated app layout verifies the Supabase user, then asks the backend whether that user has connected Google before it renders the application shell.
- `frontend/src/lib/api.ts` is the common authenticated REST client. Domain hooks own SWR keys, cache updates, and feature-specific client state.
- `VoiceProvider` owns one conversational voice session across the authenticated shell. The live meeting page separately owns media capture and the meeting WebSocket; these transports do not share lifecycle or audio state.
- The root layout loads Vercel Analytics. Its GDPR/PECR position is **pending assessment: remove it or document and justify its use**.

### Backend API

- `backend/app/main.py` owns application lifespan, database-pool startup and shutdown, the in-process scheduler, CORS, error normalization, and router registration.
- API modules own transport concerns such as authentication, validation, rate limits, and response shapes. Service modules own provider integration and domain behavior. Job modules coordinate scheduled workflows.
- `backend/app/db.py` is a thin `asyncpg` layer and does not add tenant predicates. The backend database role bypasses RLS, so the tenant-isolation rules in [`CLAUDE.md`](../CLAUDE.md) are the effective backend security boundary.
- Google API client calls are synchronous underneath; `google_api.py` supplies the shared thread-offload, timeout, and retry wrapper used by the Gmail and Calendar services. Google service objects are constructed from the current user's credentials rather than stored globally.
- `utils/background.py` keeps strong references to spawned asyncio tasks and logs their exceptions. It is not a durable queue: unfinished tasks disappear if the Railway process exits.

### External systems

- Supabase provides Auth and PostgreSQL. The code also uploads generated briefing audio to a Supabase Storage bucket named `felix-audio` and requests public URLs. **TODO: verify** that the production bucket exists and that its public-access policy is intentional.
- Google provides Gmail, Calendar, OAuth user information, and Speech-to-Text V2.
- Anthropic provides the smart model for drafting, conversational tool use, meeting prep/summaries, and Live Assist answers. `AI_MODEL_FAST` defaults to OpenAI `gpt-5.6-luna` for triage, routing, extraction, session/memory distillation, and Live Assist watch. `ai_service.call_fast` routes Luna through Responses and `claude-*` models through Anthropic; adding another provider requires code.
- ElevenLabs provides streaming speech for voice responses and generated briefing audio.
- OpenAI's embeddings endpoint remains best-effort. `OPENAI_API_KEY` is required at startup when Luna is the fast model; with a Claude fast model it is optional and retrieval can fall back to non-vector signals.

## Subsystem map

### Meeting capture and speech-to-text

**Owns.** Live two-source audio capture, speaker-labelled transcription, persisted transcript segments, capture liveness, and the capture side of the meeting lifecycle.

**Components and flow.** The frontend `useMeetingCapture` hook acquires microphone audio (`me`) and display/share audio (`them`). `meeting-capture-worklet.js` converts each source to mono LINEAR16 at 16 kHz. Binary WebSocket frames carry a one-byte channel prefix followed by PCM. `meetings_ws.py` admits only authenticated, owned capture meetings that are still open, then feeds a `MeetingSTTSession`. That session owns two `MeetingSTTChannel` instances and therefore two independent Google Speech V2 streams, preserving speaker attribution without diarization.

Only finalized recognizer results are stored in `meeting_transcript_segments`; interim text exists only on the live socket. Meeting-relative timestamps are calculated from consumed audio bytes. On reconnect, the STT session seeds its offset from the greatest persisted segment end time so transcript ordering does not restart at zero. Google streams roll over without replacing the meeting-level clock.

**Connections and gotchas.** Final transcript turns feed Live Assist and later become summarizer input. A capture socket also writes a tokenized heartbeat to the meeting row for the second-device viewer. Disconnecting the socket tears down its STT and Assist work but leaves the meeting open for reconnect; REST end or the stale-meeting job changes lifecycle state. The stale job treats a capture meeting as abandoned after ten minutes without a finalized segment, so long silence is indistinguishable from abandoned capture. Manual sessions use a separate 180-minute activity window based on notes and Assist activity. Lifecycle and single-writer requirements are defined in [`CLAUDE.md`](../CLAUDE.md), not repeated here.

### Live Assist

**Owns.** Bounded meeting context, proactive assistance decisions, typed questions, persisted Assist cards, per-meeting inference limits, and the read-only second-device view.

**Components and flow.** `live_assist_service.py` builds `meetings.live_context` from meeting metadata, attendees, recent email, commitments, and memory. `LiveAssistWatcher` consumes finalized transcript turns from the capture pipeline. A deterministic `CandidateGate` decides whether a window merits inference; eligible windows use the fast watch model, with candidate-interview questions optionally routed to a smart-model solve. Stored results are returned as `meeting_assist_items` and emitted to the capture page.

Typed asks converge on `answer_typed_question`. The capture page submits through its existing WebSocket; manual sessions and the phone viewer use REST. Persisted Assist items provide history and restore meeting-scoped counters across reconnects. A request ID identifies a stored answer and can replay it, but current clients create a new ID for each attempt, so it is not a general client retry key.

**Connections and gotchas.** The proactive watcher belongs to the capture socket, while typed asks are transport-neutral. The phone viewer polls a database-backed snapshot and decides whether capture is attached from the persisted heartbeat; it never acquires media or starts STT. Process-local watcher/ask registries provide fast ownership inside the single instance, and PostgreSQL advisory locks protect watcher and ask ownership during process overlap. Those locks are partial distributed-safety groundwork, not evidence that the whole application supports multiple replicas. Live Assist's overlay, viewer, and socket-writing constraints are governed by [`CLAUDE.md`](../CLAUDE.md).

### Meeting notes, summaries, and prep

**Owns.** The durable meeting record, manual notes, meeting lifecycle, versioned summaries, downstream action-item fan-out, and pre-meeting preparation cards.

**Components and flow.** `MeetingService` creates either capture or manual meetings in `recording`. Manual meetings share notes, Assist, end, summary, and history behavior but never enter the audio/STT path. Ending changes the row to `processing` and spawns `summarize_meeting`, which reads finalized transcript segments in meeting-relative order, combines them with user notes, invokes the smart summary model, writes a new `meeting_summaries` version, and moves the meeting to `done` or `error`. An empty meeting becomes `done` without a model call or summary row.

After a summary commits, user-owned action items are offered to `CommitmentService`. Candidate/legacy interview summaries may append a deduplicated note to a matched tracked job when Job Search Mode is enabled. These fan-outs are best-effort and do not roll back the summary.

Meeting Prep is a separate path owned by `MeetingPrepService`. It reads a Calendar event, resolves up to six external attendees, and adds recent attendee mail, memory episodes, open commitments, and user profile context. It stores body-only HTML plus derived plain text in `meeting_preps`, keyed by user and Calendar event. The scheduler pre-generates cards and may email them; the API can generate the next imminent prep on demand. Per-user `meeting_prep_mode` selects in-app, email, both, or off behavior.

**Connections and gotchas.** Summary work runs as an in-process background task after the status transition. There is no durable worker queue; a process exit between those steps can leave a meeting in `processing`, and the current stale-meeting sweep only examines `recording` rows. Failed summaries use `error` as the explicit retry state, while the resummarize endpoint also permits a new version from `done`. State-transition requirements are centralized in [`CLAUDE.md`](../CLAUDE.md).

### Memory, sessions, episodes, and embeddings

**Owns.** User profile memory, cross-conversation summaries, episodic context, optional embeddings, retrieval, and memory maintenance logs.

**Components and flow.** `memory_service.py` implements three durable layers: one `user_memory` profile/preferences row per user, `session_summaries` for completed conversations, and `memory_episodes` for distilled email, meeting, chat, commitment, decision, and summary events. Retrieval combines importance, recency, entity matches, and semantic similarity when a vector column and embedding are available.

`session_manager.py` holds active chat/voice transcripts and per-user locks in memory. Explicit end, a 30-minute inactivity sweep, and graceful shutdown attempt to distill them into `session_summaries`; sessions with fewer than two messages are skipped, and active transcripts retain at most 40 messages. Session summaries with open items can be promoted into chat episodes.

The profile has a short process-local cache. Episode creation can call OpenAI for a 1,536-dimension embedding; the call is best-effort. Scheduled work backfills missing embeddings, extracts profile updates, and prunes low-value old episodes. `memory_operations` records retrieval and maintenance activity for backend observability.

**Connections and gotchas.** Memory enriches drafting, voice/chat, briefings, meeting prep, and Live Assist, but callers treat enrichment as optional context. The in-repository vector migration tries to create pgvector and otherwise creates a text embedding column; `memory_service.py` inspects the actual column type before using vector SQL. The production extension and migration state remain explicitly unconfirmed in the deployment-verification section below. Active sessions and profile caches are single-process state and are a primary blocker to multiple backend replicas.

### Email, inbox, calendar, and briefings

**Owns.** Gmail synchronization and labeling, the local mail mirrors, triage, draft generation and sending, Calendar access and proposals, follow-ups, daily briefings, digests, and weekly reviews.

**Components and flow.** The scheduler calls `inbox_sync.py` every two minutes for connected users. `GmailService` fetches recent inbox messages not carrying the processing label. Each message is triaged, upserted into `emails`, labelled in Gmail, and optionally drafted. The same run starts a separate recent-sent mirror into `sent_emails` so mail written outside Felix remains available to commitments, jobs, relationship profiles, follow-ups, and memory. Per-run Gmail label IDs are cached only within one user sync because label IDs differ by Google account.

Manual draft generation streams smart-model output to the browser using server-sent events and upserts one draft per user/email. Sending claims the draft by moving it to `sending`, calls Gmail, then marks it `sent`; successful sends also trigger follow-up and job-board processing. `GmailService` and `CalendarService` wrap provider operations, while `google_api.py` bounds synchronous Google calls off the event loop.

Calendar reads live data from Google. Conversational creation stores a `pending_calendar_proposals` record, and confirmation later consumes it. `BriefingService` combines priority mail, Calendar, follow-ups, commitments, and memory into one briefing per user/local date. ElevenLabs audio can be generated and uploaded to Supabase Storage. Digest and weekly-review emails are separate scheduled flows owned by `polish_service.py`, which also backs draft polishing, smart templates, and writing-style evolution behind the `/polish` router.

**Connections and gotchas.** The `emails` and `sent_emails` tables are mirrors, not replacements for Gmail. The `felix-processed` Gmail label prevents ordinary inbox reprocessing, so commitment and job scan timestamps remain null on failure and bounded catch-up sweeps revisit those rows. Much downstream work is spawned in-process after the core sync/send operation; database timestamps and unique keys provide retry points, but the tasks themselves are not durable across a process restart.

### Commitments and relationships

**Owns.** Promise extraction in both directions, commitment status, reminder-facing open lists, contact relationship profiles, and ordinary email follow-ups.

**Components and flow.** `CommitmentService` scans inbound `emails` and mirrored `sent_emails`, persists canonical rows in `commitments`, and creates commitment memory episodes. It also accepts user-owned action items from meeting summaries. Open commitments are denormalized onto contacts for display, but `commitments` remains the canonical record.

`RelationshipEngine` incrementally updates a sender's contact record when inbound mail arrives and periodically rebuilds profiles from message history. `FollowUpEngine` is separate from commitments: it derives follow-ups from sent mail, marks them replied when an inbound message lands on the same Gmail thread, checks overdue records, and can draft follow-up text.

**Connections and gotchas.** Automated/newsletter senders are excluded from inbound commitment scanning. Successful scans stamp their source rows; retryable failures intentionally leave the stamp null for catch-up. Meeting-derived commitments deduplicate on meeting and normalized text. Commitment resolution is explicit through the commitments API; it is not inferred from later conversation.

### Job tracker

**Owns.** The gated application board, automatic application-event detection, suggestions requiring confirmation, forward stage progression, follow-up dates, and job-specific draft assistance.

**Components and flow.** `JobTrackerService` accepts inbound and sent email signals from inbox synchronization plus explicit outbound events after Felix sends a draft. A deterministic recall-oriented gate checks known threads/contacts, ATS domains, and job language before calling the smart extraction model. Results at or above the confidence threshold create or update a job; lower-confidence results become `job_suggestions` for the user to resolve.

Gmail thread ID is the first automated identity. When activity moves to another thread, a normalized company match plus conservative role-token overlap is the fallback. `job_events` forms the timeline, and scheduled `job_followup_checker.py` marks due next actions. The API also supports manual jobs/events, suggestion resolution, and job-specific follow-up drafting.

**Connections and gotchas.** `settings.job_search_mode` is the entry gate for automated scanning and the frontend surface. Positive statuses only advance through saved, applied, phone screen, interview, and offer; terminal accepted/rejected/withdrawn states take precedence. Contact email is intentionally not an identity key because ATS and recruiter addresses change. Source-backed timeline events and suggestions use unique keys so retried scans do not duplicate them. Interview meeting summaries may append notes to a matched job without changing its stage.

### Project Hubs (manual workspace)

**Owns.** Private, manually curated projects with name, description, optional date,
active/archived status, linked sources, chronological activity, confirmed scope,
decisions, approvals, milestones, and explicitly generated weekly updates. The routes are
`/projects` and `/projects/[id]`, with desktop/mobile navigation and an Add to
project action on email detail, meeting detail, and commitment cards.

**Storage and ownership.** Migration 022 adds `projects`, three typed association
tables (`project_thread_links`, `project_meeting_links`, `project_commitment_links`),
`project_email_threads`, and `project_activity`. Composite foreign keys carry
`user_id` through both project and source ownership. Thread identities are
registered only after checking locally stored inbound or sent mail; Gmail's
thread ID groups both mirrors and includes subsequent mirrored replies. This
small identity table is not a new mail mirror or retrieval system. Meeting and
commitment links retain a null source reference on deletion, so the UI can show
an unavailable placeholder. This uses PostgreSQL 15+ column-specific SET NULL.

**API and flow.** `ProjectService` backs authenticated list/create/detail/patch
routes under `/projects`, `/sources/search`, `/{id}/sources` (link),
`/{id}/sources/{kind}/{link_id}` (unlink), `/{id}/activity`, and
`/{id}/threads/{thread_id}` (up to 100 locally stored messages). Search includes
archived inbound mail and sent-only threads, with pagination. Links use a
transactional project-row lock and database uniqueness to make concurrent
requests/retries idempotent. Unlink never deletes a canonical source. Meeting
search/link/content reads preserve `meeting_capture_mode`; disabled or deleted
meetings display as unavailable. Commitments are joined live; SWR revalidates
project data after in-app commitment resolution, on focus, and every 30 seconds.
Project links to resolved commitments select the matching status in the existing
commitments page.
Detail and activity constrain the source catalog to this project's links (and
historical action references for activity) before grouping mail. PostgreSQL prunes
unrelated catalog branches for source-kind searches. The Overview requests five
activity entries; only the Activity tab requests a 50-entry page.

**Activity.** Database triggers record actual project metadata changes and
link/unlink operations atomically; duplicate links and unchanged metadata do not
create duplicate activity. Source events are read from currently linked mail,
meeting dates, and commitment creation/resolution timestamps. They retain their
original dates and are labelled separately from project actions. Unlinking
removes those derived source events from the view; durable project action history
remains. Phase 2 adds scope/record changes and canonical commitment changes to
that same activity log, without copying source bodies into it.

**Confirmed knowledge.** `ProjectKnowledgeService` owns `/{id}/knowledge`,
`/{id}/scope`, `/{id}/records`, and explicit meeting-decision review/import routes.
Migration 023 adds append-only `project_scope_versions`, typed `project_records`,
`project_record_evidence`, and `project_record_requests`. Scope saves and approval/milestone edits require an
expected version; project-row locks serialize concurrent mutations. Decisions are
immutable except for supersession: a replacement atomically marks the old decision
superseded and retains its text and evidence. Approvals track a title, optional
owner/deadline, and pending/approved/declined/cancelled status; they send no request
and grant no external permission. Milestones track a target date and
planned/done/cancelled status; passing a date never completes them.
Record creation/import requires a client request UUID. A tenant/project-scoped
request ledger maps each accepted request and input hash to its record, rejecting
reuse with different input. Exact manual decision payloads and the same imported
summary/decision index deduplicate across tabs; a replacement's identity includes
the decision it supersedes. Retries do not add records or activity.

Evidence references point to canonical linked sources. Importing a meeting
decision requires user confirmation and pins its summary ID, decision index, and
text hash. A new summary does not rewrite that reference. Reads recheck ownership,
current project links, the meeting feature gate, and the pinned decision text.
If supporting evidence becomes unavailable, the record's text is withheld while
its history/reference marker remains. Browser roles cannot directly read or write
records, request receipts, evidence, or generated updates; migration 023 revokes their grants so
the API's access revalidation cannot be bypassed. Composite foreign keys and
owner RLS policies provide additional tenant isolation.
An independent `(user_id, summary_id)` foreign key clears deleted-summary
references even when meeting deletion has already nulled `meeting_id`; the
three-column foreign key also enforces that the summary belongs to the meeting.

**Generated updates.** `project_evidence_service.py` deterministically reads only
the project's confirmed state and currently linked local mail, meetings, summaries,
commitments, and project actions. It uses at most 80 evidence items, 2,000
characters per item, and 60,000 serialized evidence characters. Fingerprints cover
all eligible evidence, including omitted items and full-body hashes. Summary
overviews, decisions, and action items receive bounded excerpts; transcripts are
fingerprinted for changes but are not fed directly to the update model. Current
scope and historical revisions are explicitly distinguished.
Mail, activity, and summary fingerprints are aggregated in PostgreSQL; only up
to 80 candidates per set, plus explicitly requested evidence IDs, are returned
to Python. Saved-update reads fetch content for their bounded input manifest,
and citation reads request their specific evidence ID. Fingerprints still scan
all linked rows to detect changes outside the selected context; this is not a
constant-time change log or a cached authorization decision. User-curated scope,
records, and source links are still read in full.

`ProjectUpdateService` handles GET/POST `/{id}/update` and GET `/{id}/evidence`.
Generation is an explicit interactive call using the configured smart model,
monthly quota gate, rate limit, shared thinking policy, untrusted-data wrapper,
versioned `project_update` prompt, and `log_ai_call`. A database token with a
two-minute lease prevents overlapping generation; a 60-second model timeout
bounds the call. The latest successful request ID can be replayed after transport
failure. Output is strict JSON with at most 12 claims, validated categories/time
bases, and 1–4 exact evidence quotations per claim. Citation validation establishes
source/quote validity, not a deterministic proof that every generated statement
is entailed. Failed, cancelled, malformed, or changed-input attempts preserve the
last successful update and never modify confirmed knowledge.

`project_updates` stores generated claims, their full input evidence manifest,
fingerprint, model/prompt version, and reporting window separately from confirmed
state. Reads mark an update stale after evidence, association, timezone, model, prompt,
or local-week changes; if any input evidence is no longer accessible, all prose
is withheld. Citation previews resolve current evidence through the API. Source
changes are shown as stale, and users explicitly regenerate.
Generation builds one input snapshot and one post-call verification snapshot;
the response reuses the verified snapshot rather than building a third one.

**Weekly semantics and UI.** “What changed this week?” uses the user's settings
timezone (UTC fallback), local Monday inclusive to next Monday exclusive, capped
at generation time. Boundaries are localized separately across DST. Emails use
received/sent time, meetings and their summaries use the original meeting time,
and decisions use the user's entered local decision date. Recording/linking an
old item this week is a project action, not a new source event. Milestone dates
are targets. Canonical commitments use their material-change timestamp, with
resolution/creation fallback for pre-023 rows. UI labels distinguish current
context, this week's source events, and this week's project actions. Responsive
Scope/Decisions/Approvals/Milestones tabs keep confirmed state separate from the
generated Overview panel; SWR revalidates availability and stale status on focus
and every 30 seconds. No suggested associations, automatic discovery, shared
access, background generation, or additional retrieval infrastructure is included.

**Migration verification.** `tests/test_projects.py` and `tests/test_project_knowledge.py` use a separately configured
`PROJECT_TEST_DATABASE_URL` to create disposable databases for the complete fresh
schema/migration chain, a populated upgrade, migration reruns, API behavior, real
concurrent writes, composite foreign keys, and RLS. CI provisions PostgreSQL 16;
without that explicit test URL, database-backed tests skip. The migration builds
two supporting unique indexes on existing source tables in 022 and two summary
indexes in 023; allow for locks on a large deployment. Neither migration backfills
existing sources or projects. 023 also adds a nullable commitment modification
timestamp and triggers for material changes, record versions, and activity.

### Authentication and settings

**Owns.** Felix identity, Google provider authorization, encrypted provider tokens, request authentication, Google-connection status, per-user preferences, and feature gates.

**Components and flow.** Supabase Google sign-in and backend Google API authorization are separate OAuth flows. The frontend completes Supabase sign-in at its auth callback. An authenticated Felix user then starts the backend Google connection flow; the public backend callback validates and consumes a short-lived one-time nonce, exchanges the code, fetches Google user information, encrypts access and refresh tokens with Fernet, and upserts `google_connections`.

`get_current_user` validates bearer tokens, preferring local HS256 verification when `SUPABASE_JWT_SECRET` is configured and otherwise asking Supabase. Successful results are cached for 60 seconds in the backend process. `get_google_credentials` decrypts the current user's tokens, refreshes expired access, and persists rotated credentials. The authenticated frontend layout checks both Supabase identity and Google connection before showing the application shell.

The single `settings` row per user stores timezone, briefing/digest behavior, VIPs, writing profile, voice selection, meeting modes, meeting-prep delivery, and Job Search Mode. The precise fields are schema/API details rather than an architectural contract.

**Connections and gotchas.** Invite-only access is enforced entirely by the Google Cloud OAuth project's **TEST USERS** list, with Google's 100-user cap. There is no application-level allowlist in this repository. Moving beyond test users requires Google app verification. The two OAuth flows have different callbacks and allowlists, which must not be conflated during deployment setup.

### AI observability, quota, and evaluation

**Owns.** The per-call AI telemetry log, prompt versioning, cost-weighted usage quota, user feedback ratings, the admin-only inspection surface, and the offline Live Assist quality harness.

**Components and flow.** Every model call in the codebase reports through `log_ai_call` in `ai_service.py`, which writes one `ai_calls` row carrying feature, model, prompt version, input/output tokens, latency, success, parse outcome, error message, and quota accounting. `PROMPT_VERSIONS` in the same module is the authoritative feature-to-version registry; a prompt edit is expected to bump its matching key so that later analysis can segment on `prompt_version`. Live Assist deliberately keeps two watch keys — `live_assist_watch` and `live_assist_interview_watch` — so bumping the interview prompt does not restamp general cards with a version their prompt never had.

Quota is separate from observability but metered from the same table. `check_monthly_ai_budget` in `middleware/rate_limit.py` sums `billable_units` for the current month over `quota_scope = 'interactive'` successful calls only, so background triage, commitment scans, and their retries cannot exhaust a user's interactive budget, and failed calls carry no usage. Admin callers get a separate higher cap. `slowapi` supplies the per-endpoint burst limit on expensive AI routes, keyed on a locally verified JWT subject with an IP fallback so a forged token cannot burn another user's budget.

`ai_feedback` stores per-response user ratings (1 wrong, 2 edited, 3 good). A submitted `ai_call_id` is accepted only when it is a UUID that exists in `ai_calls` **for the calling user**; otherwise it is dropped to null so the rating still lands. The `/admin` routes expose cross-user aggregates — the seven-day per-feature summary, the last twenty parse errors, and performance grouped by prompt version — gated by `_require_admin` against `ADMIN_EMAILS` and written to `admin_audit` on access. Error messages surfaced to admins pass through `redact_pii` from `middleware/pii.py` and are truncated, because provider errors can echo email subjects and bodies. The frontend `admin` page renders these four sections and is gated by a backend `/admin/me` probe rather than a client-side check.

Offline quality evaluation is separate from the functional tests. `evals/assist_benchmark.py` holds annotated meeting scenarios, and `tests/test_assist_eval.py` replays them through the real `CandidateGate`, watch prompt, and acceptance gate with the database and socket bypassed, scoring intervention precision, opportunity recall, groundedness, and redundancy. It is marked `assist_eval`, excluded from the default run, and requires a real API key because it makes live model calls.

Fast calls share a bounded adapter with explicit text, usage, and truncation/refusal handling. Luna uses Responses with `reasoning.effort=none`, `store=false`, and strict schemas for JSON features; application parsing and acceptance gates still apply. The adapter uses the existing `httpx` dependency and retries transport/429/5xx failures at most twice within the total call deadline. Quota weights are 0.2/1.2 for Luna, 2/10 for Sonnet 5, and 1/5 for Haiku (input/output). Older Sonnet models retain 3/15. The existing unscaled `NUMERIC` column stores fractional units; no migration is needed. These are quota estimates, not exact provider invoices, and existing historical rows are not repriced.

To compare fast models, run `AI_MODEL_FAST=gpt-5.6-luna python -m pytest -m assist_eval -s` and `AI_MODEL_FAST=claude-haiku-4-5-20251001 python -m pytest -m assist_eval -s` from `backend` with real provider keys. Intervention precision must reach 0.85; a skipped run is not quality evidence.

**Connections and gotchas.** `ai_calls`, `eval_runs`, and `admin_audit` are operational tables, not user-facing product data: `ai_calls` is service-role only, and the admin aggregates are intentionally cross-user. That is why `tests/test_user_id_discipline.py` — which otherwise requires `user_id` in every raw SQL string under `app/api/**` — carries named exemptions for those specific admin queries. Those exemptions are the audited boundary of the rule, not a general escape hatch. Telemetry writes are best-effort and must never fail the user-facing call they describe.

## Frontend architecture and major surfaces

The App Router separates authentication/onboarding from an authenticated application layout. That layout provides navigation, Google-disconnection handling, auth synchronization, and the global conversational voice context. Most product surfaces are client components backed by a domain hook and the shared API client; the layout performs server-side identity and Google-connection checks before rendering them.

The daily-work surfaces cover home/dashboard, inbox and threads, Calendar, briefings, follow-ups, commitments, contacts, and reusable templates. Job Search Mode adds a board and application detail. Meetings have distinct history/detail, capture/manual, and phone-viewer experiences because only the capture page may own media and STT. Settings centralizes the per-user behavior stored by the backend.

The filesystem router remains authoritative for exact URLs. Of the non-obvious boundaries, the important one is that `VoiceProvider` is global to the authenticated shell while meeting capture belongs to a single live-meeting page and `useMeetingCapture` instance.

## Database and Supabase architecture

`infra/schema.sql` is the repository's base schema. The repository then contains ordered migrations `001_phase2_email_fields.sql` through `023_project_knowledge.sql`. Those files describe source-controlled intent; they are not proof of the schema deployed in Supabase.

Most product tables are user-owned and carry `user_id` plus Row Level Security. Operational tables such as AI/memory logs, admin audit, and digest-send deduplication are backend/service-role only. `google_connections` is also backend-only so encrypted provider tokens never reach an authenticated browser. The backend itself connects with a role that bypasses RLS; see [`CLAUDE.md`](../CLAUDE.md) for the tenant-scoping requirements that follow from that design.

The schema carries durable coordination state where a retried operation has external consequences: draft send status, one briefing per local date, digest-send slots, source scan timestamps, job/source uniqueness, meeting statuses, Assist items, and capture connection tokens/heartbeats. PostgreSQL advisory locks add process-overlap protection for scheduled jobs and Live Assist ownership. These mechanisms cover specific races; they do not make all process-local state distributed.

Migration 007 contains two repository paths for `memory_episodes`: a `vector(1536)` column plus IVFFlat index when pgvector can be created, or a text column when it cannot. Runtime retrieval checks the real column type before issuing vector queries.

- **Verified in Supabase as of `<DATE — I'll fill in>`:** **TODO: verify** the actually applied migration list and pgvector/embedding-column status when the Supabase result is supplied. Until this line is populated, this document makes no claim that deployed database state matches the repository.

## WebSocket ownership

Felix has two independent WebSocket systems:

- Conversational voice is rooted at `/voice/stream`. One connection owns receive, Google STT, intent routing, session accumulation, ElevenLabs TTS, and interruption state for that voice interaction. The global frontend `VoiceProvider` is its UI owner.
- Meeting capture is rooted at `/ws/meetings/{meeting_id}`. One connection owns its two audio channels, two rolling STT streams, capture heartbeat, and proactive Live Assist watcher. The live meeting page is its UI owner.

The capture handler sends admission responses before concurrent work begins, then creates `_SocketWriter`. STT, control responses, and Assist all enqueue through that writer; it is the only task that sends JSON after the concurrent phase starts. Shutdown first drains STT finals, then closes Assist, detaches the tokenized heartbeat, and drains the writer. This is the implementation of the single-writer rule referenced in [`CLAUDE.md`](../CLAUDE.md).

The heartbeat token prevents cleanup from an older capture connection clearing a newer connection's liveness. Although that state is stored in PostgreSQL, the production deployment remains single-instance; the database design primarily supports reconnect/takeover correctness and lays groundwork for future distribution.

## Background work and multi-instance behavior

APScheduler starts inside the FastAPI lifespan and therefore runs in the Railway web process. Production intentionally uses one backend replica. Scheduled work includes inbox/sent synchronization, follow-up checks, briefings and digests, relationship/style refreshes, weekly reviews, meeting prep, stale-meeting finalization, OAuth nonce cleanup, session sweeping, embedding backfill, profile extraction, and episode pruning.

Every registered scheduler function is wrapped in a PostgreSQL advisory lock. In today's topology this protects against an overlapping old/new process during deployment. The lock uses a dedicated PostgreSQL connection because session-scoped locks must remain on their acquiring session and must not occupy the request pool while job bodies run. APScheduler's own `max_instances` behavior supplies same-process overlap protection because PostgreSQL lets one session reacquire its own advisory lock.

Jobs that depend on Google connections load eligible users and isolate per-user failures. Maintenance jobs that do not require Google query their own target data; notably, stale meeting finalization does not require a current Google connection.

Process-local state includes APScheduler itself, active conversational sessions and their locks, the JWT and memory-profile caches, tracked background-task references, Live Assist watcher/takeover state, ask fast-fail slots, and watch-call counters. Spawned tasks are not durable and graceful-shutdown session persistence is best-effort. PostgreSQL stores domain records, selected idempotency markers, scheduler/Assist advisory locks, and capture liveness, but it does not yet replace all of this in-process state.

Multiple Railway replicas are therefore a future architecture step, not an available scaling switch. The existing advisory locks are partial groundwork. Before that step, session state and other process-local ownership must move to shared storage or become safely partitioned, every spawned workflow needs a recovery/handoff design, and scheduler/WebSocket behavior needs an explicit distributed-state audit.

## Deployment and runtime caveats

- The backend is deployed to Railway as one replica. `railway.toml` selects Nixpacks, installs `backend/requirements.txt`, and starts one Uvicorn process with Railway proxy headers enabled. `backend/Dockerfile` is an alternative Python 3.12 container build and is not the active Railway path.
- The frontend is deployed to Vercel. Vercel Analytics is currently loaded globally; GDPR/PECR assessment and the removal-or-justify decision remain pending.
- Invite-only enforcement is the Google Cloud OAuth project's TEST USERS configuration, not application code. It is capped at 100 users, and leaving test-user mode requires Google app verification.
- The API fails startup if required Pydantic settings are missing or PostgreSQL cannot be reached. The database pool is initialized and checked before APScheduler starts.
- Advisory locks require a direct or session-mode PostgreSQL connection. Supabase transaction-mode port 6543 can move statements between server sessions and invalidate the lock design; the deployment uses the session/direct path on port 5432.
- CORS accepts the normalized `FRONTEND_URL`. Google OAuth redirect URIs and Supabase allowed redirects must match the deployed frontend/backend origins. Codespaces uses separate origins for forwarded frontend and backend ports.
- Meeting capture requires browser support for `getUserMedia`, `getDisplayMedia`, and AudioWorklet, and display capture must include audio. Manual notes provide the no-recording path.
- Google provider calls have bounded retries/timeouts. Live Assist and memory apply additional bounded work around optional context and inference.
- Generated briefing audio depends on the externally provisioned `felix-audio` Supabase Storage bucket and its access policy; see the inline TODO above.

## Environment and configuration conventions

Backend development settings come from `backend/.env`, normally copied from the repository `.env.example`. Frontend public settings live in `frontend/.env.local`. Only `NEXT_PUBLIC_*` values belong in browser bundles; Supabase service credentials, Google secrets and tokens, AI provider keys, the token-encryption key, and `DATABASE_URL` remain backend-only.

`backend/app/config.py` defines required backend settings and defaults. `ANTHROPIC_MODEL_SMART` remains Anthropic-only. Set Railway's backend `AI_MODEL_FAST=gpt-5.6-luna` and `OPENAI_API_KEY` to switch fast calls, retaining `ANTHROPIC_API_KEY` for smart calls. Redeploy the adapter code before switching. `ANTHROPIC_MODEL_FAST` is a legacy fallback alias; `AI_MODEL_FAST` takes precedence when both are present, so the old variable can be removed after rollout. Roll back with `AI_MODEL_FAST=claude-haiku-4-5-20251001`. Startup rejects an unsupported fast model or missing provider key. `FELIX_VOICE_CATALOG` is JSON parsed into the allowlisted voice selector.

Environment variables describe deployment-wide credentials, provider/model defaults, origins, quotas, and limits. User-selected product behavior is stored in the per-user `settings` row.

## TODO: verify against deployed infrastructure

- **Supabase schema:** replace the dated placeholder in the database section with the actually applied migration list and pgvector/embedding-column status once supplied.
- **Supabase Storage:** verify that the production `felix-audio` bucket exists and confirm whether its public URL policy is intentional.
