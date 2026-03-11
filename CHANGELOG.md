# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Suggest Reply API** (`POST /v1/reply/generate`): Platform-agnostic endpoint for generating suggested replies. Stateless, no conversation required. Use for ticket systems (WHMCS, Zendesk), livechat, or any helpdesk.
- **Production config**: `CORS_ORIGINS` – restrict CORS to specific origins (comma-separated). Use `*` for allow all (dev).
- **Production config**: `DOCS_ENABLED` – set to `false` to hide `/docs`, `/redoc`, and `/openapi.json` in production.

### Changed

- **Chunk citation sanitization**: LLM-generated chunk references (e.g. `(Chunk uuid, url)`, `(Chunks ...)`) are now stripped from answer text. Citations remain in the `citations` array only.
- **README**: Translated to English. Added Suggest Reply API docs and examples.

### Fixed

- Raw citation patterns `(Chunk uuid, url)` and `(Chunks uuid1, url1; uuid2, url2)` no longer appear in user-facing answer text.

---

## [1.0.0] - Initial release

- RAG chatbot with hybrid retrieval (BM25 + vector)
- Conversations API (CRUD, chat sync/stream)
- Tickets API (list, detail, approval workflow)
- Documents API (CRUD, fetch URL, crawl, re-crawl)
- WHMCS crawler
- Admin config (prompts, intents, doc-types, LLM, branding)
- Auth (JWT, API tokens sk_*, X-API-Key)
- Frontend (React + Vite)
