# Auto Reply Chatbot | Support AI Assistant

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**RAG (Retrieval-Augmented Generation) chatbot** – Enterprise internal Support AI Assistant. Answers support questions via REST API using **hybrid retrieval** (BM25 + vector search) over your knowledge base. Combines web-crawled data, manually curated sample conversations, and continuous learning from highly-rated conversations.

> 🔍 *Keywords: RAG chatbot, LLM support assistant, WHMCS ticket crawler, vector search, knowledge base AI, customer support automation*

## Table of Contents

- [Data Sources & Continuous Learning](#data-sources--continuous-learning)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Usage Guide](#usage-guide)
- [Authentication](#authentication)
- [API Endpoints](#api-endpoints)
- [Configuration](#configuration)
- [Project Structure](#project-structure)

## Data Sources & Continuous Learning

The knowledge base is built from **three sources** and **improves continuously** through a feedback loop:

### 1. Web-crawled data

- **WHMCS tickets**: Crawl support tickets from WHMCS (via Playwright, login with cookies or credentials)
- **Documents from URL**: Fetch webpage content (policies, FAQ, docs) via `/documents/fetch-from-url` API
- **Website crawl**: Crawl entire site via `/documents/crawl-website`
- **Source JSON**: Ingest from multiple formats – `pages` (url, title, text), `articles`, `plans`, `sales_kb`, etc.
- **WHMCS SQL dump**: Import tickets from `source/*.sql` via `make import-whmcs`

### 2. Manually curated sample conversations

- **sample_conversations.json**: Add high-quality sample conversations directly (real Q&A)
- **sample_docs.json**: Pre-prepared static documents (web pages, articles)
- **custom_docs.json**: Documents created from admin panel, synced back to file

### 3. Learning from highly-rated conversations

- Crawled tickets are **manually reviewed** (approve/reject). Only **approved** tickets are added to the knowledge base
- **Export approved tickets** → `sample_conversations.json` via `POST /v1/admin/ingest-tickets-to-file`
- Re-run ingest so new sample conversations are embedded and indexed into OpenSearch/Qdrant
- Loop: *Crawl → Review (approve) → Export → Ingest* lets the system **learn more** from real high-quality conversations

---

## Features

- **RAG**: BM25 (OpenSearch) + vector (Qdrant) + reranking
- **Conversations**: CRUD, chat sync/stream, linked to ticket/livechat
- **Tickets**: List from DB, approval workflow (pending/approved/rejected)
- **Documents**: CRUD, fetch from URL, crawl website, re-crawl, upload
- **WHMCS Crawler**: Crawl tickets via Playwright, save cookies, check session
- **Admin**: Ingest docs/tickets, config (prompts, intents, doc-types, LLM, archi), branding
- **Auth**: JWT login, API tokens (sk_*), user management
- **Frontend**: React + Vite – Login, Conversations, Sample conversations, Documents, Crawl, Dashboard, Intents, Doc Types, Settings, API Tokens, API Reference

## Tech Stack

- **API**: FastAPI + Pydantic v2 + Uvicorn
- **DB**: PostgreSQL 15+
- **Cache/Queue**: Redis + Celery
- **Search**: OpenSearch (BM25), Qdrant (vector)
- **Embeddings/LLM**: OpenAI (pluggable)
- **Crawler**: Playwright (Chromium)
- **Frontend**: React 19, Vite 7, Tailwind CSS

## Quick Start

### Prerequisites

- Docker & docker-compose
- OpenAI API key

### Environment Variables

```bash
cp .env.example .env
# Edit .env: OPENAI_API_KEY, JWT_SECRET (production), ADMIN_API_KEY, API_KEY
```

### Run with Docker Compose

```bash
docker-compose up -d
```

- **API**: http://localhost:8000
- **Frontend**: http://localhost:5174
- **MinIO**: http://localhost:9000 (console: 9001)

**With Nginx gateway** (API on port 80):

```bash
docker-compose --profile full up -d
```

### Migrations and Initial Setup

```bash
# Inside container
docker-compose exec api alembic upgrade head
docker-compose exec api python -m scripts.create_admin_user   # Create admin (after migration 011)
docker-compose exec api python scripts/ingest_from_source.py
docker-compose exec api python scripts/ingest_tickets_from_source.py

# Or local (with services running)
make init-db
make create-admin
make ingest
```

**Source files** in `source/`:

- `sample_docs.json` – documents (pages: url, title, text)
- `sample_conversations.json` – tickets/conversations (from WHMCS crawl or manual)
- `custom_docs.json` – documents created from admin panel
- `*.sql` – WHMCS SQL dumps for `make import-whmcs`

See `app/services/source_loaders.py` for supported formats.

### Local Development

1. Start PostgreSQL, Redis, OpenSearch, Qdrant (or use docker-compose for infra only)
2. `pip install -r requirements.txt`
3. `uvicorn app.main:app --reload`
4. Worker: `celery -A worker.celery_app worker --loglevel=info`
5. `alembic upgrade head`
6. `make create-admin` (create first admin user)

## Usage Guide

### First-time setup (complete flow)

1. **Start services**: `docker-compose up -d`
2. **Run migrations**: `docker-compose exec api alembic upgrade head`
3. **Create admin**: `docker-compose exec api python -m scripts.create_admin_user` (enter username/password when prompted)
4. **Login to frontend**: Open http://localhost:5174 → Login with the account you just created
5. **Add knowledge base data** (choose one or more methods below)

### Method 1: Ingest from JSON files in `source/`

Prepare `source/sample_docs.json` or `source/sample_conversations.json`:

```json
// sample_docs.json - documents (web pages, policy, FAQ)
{
  "pages": [
    {"url": "https://example.com/refund-policy", "title": "Refund Policy", "text": "Full content..."}
  ]
}

// sample_conversations.json - Q&A from tickets (requires external_id, subject, description)
{
  "source": "whmcs",
  "conversations": [
    {
      "external_id": "12345",
      "subject": "Refund question",
      "description": "User: How do I request a refund?\nStaff: You can request a refund within 30 days...",
      "status": "Closed",
      "priority": "Medium"
    }
  ]
}
```

Run ingest:

```bash
make ingest                                    # Ingest documents
python scripts/ingest_tickets_from_source.py   # Ingest sample conversations
```

### Method 2: Fetch from URL or crawl website

- **Single URL**: Use API `POST /v1/documents/fetch-from-url` with `{"url": "https://..."}` or via frontend **Documents** → Add → Fetch from URL
- **Entire website**: Use API `POST /v1/documents/crawl-website` with `{"base_url": "https://example.com", "max_pages": 50}` or via frontend **Documents** → Crawl website

### Method 3: Crawl WHMCS tickets (via frontend)

1. Go to **Crawl** (sidebar)
2. Enter **Base URL** (e.g. `https://billing.example.com`)
3. **Login to WHMCS**:
   - **Option A (Cookies)**: Login to WHMCS in browser → DevTools → Application → Cookies → Copy JSON → Paste into "Session cookies" field → Save cookies
   - **Option B (Credentials)**: Enter username, password (and TOTP if applicable) → Click "Login & Crawl"
4. **Check connection** → If OK, click **Crawl tickets**
5. Go to **Sample conversations** (Tickets) → Review each ticket → **Approve** high-quality tickets
6. **Export approved** → `POST /v1/admin/ingest-tickets-to-file` (or corresponding button) to write to `sample_conversations.json`
7. Run `python scripts/ingest_tickets_from_source.py` to embed and index

### Method 4: Import from WHMCS SQL dump

If you have a WHMCS dump file (e.g. `source/greenvps_whmcs.sql`):

```bash
make import-whmcs-dry   # Validate parse first
make import-whmcs       # Run actual import
```
This project improves chatbot performance through structured evaluation, prompt optimization, and continuous feedback loops.

Powered by [OptyxStack AI Optimization](https://optyxstack.com/ai-optimization)
 to enhance accuracy, relevance, and reliability at scale.
 
Then approve tickets in **Sample conversations** and ingest as in steps 6–7 of Method 3.

### Chat workflow (API)

1. **Create conversation**:
   ```bash
   curl -X POST http://localhost:8000/v1/conversations \
     -H "Authorization: Bearer YOUR_JWT" \
     -H "Content-Type: application/json" \
     -d '{"source_type": "ticket", "source_id": "TKT-123"}'
   ```
2. **Send message** (sync or stream):
   ```bash
   curl -X POST http://localhost:8000/v1/conversations/{CONV_ID}/messages \
     -H "Authorization: Bearer YOUR_JWT" \
     -H "Content-Type: application/json" \
     -d '{"content": "What is your refund policy?"}'
   ```
3. Response contains `answer` (RAG-generated) and `debug_metadata` (retrieval, evidence).

### Frontend – main pages

| Page | Purpose |
|------|---------|
| **Conversations** | List conversations, create new, try chat |
| **Sample conversations** | View crawled/imported tickets, approve/reject, export approved |
| **Documents** | CRUD documents, fetch URL, crawl website, re-crawl |
| **Crawl** | Configure WHMCS, save cookies, crawl tickets |
| **Dashboard** | Token stats, retrieval, escalation |
| **Intents** | CRUD intents (query classification) |
| **Doc Types** | CRUD doc types (policy, faq, pricing, …) |
| **Settings** | System prompt, LLM config, branding, domain terms |
| **API Tokens** | Create/revoke API token (sk_*) |
| **API Reference** | API documentation |

### External system integration

- **Suggested reply (platform-agnostic)**: Call `POST /v1/reply/generate` with `query` = ticket/chat content. No conversation creation required. Use for WHMCS, Zendesk, livechat, or any helpdesk.
- **Livechat / Ticket system (chat flow)**: Call `POST /v1/conversations` with `source_type: "livechat"` or `"ticket"`, `source_id` = ID from your system. When user sends a message, call `POST /v1/conversations/{id}/messages` and use `answer` to display to the user.
- **Webhook**: You can wrap the API in your webhook endpoint to receive requests from livechat/ticket platforms.

### Troubleshooting

| Issue | Suggestion |
|-------|------------|
| Frontend login returns 401 | Check `JWT_SECRET` in `.env`, ensure you ran `make create-admin` |
| WHMCS crawl fails | Cookies expired → log in to WHMCS again, copy new cookies |
| Ingest has no data | Check files in `source/` have correct format (pages, conversations), run `make ingest-dry` to see logs |
| API returns 401 | Use Bearer JWT (from `/auth/login`) or valid `X-API-Key` |
| OpenSearch/Qdrant error | Ensure all services are healthy: `docker-compose ps` |

## Authentication

API accepts **three auth methods**:

1. **Bearer JWT** – from `POST /v1/auth/login` (username/password)
2. **X-API-Key** – env `API_KEY` or DB API token (sk_*)
3. **X-Admin-API-Key** – for admin endpoints (env `ADMIN_API_KEY` or JWT with role=admin)

**Create admin user** (after migration 011):

```bash
make create-admin
# Or: python -m scripts.create_admin_user
```

**API tokens** (sk_*): Create via `POST /v1/auth/tokens` (requires Bearer JWT). Tokens are stored in DB and can be revoked.

## API Endpoints

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/auth/login` | Login (username, password) → JWT |
| GET | `/v1/auth/me` | Current user (Bearer JWT) |
| GET | `/v1/auth/tokens` | List API tokens |
| POST | `/v1/auth/tokens` | Create API token |
| DELETE | `/v1/auth/tokens/{token_id}` | Revoke token |

### Conversations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/conversations` | List (pagination, filter: source_type, source_id) |
| POST | `/v1/conversations` | Create (source_type: ticket/livechat, source_id) |
| GET | `/v1/conversations/{id}` | Detail + messages |
| PATCH | `/v1/conversations/{id}` | Update metadata |
| DELETE | `/v1/conversations/{id}` | Delete |
| POST | `/v1/conversations/{id}/messages` | Send message (sync) |
| POST | `/v1/conversations/{id}/messages:stream` | Send message (SSE) |

### Suggest Reply (platform-agnostic)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/reply/generate` | Generate suggested reply (ticket, livechat, helpdesk). Stateless, no conversation required. |

### Tickets

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/tickets` | List (pagination, filter: status, approval_status, q) |
| GET | `/v1/tickets/{id}` | Ticket detail |

### Documents

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/documents` | List (pagination, filter: doc_type, q) |
| GET | `/v1/documents/{id}` | Detail |
| POST | `/v1/documents` | Create document (ingest) |
| POST | `/v1/documents/fetch-from-url` | Fetch content from URL |
| POST | `/v1/documents/crawl-website` | Crawl website |
| POST | `/v1/documents/re-crawl-all` | Re-crawl all documents |
| POST | `/v1/documents/upload` | Upload document |
| POST | `/v1/documents/{id}/re-crawl` | Re-crawl single document |
| PATCH | `/v1/documents/{id}` | Update metadata |
| DELETE | `/v1/documents/{id}` | Delete |

### Admin (Bearer JWT admin / X-Admin-API-Key)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/admin/ingest` | Ingest documents (queue Celery) |
| POST | `/v1/admin/ingest-from-source` | Ingest from source/ (sync) |
| POST | `/v1/admin/save-whmcs-cookies` | Save WHMCS cookies |
| POST | `/v1/admin/check-whmcs-cookies` | Check cookies |
| GET | `/v1/admin/whmcs-cookies` | Get saved cookies |
| GET | `/v1/admin/config/whmcs` | WHMCS defaults |
| POST | `/v1/admin/crawl-tickets` | Crawl WHMCS tickets |
| PATCH | `/v1/admin/tickets/{id}/approval` | Update approval (pending/approved/rejected) |
| POST | `/v1/admin/ingest-tickets-to-file` | Export approved tickets → sample_conversations.json |
| GET/PUT | `/v1/admin/config/llm` | LLM config |
| GET/PUT | `/v1/admin/config/archi` | Architecture config (normalizer, evidence, etc.) |
| GET/PUT | `/v1/admin/config/system-prompt` | System prompt |
| GET/PUT | `/v1/admin/config/{key}` | App config (generic) |
| POST | `/v1/admin/config/refresh-cache` | Refresh config cache |
| POST | `/v1/admin/config/auto-generate-from-domain` | Auto-generate branding from domain |
| GET/POST/PUT/DELETE | `/v1/admin/intents` | CRUD intents |
| GET/POST/PUT/DELETE | `/v1/admin/doc-types` | CRUD doc types |

### Health & Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/health` | Health check |
| GET | `/v1/metrics` | Prometheus metrics |
| GET | `/v1/dashboard/stats` | Token cost, retrieval hit-rate, escalation rate |

## Example cURL Requests

### Login

```bash
curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password"}'
```

### Create conversation (with Bearer JWT or X-API-Key)

```bash
curl -X POST http://localhost:8000/v1/conversations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT" \
  -d '{"source_type": "ticket", "source_id": "TKT-12345", "metadata": {}}'
```

### Send message

```bash
curl -X POST http://localhost:8000/v1/conversations/{CONV_ID}/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "X-External-User-Id: user-123" \
  -d '{"content": "What is your refund policy?"}'
```

### Generate suggested reply (platform-agnostic)

```bash
curl -X POST http://localhost:8000/v1/reply/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT" \
  -d '{
    "query": "What is your refund policy? I want to cancel my order.",
    "source_type": "ticket",
    "source_id": "TKT-12345"
  }'
```

Response: `{ "answer": "...", "decision": "PASS"|"ASK_USER"|"ESCALATE", "followup_questions": [], "citations": [...], "confidence": 0.9 }`

### Ingest documents

```bash
curl -X POST http://localhost:8000/v1/admin/ingest \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: admin-key" \
  -d '{
    "documents": [
      {
        "url": "https://example.com/refund-policy",
        "title": "Refund Policy",
        "raw_text": "Full refund within 30 days...",
        "doc_type": "policy"
      }
    ]
  }'
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL (async) |
| `DATABASE_URL_SYNC` | `postgresql://...` | PostgreSQL (sync, Celery) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis |
| `OPENSEARCH_HOST` | `http://localhost:9200` | OpenSearch |
| `QDRANT_HOST` | `localhost` | Qdrant |
| `OPENAI_API_KEY` | - | Required for embeddings/LLM |
| `API_KEY` | - | API auth (empty = dev mode) |
| `ADMIN_API_KEY` | - | Admin auth |
| `JWT_SECRET` | `change-me-in-production` | JWT signing secret (required in production) |
| `JWT_EXPIRE_MINUTES` | `10080` (7 days) | JWT expiry |
| `OBJECT_STORAGE_URL` | - | MinIO/S3 (e.g. http://minio:9000) |
| `LLM_MODEL` | `gpt-5.2` | LLM model |
| `LLM_MAX_TOKENS` | `2048` | Max tokens |
| `APP_NAME` | - | Company/app name for branding (greeting, title) |
| `NORMALIZER_DOMAIN_TERMS` | - | Comma-separated entity terms (e.g. vps,windows,linux,pricing) |
| `NORMALIZER_SLOTS_ENABLED` | `false` | Enable slot extraction (product_type, os, billing_cycle, region) |
| `NORMALIZER_SLOT_PRODUCT_TYPES` | - | Product types for slots (e.g. vps,dedicated,vds). Empty = disabled |
| `NORMALIZER_SLOT_OS_TYPES` | - | OS types for os slot (e.g. windows,linux,macos). Empty = disabled |
| `CORS_ORIGINS` | `*` | CORS allowed origins. Comma-separated (e.g. `https://app.example.com`). `*` = allow all (dev) |
| `DOCS_ENABLED` | `true` | Enable `/docs` and `/redoc`. Set `false` in production to hide API docs |

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/init_db.py` | Create DB and run migrations |
| `scripts/create_admin_user.py` | Create initial admin user (run after migration 011) |
| `scripts/ingest_from_source.py` | Ingest documents from source/ |
| `scripts/ingest_tickets_from_source.py` | Ingest tickets from sample_conversations.json |
| `scripts/import_whmcs_sql_dump_to_tickets.py` | Import tickets from source/*.sql |
| `scripts/crawl_whmcs_tickets.py` | Crawl WHMCS tickets (CLI) |
| `scripts/whmcs_login_browser.py` | Open browser to login WHMCS, get cookies |

### Makefile commands

```bash
make init-db       # Run migrations
make create-admin  # Create admin user
make ingest        # Ingest docs from source/
make ingest-dry    # Dry run: load docs without ingesting
make import-whmcs  # Import WHMCS tickets from source/*.sql
make import-whmcs-dry  # Dry run: validate SQL parsing
```

## Frontend

```bash
cd frontend && npm install && npm run dev
# http://localhost:5173
```

Or use Docker: `docker-compose up -d frontend` → http://localhost:5174

**Main pages**: Login, Conversations, Sample conversations (tickets), Documents, Crawl (WHMCS), Dashboard, Intents, Doc Types, Settings, API Tokens, API Reference.

## Project Structure

```
app/
  main.py              # FastAPI app
  api/routes/          # auth, conversations, reply, tickets, documents, admin, health, dashboard
  services/            # retrieval, LLM, ingestion, ticket_db, ticket_loaders, source_loaders
  search/              # OpenSearch, Qdrant, reranker, embeddings
  crawlers/            # WHMCS crawler (Playwright)
  db/                  # Models, session
  core/                # Config, auth, logging, rate limit, tracing, gateway
worker/
  celery_app.py
  tasks.py             # Ingestion tasks
frontend/              # React + Vite (CRUD, chat, crawl UI)
alembic/              # Migrations
scripts/               # init_db, create_admin_user, ingest_from_source, ingest_tickets_from_source, import_whmcs_sql_dump_to_tickets, crawl_whmcs_tickets, whmcs_login_browser
source/                # sample_docs.json, sample_conversations.json, custom_docs.json, *.sql
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
