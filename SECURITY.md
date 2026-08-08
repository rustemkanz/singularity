# Security Policy

## Scope

Singularity is a local workflow helper that can read and mutate provider resources when configured with valid credentials. Treat security issues accordingly.

Examples of security-relevant issues include:

- credential leakage
- unintended work-item or pull-request mutations
- insecure transport defaults
- unsafe handling of downloaded attachment context
- command or argument injection paths

## Reporting

Do not open public issues for suspected vulnerabilities.

Report them privately to Rustem Kanzafarov at rustem.kanz@gmail.com. If the repository later enables GitHub private security advisories, that path is also acceptable. Include:

- affected version or commit
- reproduction steps
- impact assessment
- any proposed mitigation if available

## Handling expectations

- maintainers should acknowledge receipt promptly
- fixes should be prepared privately when practical
- public disclosure should follow after a fix or mitigation is available

## Operational guidance

- use explicit environment configuration rather than hard-coded credentials
- configure `GITLAB_BASE_URL` as the exact HTTPS origin that may receive `GITLAB_TOKEN`
- keep Azure DevOps attachment authentication limited to the configured organization; off-origin media is denied by default and must be explicitly allowlisted with `AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS`
- allow only exact public HTTPS media origins; every redirect and DNS result is revalidated, private/link-local destinations are rejected, and the connection is pinned to the validated address
- each media operation permits at most 50 references, 100 MiB total, 16 validated address attempts, five redirects per URL, and 120 seconds wall-clock time; artifact files are created exclusively without following symlinks
- the DNS-pinned media transport connects directly and intentionally does not honor HTTP(S) proxy environment variables; proxy-only networks must permit direct Azure DevOps and explicitly allowlisted media origins
- use unredirected authorization headers and manual redirect validation on transports that handle provider-controlled or content-derived destinations
- leave TLS verification enabled in normal use
- treat work-item attachments and screenshots as potentially sensitive
- review every generated external mutation and its `sha256:` Plan ID; after explicit approval, rerun the otherwise unchanged command with `--apply <PLAN_ID>`
- Plan IDs are persisted in a private local store, bound to the preview Git worktree identity, expire after one hour, and are atomically consumed before the provider request; a mismatch, expiry, or reuse requires a fresh preview and approval
- pending and consumed plan records contain hashes and approval metadata only; mutation targets, comment text, PR descriptions, and other payload values are not persisted in the plan store
- treat the local plan store as protection against stale, accidental, and normal replay—not as an authorization boundary against a malicious process running as the same OS user, which can tamper with files owned by that account
- because an approval is consumed before provider dispatch, reconcile provider state after a timeout or other ambiguous response before previewing a retry; the original request may have succeeded
- create pull requests and transition work items with separate approvals; apply one review-draft entry per approval, and inspect provider state before retrying a partially completed sequential cleanup operation

## Maintainer contact

- Maintainer: Rustem Kanzafarov
- GitHub: https://github.com/rustemkanz
- LinkedIn: https://www.linkedin.com/in/rustem-kanzafarov/
