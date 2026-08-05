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
- leave TLS verification enabled in normal use
- treat work-item attachments and screenshots as potentially sensitive
- review generated external comments before posting when possible

## Maintainer contact

- Maintainer: Rustem Kanzafarov
- GitHub: https://github.com/rustemkanz
- LinkedIn: https://www.linkedin.com/in/rustem-kanzafarov/