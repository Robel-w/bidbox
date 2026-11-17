# Approval boundaries

The Integrations page generates each connector action's policy from its manifest. `Connector.perform` enforces `consequential: true` before invoking the adapter, independently of UI or model instructions. A caller cannot change the action's read/effect mode. An approval must identify this run and action, a name, role and approve decision, and the exact outbound payload hash. Failed conformance excludes the connector from MCP tool discovery.

| Effect | Gate | Enforcement |
| --- | --- | --- |
| Create investigation file | Required; binds the reviewed report and limitations | `create_investigation_file_tool` → `require_report_approval` |
| Send report copy through SMS/email | Required; exact payload bound | `Connector.perform` → `require_connector_approval` |
| Send approval invitation | Not a case-content approval; only case ID and signed link | Fixed recipient from host environment, manifest allowlist, personal-data opt-in |
| Generic configured REST effect | Must declare consequential and act mode | Connector base gate; REST adapter rejects ungated writes |
| Payment, WhatsApp, inbound SMS/USSD, county IFMIS | Planned; no effect implemented | Planned connector refusal |

An independent reviewer sees the retained findings, sources, rationale, uncertainty, follow-up and limitations. Name, role and reason are mandatory. The browser submits the report hash it displayed. The API rejects a changed report, stale request, duplicate decision or invalid audit chain; the report tool rechecks the hash before writing. Rejection records a reason and creates nothing. Approval means permission to create a review file, not an award decision or a guarantee that findings are correct.

Set `APPROVAL_CONNECTOR=smtp_email` or `africastalking_sms`, the appropriate provider settings and personal opt-in, `PUBLIC_BASE_URL` and `APPROVAL_SIGNING_KEY`. Invitations expire after one hour, are bound to run and report hash, and are consumed once. Tokens are in the URL fragment and omitted from logs. Provider acceptance is reported as `accepted`, not confirmed delivery. Missing settings remain `not_configured`; delivery is never simulated.

The service records asserted names and roles. Verified institutional identity and separation of requester/approver roles require an access gateway; possession of the signed invitation is not identity proof. Development mode permits unsigned local approvals when no signing key is configured; production startup refuses that configuration. If delivery fails or a link expires, an operator must correct configuration and issue a fresh invitation through the case service after checking current state; no public resend endpoint is exposed.
