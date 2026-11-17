# Changelog

## 0.4.1

- Added explicit per-instance OAuth grant lifetime configuration, including no fixed expiry.
- Extended valid existing refreshable connections when no fixed expiry is explicitly enabled.
- Preserved access-token deadlines, consent scope, refresh rotation and revocation.
- Updated consent/admin lifetime displays and expiry/revocation regression tests.

## 0.4.0

- Added OAuth discovery, DCR, authorization code with mandatory S256 PKCE and resource binding.
- Added persistent issuer-bound SQLite authorization state, rotating refresh tokens and admin revocation.
- Corrected parallel sign-in/reload cookie interference.
- Corrected browser Origin handling and consent CSP callback navigation.
- Replaced simulated callback success with real cross-origin HTTPS browser regression coverage.

## 0.3.1

- Added preview date filters and configurable 60-second SQL execution deadline, capped at 120 seconds.
- Improved private timeout diagnostics.

## 0.3.0

- Added guided dataset preparation/publication, participant grants and access tests.

## 0.2.0

- Added saved SQL setup, object dropdowns and persistent field remarks.

## 0.1.x

- Established approved datasets, parameterized SELECT compilation, private admin and atomic catalogs.
- Added SQL TLS controls, dedicated password storage and redacted diagnostics.
- Fixed ODBC cursor/result lifecycle and disabled reporting connection pooling.
- Added Linux/HTTPS templates and API/browser/disposable-SQL CI.

## Repository documentation and tooling update 17 November 2026

- Consolidated installation, configuration, architecture, OAuth, security and troubleshooting guides.
- Added a generic reviewed-JSON bulk field-remarks import helper with dry run and private backups.
- Added synthetic helper tests and clarified implementation limits.
- Kept customer-specific channel inventory and acceptance evidence private.
- This repository update does not change the deployed application version by itself.
