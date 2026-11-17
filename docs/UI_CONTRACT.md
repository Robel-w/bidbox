# UI contract and Phase A audit

Audit date: 2026-11-17. This table was written before implementing the new integration.
Baseline commit: `d5d4ecf`; supplied `app.html` Git blob: `b373f444f7a00c1838ebefb9ca497afe293251f3` (index and working file match).

## Files and scope

- `app.html`: supplied connector gallery and six-step connector wizard only. There is no Workspace, Review, Approval, Report, Economics, EventSource, or HTML form submission in this file.
- `apps/web/public/landing.html`: currently served at `/`; preserves the supplied `index (57).html` design with local fonts and separate `landing.js`/`landing.css`.
- `apps/web/public/index.html` plus `app.js`: existing review workspace, currently served at `/app`, `/app/`, and `/architecture`.
- Preserve all three designs. The requested routing will make the landing `index.html`, the supplied wizard `app.html`, and retain the existing review workspace separately rather than inventing absent review screens in the supplied wizard.
- All custom backend routes are currently in `bidbox/web.py`; inspection of the loaded app found no additional application routers.

## Every supplied UI request and every response field consumed

One `fetch(u,o)` wrapper exists at `app.html:50`. It parses JSON for every response, defaults to GET, and reads errors as `(d.error || d).{code,message,fix,steps}` (`steps[]` may be strings or objects with `status`). There are no `EventSource(...)` calls. Seven request forms follow; these represent six distinct method/path pairs because the drill shares the sample-test path.

| Source | Trigger | Method and path | Request/query | Every response field consumed | Current backend diff |
| --- | --- | --- | --- | --- | --- |
| 142–146 | Inspect URL/provider/database, upload, PPIP or sample | POST `/api/connectors/inspect` | JSON `{preset,url?,engine?,secret_env?,provider?}`; multipart `{preset,file}`; sample `{preset,sample:true}`; PPIP `{preset:'ocds',feed:'ppip'}` | `steps[].status` or step strings; `mapping[]` or `fields[]`; each mapping's `field,sample,mapping,confidence,suggested,target`; `actions[].name,label,consequential`; `name`; `type`; `records`; `field_count` or `fields.length`; `warnings[].message` or warning strings; `preview[]` arbitrary row keys/values; `env_vars[]` strings or `{name}`. Missing steps are incorrectly treated as `ok`. | **Missing** |
| 149,151 | Check access | GET `/api/connectors/{name}/credentials/check` | Name URL-encoded; no body | `present` or `results` object indexed by environment-variable name; each value treated as a boolean | **Missing** |
| 149,152 | Sample read | POST `/api/connectors/{name}/test` | No body | `ok`, `latency_ms`, `summary` or `message`; missing `ok` incorrectly means passed | **Missing** |
| 149,153 | Failure drill | POST `/api/connectors/{name}/test?drill=failure` | `drill=failure`, no body | `outcomes` or `results` or root array; `[].kind` or `name`; `[].outcome` or `result`; failures swallowed to `[]` | **Missing** (same missing test route, different operation) |
| 98,120 | Launch confirmation | POST `/api/connectors/{name}/confirm` | JSON `{actions: {[actionName]: boolean}}` | `setup_duration_s`; UI unconditionally displays Sandbox on success | **Missing** |
| 155–156 | Gallery load/retry | GET `/api/integrations` | No body | Root array or `integrations` or `items`; each `name` or `id`, `status` or `mode`, `last_check`, `setup_duration_s` | **Exists but mismatched**: `integrations`/name/status/check work; setup duration absent; UI incorrectly labels error/not_configured as sandbox |
| 157 | Gallery Test | POST `/api/integrations/{name}/test` | No body | `latency_ms`, `summary` or `message`; errors read `message` | **Exists but mismatched**: returns `detail`, `raw`, `status`, `elapsed_ms` rather than summary/message/ok; default errors use `detail` |

Result for these seven request forms: **0 fully matching, 2 mismatched, 5 missing**. No sample data or successful execution has been inferred from absent fields.

### Routes and non-network interactions in the supplied UI

| Source | Route/control | Behavior and audit finding |
| --- | --- | --- |
| 91,155,158–159 | `#connectors`, `#connectors/new`; empty hash becomes `#connectors` | Gallery versus wizard. All other hashes silently show gallery. Close returns to gallery; no landing/workspace link. |
| 135–148 | URL/engine/secret-name/provider/name inputs; Browse and drop; mapping and action changes | Mutate browser-only `D`. No draft request. Changing source does not invalidate all previous inspection/test state. |
| 78,131,136–148 | `touch()` / Developer YAML editor | Claims “Draft saved” without any persistence; edited YAML is never sent to the backend. |
| 80–87,129 | YAML generator/validator, Copy/Download | Generates a second, incompatible manifest locally and downloads a Blob; validator is not the backend model. Blob URL is not revoked. |
| 97 | Recommended settings | Applies server mapping suggestions; enables nonconsequential actions locally. No approval recorded for toggling effects. |
| 104,140,145 | Optional REST specification file | Selection updates `D.spec`, but inspection never includes it in the request. |
| 118,150 | Credential setup Copy | Builds `export ENV=YOUR_VALUE`; values are placeholders, not credentials, but command is not a host-secret-store setup. |
| Entire file | Form submissions / streaming | No `<form>`, `onsubmit`, or EventSource. Buttons make the requests above. |

