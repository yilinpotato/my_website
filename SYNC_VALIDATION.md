# 2026-10-18 source synchronization validation

This synchronization archives the deployed services and adds locally maintained frontend/game source. It does not deploy new application behavior or switch the public domain.

- Public and private candidate trees passed Gitleaks v8.30.1 scanning. The repository security scan passed after permitting exact reviewed dummy credentials in tests and documentation.
- Runtime databases, uploads, environment files, certificates, logs and actual access passwords are excluded. Example credentials are placeholders and must be configured separately. Public profile contact information and upstream license notices remain.
- Tencent standby verification: seven web services active, ten SQLite snapshots passed integrity and table-count checks, eighteen route statuses matched the source baseline. Notification and certificate timers remain disabled.
- Budget tests: 9/9 passed. Reset subscription tests: 9/9 passed.
- QQ tests: 26/29 passed. Three assertions concerning card naming/order and command/help wording also fail on the unchanged production source export.
- Admin tests: 94/98 passed. Two existing authentication/dashboard expectations fail; two deployment checks cannot complete in the Windows environment (nginx and ACL shell tooling). The unchanged source export has the same failing cases.
- Frontend tests: 30/31 passed. The existing homepage card-count assertion expects five cards while the current source has seven; the unchanged source export has the same failure.

These tests do not establish successful real-user login, uploads, model calls, mail delivery, third-party callbacks or final domain cutover. No production code was changed to suppress these pre-existing failures.
