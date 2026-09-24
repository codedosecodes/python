# E-signature platform implementation plan

## 1. Existing architecture

The repository currently contains six standalone Google Colab/Jupyter teaching
notebooks and a one-line README. There is no installable Python package, web
application, dependency manifest, database schema, automated test suite, local
asset library, CI configuration, or deployment configuration. The notebooks
cover introductory Python, object-oriented programming, modules, file handling,
SQLite/SQLAlchemy, and introductory Flask/Django material. They are educational
content rather than reusable application infrastructure and remain untouched.

No screenshots, PDFs, mockups, local design references, or other media assets
are present. Consequently, the product requirements in the project brief are
the initial UX source of truth. The implementation will use original visual
assets and the centrally configured **SignFlow** development brand.

The new application is a conventional Django project. Django provides a mature
authentication/session/CSRF stack, server-rendered progressive enhancement,
an ORM with migrations and transactions, accessible HTML foundations, and a
straightforward path from SQLite in local development to PostgreSQL in hosted
environments. Product code is split between the project package (`signflow`),
the domain application (`esign`), reusable templates, and static assets.

## 2. Existing functionality

Before this project, there was no e-signature functionality. The only remotely
related examples are isolated notebook demonstrations of SQLite, SQLAlchemy,
Flask, and Django commands. They do not provide authentication, persisted domain
entities, tenant isolation, document storage, PDF rendering, or signing flows.

Sprint 1 establishes:

- custom users, organizations, and memberships;
- secure Django signup, login, logout, password reset wiring, sessions, and CSRF;
- active-organization resolution and tenant-scoped query helpers;
- foundational envelope, recipient, document, field, signature, template,
  contact, audit, notification, and signing-session relations;
- a centrally branded, responsive application shell and initial dashboard;
- Django admin, development settings, and automated security/domain tests.

## 3. Missing functionality

After Sprint 1, remaining work includes production object storage, uploads and
conversion, PDF.js rendering, the envelope composer, normalized field editing,
email delivery, secure recipient ceremonies, completed-PDF/certificate
generation, full manage/detail experiences, templates, contacts/settings UI,
search/filtering, corrections, reminders, voiding/expiration jobs, rate limits,
observability, broader integration/E2E coverage, and production deployment.

## 4. Screen inventory

| Area | Screens and important states |
| --- | --- |
| Public auth | Login, signup, password reset request/sent/confirm/complete, invalid/expired reset, logged-out |
| Home | Overview, loading skeleton, populated activity, empty activity, notification popover |
| Manage | Inbox, sent, drafts, action required, waiting, expiring, completed, voided, deleted, bulk-selected, no results |
| Envelope compose | Documents/recipients/message/options, upload progress/error, field editor, validation review, sent confirmation |
| Envelope detail | Summary, recipients/routing, documents, audit history, actions, void/remind/correct dialogs |
| Signing | Invalid/expired link, welcome/review, document navigation, active field, signature modal (adopt/draw/upload), completion/error |
| Templates | List/empty/filter, create/edit editor, role mapping, use confirmation |
| Contacts | List/search/empty, create/edit/delete/import |
| Activity | Audit/event feed and filters |
| Settings | Profile, organization/brand, signing, notifications, security/sessions, sending defaults |
| Errors | 400 validation, 403, 404, 429, 500, offline/network retry |

Responsive variants are required for public auth, navigation, tables, envelope
composition, and especially the signing ceremony.

## 5. Component inventory

- **Foundations:** design tokens, typography, icons, focus ring, spacing, layers.
- **Primitives:** Button, IconButton, Input, Textarea, Select, Checkbox, Radio,
  Switch, Badge, StatusBadge, Avatar, Card, Divider, Skeleton, Spinner.
- **Overlays:** Dialog, Drawer, Popover, Tooltip, DropdownMenu, ContextMenu,
  Toast, confirmation dialog.
- **Navigation:** AppHeader, PrimaryNav, MobileNav, Breadcrumb, Tabs, Pagination.
- **Data:** DataTable, sort header, filter bar, SearchInput, bulk action bar,
  EmptyState, activity timeline.
- **Documents:** FileUpload, UploadRow, DocumentThumbnail, PDFViewport,
  ThumbnailRail, ZoomToolbar, DocumentSwitcher.
- **Envelopes:** EnvelopeStatus, RecipientChip, RecipientEditor, RoutingOrder,
  MessageEditor, AdvancedOptions, action menu.
- **Fields/signing:** FieldPaletteItem, DocumentFieldOverlay, resize handles,
  FieldProperties, required-field navigator, SignatureModal, signature canvas.

Components expose loading, disabled, error, hover, active, and focus states and
use semantic HTML and keyboard interactions.

## 6. Database architecture

PostgreSQL is the production database; SQLite is permitted only for isolated
development and tests. UUID primary keys avoid sequential identifiers.

- `User`, `Organization`, `OrganizationMembership` (role and active status).
- `Envelope` belongs to an organization and sender; it owns recipients,
  envelope-document snapshots, fields, signing sessions, and audit events.