## Current FastAPI route inventory

This is the actual loaded route inventory before Phase B, not just string matches. Generic `dict`, `list[dict]`, and absent response models do not document the JSON fields accurately.

| Method | Path | Current return/response declaration |
| --- | --- | --- |
| GET, HEAD | `/openapi.json` | Framework OpenAPI JSON |
| GET, HEAD | `/docs`, `/docs/oauth2-redirect`, `/redoc` | Framework documentation |
| GET | `/` | FileResponse: landing.html |
| GET | `/app`, `/app/` | FileResponse: existing review index.html |
| GET | `/api/capabilities` | dict: capability/configuration descriptions |
| GET | `/api/data/status` | dict: measured origin counts and coverage |
| GET | `/api/integrations` | dict: integrations/refused/generated_at/add_a_system |
| POST | `/api/integrations/{name}/test` | dict: name/status/detail/latency_ms/elapsed_ms/raw |
| GET | `/api/data/search` | dict: hits/status/origin/sandbox_fallback_used; q/limit/awards_only query |
| GET | `/api/data/process` | dict: stored process, record and fiscal membership; ocid query |
| GET | `/api/runs` | list[dict]: modern and replay summaries |
| GET | `/api/runs/{run_id}` | dict: run detail/evidence/events/metrics |
| GET | `/api/runs/{run_id}/source` | dict: authorized fixture source; ocid query |
| GET | `/api/runs/{run_id}/events` | StreamingResponse: JSON audit events, ids, heartbeat, seq/Last-Event-ID resume |
| GET | `/api/runs/{run_id}/report` | Response: Markdown, not JSON |
| POST | `/api/runs/{run_id}/approve` | dict: approval audit event; ApprovalRequest body |
| GET | `/health` | untyped JSON: status/version/llm_configured/data |
| GET | `/api/cases` | untyped list of non-replay summaries |
| POST | `/api/cases` | untyped: 202 run_id or 200 status/candidates/detail; CaseRequest body |
| GET | `/api/runs/{run_id}/metrics` | untyped run metrics |
| GET | `/api/runs/{run_id}/metrics/download` | JSON attachment |
| GET | `/api/runs/{run_id}/audit/download` | JSONL attachment; sensitive legacy logs refused |
| GET | `/api/runs/{run_id}/verify` | untyped run_id/valid/error |
| GET | `/api/runs/{run_id}/report/download` | JSON attachment |
| GET | `/api/economics` | untyped population metrics for modern cases |
| GET | `/architecture` | FileResponse: existing review index.html |
| GET | `/api/architecture` | untyped SVG wrapper |
| GET | `/api/runs/{run_id}/sequence` | untyped SVG wrapper |
| Mount | `/` | StaticFiles(public), includes arbitrary existing public assets, no `/assets` mount |

### Requested core routes versus existing backend

None of these core calls is made by the supplied wizard; the existing workspace is the working client for them.

| Required contract | Audit status | Required adjustment |
| --- | --- | --- |
| `/` index.html, `/app` app.html, `/assets/*` | Exists but mismatched | Route/filename separation; retain review workspace and links; safe static cache policy |
| GET `/api/data/status` | Exists, data works; schema incomplete | Document actual counts/coverage/source fields; retain real data |
| GET/POST `/api/cases` | Exists, behavior works; schema incomplete | Typed summaries/intake response and common errors |
| GET `/api/runs/{id}` | Exists, behavior works; schema incomplete | Typed detail and nullable measurements; keep replay flag |
| GET `/api/runs/{id}/events` | Exists | Document stream and test replay/live/resume; no EventSource in supplied wizard |
| GET `/api/runs/{id}/metrics` | Exists, schema incomplete | Typed actual metering; no estimates |
| POST `/api/runs/{id}/approve` | Exists | Keep content-bound approval/token checks; standardize errors without weakening gate |
| GET `/api/runs/{id}/report` | Exists but response differs from generic JSON assumption | Preserve explicit Markdown consumer; document media type and separate JSON download |
| GET `/api/runs/{id}/verify` | Exists, schema incomplete | Typed valid/error result |
| GET `/api/runs/latest/verify` | Missing semantic route | Existing parameter route treats `latest` as literal run ID; implement before that route |
| GET `/api/economics` | Exists, schema incomplete | Preserve population denominator, null values and actual working |
| GET `/api/integrations` | Exists but mismatched | Measured timer/trust metadata, credentials env_name, honest errors versus mode |
| POST `/api/interest` | Missing | Consent, bounds/email validation, DuckDB storage, IP rate limit; no email logging |
| All errors/security | Exists but mismatched | JSON errors currently use detail; early auth/cross-origin failures lack shared headers; APIs no-store already, static cache policy absent |

## Invented content and hard-coded assumptions

