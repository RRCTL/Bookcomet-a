# Security Policy

Bookcomet handles receipts, invoices, bank statements, and company accounting context. Please report security issues privately.

## Supported versions

This public MVP is the `main` branch of [RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a). Security fixes are applied to `main`. Older snapshots, forks, and local checkouts are not separately maintained.

## How to report a vulnerability

Do **not** open a public GitHub issue, pull request, or discussion for a vulnerability.

1. Use [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository when it is enabled.
2. If private reporting is not yet enabled, contact the repository maintainers through the [RRCTL organization](https://github.com/RRCTL) without attaching real financial documents, API keys, or a public exploit.

Include:

- Bookcomet-a commit SHA or release tag
- Operating system and whether you used local or cloud OCR/LLM
- A sanitized description of the issue and impact
- Steps to reproduce with **fictional** data only

## What not to include

Do not attach receipts, bank statements, customer lists, `.env` files, databases, session tokens, or a public proof-of-concept that would let others extract data from a running instance.

## Response targets

We aim to acknowledge a valid private report within **7 days** and to share an initial assessment or mitigation plan within **14 days**. Timelines may be longer for issues that require a coordinated provider or dependency fix.

## Scope notes

- Default VLM/LLM settings use an OpenAI-compatible gateway. Cloud OCR/AI sends uploaded document images, OCR content, and necessary company profile data to the provider you configure. A local endpoint keeps that data on the device.
- Bookcomet prepares reviewable draft journals. It does not replace qualified accounting, tax, legal, audit, or regulatory review.
- Issues that only affect an operator-misconfigured public tunnel, a weak local `JWT_SECRET_KEY`, or a third-party model provider are still useful to report if Bookcomet can warn or fail closed more clearly.