- `EnvelopeRecipient` stores role, routing order, color, authentication policy,
  delivery/signing status, and timestamps.
- `Document` is an immutable organization-owned source object with storage key,
  MIME/size/page metadata, and SHA-256. `EnvelopeDocument` snapshots ordering
  and completed-version storage/hash without replacing the original.
- `DocumentField` stores page number and normalized x/y/width/height decimal
  coordinates plus typed validation/options/value metadata.
- `Signature` records adopted/drawn/uploaded representation metadata and a
  private storage reference where relevant.
- `SigningSession` stores only a SHA-256 token hash, recipient/envelope scope,
  expiration, revocation, completion, and idempotency key.
- `Template`, `TemplateDocument`, `TemplateRecipient`, and `TemplateField`
  mirror reusable roles and placements.
- `Contact`, `Notification`, and append-only `AuditEvent` are organization
  scoped. Relevant tenant/status/time/search columns have indexes and relational
  constraints. Unique constraints prevent duplicate memberships, positions,
  token hashes, and idempotent completion records.

Domain writes that span recipient, envelope, audit, and notification rows use
`transaction.atomic()` and row locks. Sent snapshots are immutable except via
an explicit, audited correction workflow.

## 7. API architecture

Versioned endpoints live below `/api/v1/`; HTML views call the same service
layer. JSON bodies are schema validated, errors use stable codes, and every
authenticated endpoint resolves the active organization before querying.

- `/auth/*`: framework-backed browser authentication and recovery.
- `/organizations/*`: membership and active-workspace selection.
- `/envelopes`: tenant-scoped list/create/search/filter/pagination.
- `/envelopes/{id}`: detail and draft-only update.
- `/envelopes/{id}/documents`: initiate upload, finalize, order, preview URL.
- `/envelopes/{id}/recipients`: CRUD and routing order.
- `/envelopes/{id}/fields`: validated normalized-coordinate CRUD.
- `/envelopes/{id}/send`, `/reminders`, `/void`, `/correct`, `/clone`:
  service-backed commands rather than generic updates.
- `/signing/{token}`: token resolution; field response and idempotent finish.
- `/envelopes/{id}/downloads/{kind}`: authorized signed, source, certificate,
  or ZIP streaming.
- `/templates`, `/contacts`, `/notifications`, `/settings`: tenant-scoped CRUD.

Opaque UUIDs are identifiers, never authorization. Signing URLs contain a
separate high-entropy secret. Object downloads use short-lived signed URLs or
an authorized streaming endpoint.

## 8. Security considerations

- Authenticate with Django's hardened password hashing and rotate sessions on
  login; use secure/HTTP-only/SameSite cookies and HTTPS/HSTS in production.
- Resolve organization membership server-side and include `organization_id` in
  every repository query. Object-level sender/member/recipient policy checks are
  mandatory; UI visibility is never treated as authorization.
- Retain only SHA-256 token hashes, use constant-time comparisons where needed,
  apply expiration/revocation/scope checks, and redact secrets from logs.
- Enforce CSRF on browser mutations, restrictive security headers and CSP,
  contextual output escaping, trusted-host configuration, and rate limits on
  login, reset, signing-token, upload, send, and reminder endpoints.
- Stream uploads to private object storage after extension, MIME signature,
  size, malware, and PDF parser checks. Ignore user-provided paths/names.
- Encrypt transport and storage, separate signature/document keys, minimize IP
  retention, define deletion/retention policy, and never log document contents,
  signatures, passwords, or raw tokens.
- Audit privileged reads and all lifecycle writes. Application code cannot
  update/delete audit rows; production DB permissions should reinforce this.
- Perform dependency/SAST/secret checks and restore drills before release.

## 9. PDF/document architecture

Original files are immutable private objects. Upload finalization computes a
SHA-256, records verified metadata, and queues safe preview/thumbnail and DOCX or
image-to-PDF conversion. An `EnvelopeDocument` points to the source and captures
the envelope order/version. Completion creates a separate flattened PDF and
hash; it never overwrites the source.

PDF.js renders lazy multi-page canvases. Each page has a same-size overlay.
Field positions are normalized to page dimensions (`0..1`) and converted at the
render boundary, preserving alignment under zoom and resize. Page rotation and
PDF crop/media boxes are part of conversion. The completion worker maps the
same normalized coordinates back to PDF points, embeds signature images/fonts,
then hashes and stores the result. Signed URLs are short lived.

## 10. Signing architecture

Sending validates a document, actionable recipients, valid emails, and required
signature fields, then freezes a snapshot and creates high-entropy, hashed,
expiring sessions for only the first routing group. The provider abstraction
sends invitations after transaction commit.

Token resolution verifies hash, expiry, revocation, envelope state, recipient,
and routing eligibility. Viewing and responses append audit events. Known name,
email, company, title, and signing date values can be server-derived. Signature
assets are stored privately and referenced by `Signature`.

