# Design Decisions

## 1. FastAPI over Flask
**Decision:** Use FastAPI as the backend framework.
**Reason:** Native async support, automatic request validation via Pydantic, and
clean dependency injection for the API key. Also gives us an OpenAPI schema
(which we hide from the phone UI) for agent developers.

## 2. SQLite over Postgres
**Decision:** Use SQLite3 (stdlib) for the checkpoint store.
**Reason:** Zero external dependencies, file-backed (easy backup with
`CHECKPOINT_DB`), perfectly adequate for single-instance or persistent-disk
deployments. Postgres migration is planned for v1.0 if multi-instance
coordination becomes necessary.

## 3. Shared API key (single key, not per-agent)
**Decision:** One `CHECKPOINT_KEY` environment variable.
**Reason:** Simplicity for the initial deployment. All agents share one
gateway; they identify themselves via the `agent` field in the request body.
Per-user / per-agent key scoping is on the roadmap for v1.0 when auditability
becomes a requirement.

## 4. Gateway is stateless about agent logic
**Decision:** The server never interprets the question or decides what to do
with the answer.
**Reason:** Decouples the gateway from every agent. Agents bring their own
reasoning, their own follow-up logic. The gateway only moves structured data
(question + answer + reason) between two parties.

## 5. Polling instead of WebSockets
**Decision:** Phone polls `/api/checkpoints?status=pending` every 12 seconds.
**Reason:** Simpler server code, works on any host without special
infrastructure, and the 12-second interval is acceptable for human-paced
decisions. WebSockets or Web Push are on the roadmap for lower latency.

## 6. PWA instead of native app
**Decision:** Build the phone UI as a single HTML file PWA.
**Reason:** Zero App Store friction, instant updates (just deploy the server),
works on both iOS and Android, installs to home screen with icon. No build
tool, no bundler, no dependencies.

## 7. No server-side sessions
**Decision:** Phone authenticates every request with the API key stored in
localStorage.
**Reason:** Stateless server means no session store, no cookies, no CSRF risk.
The API key is the only credential — same model as the agent side.

## 8. Answer is a free-form string
**Decision:** `answer` field is `TEXT`, not an enum.
**Reason:** Works for yes/no verify questions AND for approve-style custom
options without schema changes. The agent can normalize on its side (`answer
== "yes"`, `answer == option_label`).

## 9. Reason captured only on "No"
**Decision:** Reason textarea is only shown when the user taps "No" (not on
"Yes").
**Reason:** The reason for a "No" is the high-information moment — that's when
the agent needs context to adjust its plan. A "Yes" doesn't need explanation.

## 10. Dark-only UI
**Decision:** One dark theme, no light/dark toggle.
**Reason:** The app is used in low-light contexts (bed, evening, quick checks).
A single polished theme is better than two average ones. Theme toggle is on
the roadmap.

## 11. Round-robin agent colors
**Decision:** Agent color chips are assigned from a fixed 6-color palette by
insertion order.
**Reason:** Deterministic without a config file. The same agent name always
gets the same color within a session. Good enough for personal use.

## 12. Hidden /docs and /redoc
**Decision:** `docs_url=None, redoc_url=None` on the FastAPI app.
**Reason:** The API is for machines, not humans. The phone app is the only
human-facing surface. Showing Swagger UI would add no value and leak the API
schema to anyone who finds the URL.

## 13. 404 fallback to index.html
**Decision:** Non-API 404s serve `static/index.html`.
**Reason:** Enables client-side routing if the PWA ever grows to multiple
views. The browser can deep-link to any screen and the server will serve the
app shell.

## 14. No database migrations
**Decision:** Schema is created once with `CREATE TABLE IF NOT EXISTS`.
**Reason:** SQLite is embedded in the server binary. Schema changes are
unlikely and can be handled by dropping/recreating the DB file. Migration
tooling is overkill at this stage.
