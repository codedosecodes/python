# Implementation status

## Implemented

- Account signup/login/logout/password-reset wiring, organization creation, and
  active membership resolution.
- Tenant-scoped envelope dashboard, Manage search/status filter, details, and
  authorized private downloads.
- Persistent draft editing for message/options, multiple validated PDF uploads,
  recipients, roles, routing order, and normalized document fields.
- Private local storage abstraction with generated keys, size/header/extension
  validation, hashes, and no public media route.
- Transactional send validation, lifecycle audit events, hashed expiring signing
  sessions, file-backed development email invitations, and first routing group.
- Token-only signing ceremony, required-value validation, adopted signatures,
  idempotent completion, sequential/parallel routing activation, notifications,
  completed document versions, certificate PDF generation, and downloads.

## Tested

- Framework-independent lifecycle, routing, signing-token, coordinate, and PDF
  generation unit tests.
- Django integration scenarios are authored for authentication and tenant
  isolation. Full Django execution requires the pinned dependencies.

## Remaining

- Generate and verify the initial Django migration against SQLite/PostgreSQL.
- PDF.js multi-page rendering, thumbnail/zoom controls, true drag/resize field
  overlays, drawn/uploaded signatures, and flattening values onto completed PDFs.
- Email verification, contacts/templates/settings CRUD, reminders/void/
  corrections/expiration jobs, ZIP downloads, and Playwright coverage.
- Production S3 adapter, async jobs, rate limiting, malware scanner adapter,
  accessibility/browser/security audits, and final visual refinement.

## Known issues

- This execution image cannot fetch Django from the configured package index;
  Django system/integration tests and migration generation remain unexecuted.
- Completed PDFs currently preserve the immutable source as a separately hashed
  completed artifact; field/signature flattening is not yet available.