| Source | Content | Resolution |
| --- | --- | --- |
| 53–54,100,105–110 | PRE/ACC and preset/format chips; planned WhatsApp/mobile-money arrays | Fetch shared presets and accepted inputs; keep only actual adapters clickable. UI instructions/icons are static presentation, not records. |
| 55,93 | `W=[.5,1,1,1,1,.5]` and “about N min left” | Remove invented duration; show actual step progress and backend measured setup duration only. |
| 78 | “Draft saved” on every local edit | Only show saved after successful draft persistence; local edits show unsaved. |
| 80–87 | Browser-generated `kind/source/fields/credentials/actions/mode` YAML; limited homemade validation | Replace with server-generated shared ConnectorManifest YAML/validity. |
| 108 | PostgreSQL, MySQL, SQL Server | Existing adapter implements DuckDB/SQLite only; remove unsupported clickable engines, describe remaining engines as planned. |
| 109 | Generic SMS gateway, SMTP labels | Use actual Africa's Talking/SMTP presets and credential names. |
| 110,146 | “Use sample file”, `sample:true` | Must inspect a real repository fixture with sandbox provenance; never fabricated rows. |
| 105,146 | PPIP shortcut | Must resolve a known actual configured feed; bounded inspector may reject a bulk file above 25 MB. No HTML-shell substitution. |
| 143 | Missing inspection steps default `ok` | Missing steps must remain skipped/not reported. |
| 75,119,124 | Missing `test.ok` is passed; any nonempty drill list is passed | Require explicit successful check outcomes. |
| 120,125,156 | Hard-coded Sandbox; any non-live/non-planned status is Sandbox | Show verified live/sandbox/planned only, otherwise `-` plus plain error/fix/retry. |
| 61,72,115 | Hyphenated example county-registry and name validator | Shared manifest accepts snake_case; use the shared naming rule instead of silently changing stored names. |
| 118,150 | `YOUR_VALUE` setup command | Server supplies env names and safe interactive/secret-store setup instructions; never accept secret values. |
| 48,55,56 | SVG paths, step names, empty state arrays/objects | Static UI/state initialization, not fabricated source data; retain. |
| Entire file | Hard-coded sample records/statistical results | No hard-coded procurement rows found. Numbers are UI sizes/limits/step estimates or response-derived counts/confidence. |

## Canonical implementation contract (binding for subsequent phases)

The supplied UI must change only where it conflicts with these requested/shared contracts; do not implement an incompatible second manifest format to satisfy its homemade YAML.

| Method/path | Request | Response fields / behavior |
| --- | --- | --- |
| GET `/api/connectors/presets` | none | `presets[]: id,name,description,icon,status,clickable,source{kind,accepted,max_mb,engines?}`; status is live/sandbox/planned or null when unverified |
| POST `/api/connectors/inspect` | JSON preset/url or multipart preset/file; explicit supported provider/engine metadata and sample shortcut | `ok,inspection_id,detected_type,record_count,fields[{path,type,sample,suggested_map,confidence}],preview_rows,warnings,steps[{id,status,detail}],error`; optional `env_vars,actions,name,source` from actual adapter/inspection; steps status ok/error/skipped |
| POST `/api/connectors/draft` | `preset,name,source,mapping`; source references retained bounded/redacted inspection, never a client filesystem path | `draft_id,manifest_yaml,validity{valid,issues[{line,message}]}`; server persists canonical shared model, expires drafts |
| PUT `/api/connectors/draft/{draft_id}` | Same mutable draft fields (and optional manifest_yaml for developer editing, validated with the same model) | Same draft response; changed source/mapping/actions invalidates prior test |
| GET `/api/connectors/{name}/credentials/check` | no secrets accepted | `credentials[{env_name,present}],setup_line`; neither request nor response carries secret values |
| POST `/api/connectors/{name}/test` | none | `ok,latency_ms,sample_summary,checks[{id,status,detail}]`; health plus sample read through Connector.perform, never sends |
| POST `/api/connectors/{name}/drill` | none | `outcomes[{kind,error_type,retry_behavior,continues,detail}],injected:true`; isolated simulated faults through shared error/retry logic, no provider effects |
| POST `/api/connectors/{name}/enable` | `actions:[name],mode`; effects additionally require recorded approval | `name,status,setup_duration_s,actions`; conformance mandatory, default effects off, real production probe before live |
| GET `/api/integrations` | none | `integrations[]` with name/kind/status/health_state/detail/last_check/setup_duration_s/credentials[{env_name,present}]/actions[{name,consequential,approval_required}]/trust_tier; null unverified status is displayed as `-`, never sandbox |
| POST `/api/integrations/{name}/test` | none | Same shared test response as connector test; no duplicated test implementation |
| Any JSON failure | none | `{error:{code,message,fix}}`; inspection additionally preserves its structured steps and `ok:false`; sensitive values never echoed |

Source uploads are temporary and deleted after inspection. A draft may persist normalized, redacted records for the file adapter on the data volume, not the original upload. Inspection alone creates no enabled connector and sends no notification. No public writable Python adapter/code or free SQL is accepted. New dynamic connectors use only built-in vetted adapters/configurations. Registry refresh must read durable connector state without executing arbitrary uploaded code.
