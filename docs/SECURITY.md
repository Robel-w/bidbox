# Security and operating boundary

This is a single-institution, single-worker deployment. Do not expose the development `uvicorn` entry point to the internet. The production entry point requires an access password and approval-signing secret; use HTTPS and an institutional identity gateway for verified identities. Shared Basic access and a signed link do not establish a person's identity or committee role. Multi-tenant authorization is not implemented.

## Credentials

Keep `LLM_API_KEY`, `BIDBOX_ACCESS_PASSWORD`, `APPROVAL_SIGNING_KEY`, REST authorization headers, SQL credentials, `AT_API_KEY`, `SMTP_PASSWORD`, and provider tokens in the host secret store. Approver contact details (`APPROVER_EMAIL`, `APPROVER_PHONE`) and SMTP/AT account names belong there too. Do not put values in YAML, chat, Git, container layers, or shell arguments. Manifests contain environment-variable names only. Local Compose may use an untracked `.env`; it is excluded from the Docker context.

Rotate a suspected exposed key at its provider, replace the host secret, restart the service, and verify the connection. Rotate `APPROVAL_SIGNING_KEY` to invalidate existing invitations. Revoke the old credential; deleting a file from Git does not remove it from history. Preserve an incident record without copying the secret.

## Logs and personal data

Audit events are redacted before their hash is calculated. Provider usage totals are recorded, not raw model reasoning. Keys, bearer tokens, contacts, ID-like strings, and signed invitation links are masked. Exact configured secret values are also removed from JSON API responses and downloaded Markdown. Validation errors omit submitted values. Legacy sensitive audit downloads are refused rather than changing their hash chain silently.

The default data classes are public for OCDS; internal for generic REST, read-only SQL, CSV/Excel and county IFMIS; personal for SMS, email, WhatsApp, inbound approvals and mobile money. Personal connectors are disabled until `BIDBOX_ENABLE_PERSONAL_<UPPERCASE_CONNECTOR_NAME>=1`. Configure only required endpoints, tables, fields and recipients. Outbound content effects require an approved payload hash. Connector adapters are trusted application code, not an isolation boundary against malicious Python plugins.

The local data volume contains case state, reports, approval assertions and audit logs. Restrict host access, encrypt/back up the volume, and agree an institutional retention policy before using personal records. Hash chaining detects modification relative to the retained chain; it is not external notarization and does not prevent an administrator rewriting the entire chain.

## Secret scanning

Run `python scripts/scan_secrets.py --history --staged`; install pre-commit hooks with `pre-commit install`. CI fetches full history and runs the same offline scanner. Rules cover common provider tokens, private keys, credential URLs, high-entropy credential assignments and exact configured secret values. Results show paths, lines and blob IDs, never values. The single exact public conformance-test token is excluded; no whole directory is excluded. This rule-based scan is not proof that arbitrary secrets cannot exist.

On 2026-11-17, the initial history scan inspected 499 blobs and flagged two historical versions of the same conformance dummy token. Both were verified to be that exact test fixture, not provider credentials. After that narrow exclusion, no real key was detected by these rules. No provider credential rotation is indicated by those findings alone.

## Deployment limits

Effects are never automatically retried after an ambiguous timeout or service restart. A timed-out synchronous provider call may still finish; check provider state before retrying manually. Run one application worker; durable multi-worker job scheduling and per-user authorization are not implemented. Do not enable the demo fault switches in production. Production notification delivery, external identity and container deployment still require verification in the institution's environment.