Finish accepts an idempotency key and, in one locked transaction, revalidates
the token and required fields, persists responses/signature references, marks
the recipient signed, appends audit events, activates the next routing group or
completes the envelope, and queues rendering/email only after commit. Repeated
finish requests return the original result without duplicate events.

## 11. Audit architecture

Audit events are append-only and include organization/envelope, event type,
actor classification and safe identifier, optional recipient, UTC timestamp,
minimized IP/user-agent, and structured non-secret metadata. An `AuditService`
is the only application writer. Events cover creation, upload/hash, edits,
send/delivery/view, signature adoption, field/recipient completion, routing,
reminders, corrections, void/expiry, completion/render, and downloads.

Certificates are generated from immutable envelope/document hashes, recipient
outcomes, and the ordered event stream. The certificate itself is stored and
hashed. A later hardening sprint can add hash chaining or external timestamping.

## 12. Testing strategy

- **Unit:** lifecycle transition table, recipient routing, email/field
  validation, coordinate round trips and bounds, token generation/hash/expiry,
  permissions, hash computation, idempotency.
- **Integration:** tenant isolation; signup/session/recovery; envelope draft,
  upload finalization, recipient/field CRUD, atomic send, token resolution,
  signing completion, next-recipient activation, void/remind, downloads/audit.
- **E2E (Playwright):** signup through sender dashboard; upload sample PDF, add
  Jane, place required signature/date, send, consume captured email link, adopt
  and finish, verify completed status, signed download and certificate/audit.
- **Quality:** formatting, linting, typing, Django checks/migration drift,
  accessibility scan, security headers, dependency audit, visual snapshots,
  responsive browser runs, and zero console/network errors.

Tests use isolated organizations and factories. Storage/email/worker providers
have deterministic local adapters, while integration contracts cover hosted
providers. PDF fixtures are generated by project code and contain no third-party
branding.

## 13. Migration strategy

1. Introduce the custom user model before the first application migration.
2. Add organization/membership and foundational domain tables with UUID keys.
3. Keep local SQLite compatibility while CI exercises PostgreSQL migrations.
4. Add constraints/indexes concurrently where PostgreSQL and table size require.
5. Use additive nullable columns, backfill jobs, then constraints for live
   changes; do not combine destructive schema changes with feature rollout.
6. Version object metadata separately from binaries and verify hashes during
   any storage migration.
7. Back up and rehearse rollback/restore before production migrations.

The legacy notebooks require no data migration and remain separate educational
artifacts.

## 14. Ordered sprint plan

1. **Foundation (current):** Django project, auth/recovery, tenant data model and
   authorization helpers, brand configuration, shell/dashboard, migrations,
   admin, tests, PostgreSQL-ready settings.
2. **Home and Manage:** server-side search/filter/sort/pagination, status
   counters, activity, notifications, table actions and empty/loading states.
3. **Envelope composer:** draft service, private upload adapter, documents,
   recipients/routing, contacts autocomplete, message/options and validation.
4. **Document editor:** PDF.js, thumbnail rail, zoom/lazy pages, normalized field
   placement, move/resize/keyboard operations and properties.
5. **Send pipeline:** frozen snapshots, state machine, token service, provider
   email abstraction, invitations/reminders/expiry and transactional audit.
6. **Signing ceremony:** secure token shell, required-field navigation, form
   validation, adopted/drawn/uploaded signatures/initials, responsive UX.
7. **Completion:** atomic/idempotent finish, routing, PDF flattening, hashes,
   completion email, certificates and authorized PDF/ZIP downloads.
8. **Templates:** roles/documents/fields editor, duplication, folders, mapping
   into draft envelopes and usage tracking.
9. **Contacts and settings:** CRUD/import, profile/organization/brand,
   notification/signing/sending/security preferences and session management.
10. **Production hardening:** corrections, custom folders, queues, rate limits,
    CSP/malware scanning, accessibility, observability, performance, broad E2E,
    responsive and pixel-level visual refinement.

Each sprint exits only after format, lint, type, unit/integration/E2E checks as
applicable, migration drift checks, a production-like startup, browser workflow
and responsive/console review, and a focused commit.

## 15. Acceptance criteria

The release is accepted when a tenant-isolated authenticated sender can upload a
validated private PDF, configure ordered recipients, place persistent normalized
fields, send via a secure expiring link, and the recipient can complete all
required fields using a genuine signature representation. Completion must be
atomic and idempotent, route subsequent recipients, create an immutable event
history, generate separate hashed signed output and certificate, notify the
sender, and expose authorized downloads.

Manage search/filters/actions, templates, contacts, settings, notifications,
void/remind/correct/expiration, and account recovery must work without deceptive
controls. Server authorization and tenant isolation tests must cover every
mutation. The critical E2E path must pass against production-like PostgreSQL,
private storage, captured email, and worker adapters. Desktop, tablet, and mobile
signing layouts must be accessible, keyboard operable, visually polished, and
free of browser console, hydration, uncaught runtime, and broken-request errors.
