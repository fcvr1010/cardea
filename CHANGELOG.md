# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-03-20

### Added

- Config-driven generic proxy engine for adding new API services via TOML
  configuration ([#7](https://github.com/fcvr1010/cardea/pull/7))
- Browser credential manager module for secure browser automation
  ([#7](https://github.com/fcvr1010/cardea/pull/7))
- Email proxy module with IMAP (read/list) and SMTP (send) support
  ([#5](https://github.com/fcvr1010/cardea/pull/5))
- Per-endpoint disable support via `enabled = false` in config
  ([#2](https://github.com/fcvr1010/cardea/pull/2))
- Docker-based build with secrets injection
  ([#1](https://github.com/fcvr1010/cardea/pull/1))
- GitHub API proxy with automatic token injection
- Gmail proxy (legacy, pre-generic-proxy)
- Telegram proxy
- CODEOWNERS file ([#4](https://github.com/fcvr1010/cardea/pull/4))
- `.dockerignore` to reduce build context size
  ([#17](https://github.com/fcvr1010/cardea/pull/17))
- `CONTRIBUTING.md` with development workflow and module contract
  ([#18](https://github.com/fcvr1010/cardea/pull/18))

### Changed

- Generalized Vito-specific references for open-source audience
  ([#16](https://github.com/fcvr1010/cardea/pull/16))
- Extracted shared proxy utilities into `_proxy_utils` module to eliminate
  duplication ([#14](https://github.com/fcvr1010/cardea/pull/14))

### Fixed

- Email list endpoint now handles arbitrary IMAP FETCH item order
  ([#6](https://github.com/fcvr1010/cardea/pull/6))
- Fixed httpx client leak in proxy modules
  ([#14](https://github.com/fcvr1010/cardea/pull/14))
- Various code review fixes: test determinism, email config caching,
  documentation comments ([#15](https://github.com/fcvr1010/cardea/pull/15))

[Unreleased]: https://github.com/fcvr1010/cardea/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/fcvr1010/cardea/releases/tag/v0.1.0
