# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in ContentStudio, please email **security@thq.digital** with:

1. Description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (if applicable)

**Do NOT open a public GitHub issue for security vulnerabilities.**

## Security Features

### Prompt Injection Prevention
- All user inputs are sanitized via `neutralize_prompt_injection()` before reaching LLM
- Brand fields undergo sanitization in `build_brand_block()`
- Safe string output is enforced

### Authentication & Authorization
- Open-source version uses demo user mode (`demo-user`, `demo-tenant`)
- No production secrets required
- All endpoints work without authentication

**⚠️ This build must not be exposed to an untrusted network (the public internet, an
internal network you don't fully trust) without replacing `app/core/dependencies.py`
with real authentication first.** Every endpoint — including connecting/publishing
social accounts, generating content, and reading all stored brand/campaign data — is
open to anyone who can reach the API. As a safety net, the app refuses to start with
`ENV=production` unless you explicitly set `ALLOW_DEMO_AUTH_IN_PRODUCTION=true`, so
this can never happen silently — but that flag does not make the API safe, it only
acknowledges you've made a deliberate choice. See [DEPLOYMENT.md](DEPLOYMENT.md) for
what to add before a real deployment.

### Data Handling
- No customer data retained beyond session
- No persistent user tracking
- No telemetry

### Dependencies
- All dependencies are pinned in `requirements.txt`
- Regularly updated via Dependabot
- No known high-severity CVEs at release time

## Supported Versions

| Version | Status | Support Until |
|---------|--------|----------------|
| 1.0.x   | Current | TBD |

## Security Scanning

This repository is scanned with:
- Bandit (security linting)
- GitHub Secret Scanning
- Dependabot (dependency updates)

## Compliance

- Apache 2.0 licensed
- No HIPAA claims
- No SOC2 certification
- No PCI-DSS compliance
- No GDPR data processing (demo mode only)

## Disclaimer

This is open-source software provided AS-IS. Users are responsible for security assessment in their own deployments. For enterprise security requirements, contact THQ.Digital.
