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
- [API Endpoints](#api-endpoints)
- [Configuration](#configuration)
- [Project Structure](#project-structure)

## Data Sources & Continuous Learning

The knowledge base is built from **three sources** and **improves continuously** through a feedback loop:

### 1. Web-crawled data

- **WHMCS tickets**: Crawl support tickets from WHMCS (via Playwright, login with cookies or credentials)
- **Documents from URL**: Fetch webpage content (policies, FAQ, docs) via `/documents/fetch-from-url` API
- **Source JSON**: Ingest from multiple formats – `pages` (url, title, text), `articles`, `plans`, `sales_kb`, etc.

### 2. Manually curated sample conversations

- **sample_conversations.json**: Add high-quality sample conversations directly (real Q&A)
- **sample_docs.json**: Pre-prepared static documents (web pages, articles)
- **custom_docs.json**: Documents created from admin panel, synced back to file

### 3. Learning from highly-rated conversations

- Crawled tickets are **manually reviewed** (approve/reject). Only **approved** tickets are added to the knowledge base
- **Export approved tickets** → `sample_conversations.json` via `POST /admin/ingest-tickets-to-file`
- Re-run ingest so new sample conversations are embedded and indexed into OpenSearch/Qdrant
- Loop: *Crawl → Review (approve) → Export → Ingest* lets the system **learn more** from real high-quality conversations

---

## Features

- **RAG**: BM25 (OpenSearch) + vector (Qdrant) + reranking
- **Conversations**: CRUD, chat sync/stream, linked to ticket/livechat
- **Tickets**: List from DB, approval workflow (pending/approved/rejected)
- **Documents**: CRUD, fetch from URL, ingest from source JSON
- **WHMCS Crawler**: Crawl tickets via Playwright, save cookies, check session
- **Admin**: Ingest docs/tickets, config (prompts, intents), branding
- **Frontend**: React + Vite – Conversations, Sample conversations, Documents, Crawl, Dashboard

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
# Edit .env: OPENAI_API_KEY, ADMIN_API_KEY, API_KEY
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

### Migrations and Ingest

```bash
# Inside container
docker-compose exec api alembic upgrade head
docker-compose exec api python scripts/ingest_from_source.py
docker-compose exec api python scripts/ingest_tickets_from_source.py

# Or local (with services running)
make init-db
make ingest
```

**Source files** in `source/`:

- `sample_docs.json` – documents (pages: url, title, text)
- `sample_conversations.json` – tickets/conversations (from WHMCS crawl or manual)
- `custom_docs.json` – documents created from admin panel

See `app/services/source_loaders.py` for supported formats.

### Local Development

1. Start PostgreSQL, Redis, OpenSearch, Qdrant (or use docker-compose for infra only)
2. `pip install -r requirements.txt`
3. `uvicorn app.main:app --reload`
4. Worker: `celery -A worker.celery_app worker --loglevel=info`
5. `alembic upgrade head`

## API Endpoints

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
| PATCH | `/v1/documents/{id}` | Update metadata |
| DELETE | `/v1/documents/{id}` | Delete |

### Admin (X-Admin-API-Key)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/admin/ingest` | Ingest documents (queue Celery) |
| POST | `/v1/admin/ingest-from-source` | Ingest from source/ (sync) |
| POST | `/v1/admin/save-whmcs-cookies` | Save WHMCS cookies |
| POST | `/v1/admin/check-whmcs-cookies` | Check cookies |
| POST | `/v1/admin/crawl-tickets` | Crawl WHMCS tickets |
| PATCH | `/v1/admin/tickets/{id}/approval` | Update approval (pending/approved/rejected) |
| POST | `/v1/admin/ingest-tickets-to-file` | Export approved tickets → sample_conversations.json |
| GET/PUT | `/v1/admin/config/{key}` | Get/update config (system_prompt, etc.) |
| GET/POST/PUT/DELETE | `/v1/admin/intents` | CRUD intents |

### Health & Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/health` | Health check |
| GET | `/v1/metrics` | Prometheus metrics |
| GET | `/v1/dashboard/stats` | Token cost, retrieval hit-rate, escalation rate |

## Example cURL Requests

### Create conversation

```bash
curl -X POST http://localhost:8000/v1/conversations \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key" \
  -d '{"source_type": "ticket", "source_id": "TKT-12345", "metadata": {}}'
```

### Send message

```bash
curl -X POST http://localhost:8000/v1/conversations/{CONV_ID}/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key" \
  -H "X-External-User-Id: user-123" \
  -d '{"content": "What is your refund policy?"}'
```

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
| `OBJECT_STORAGE_URL` | - | MinIO/S3 (e.g. http://minio:9000) |
| `LLM_MODEL` | `gpt-5.2` | LLM model |
| `LLM_MAX_TOKENS` | `2048` | Max tokens |

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/init_db.py` | Create DB and run migrations |
| `scripts/ingest_from_source.py` | Ingest documents from source/ |
| `scripts/ingest_tickets_from_source.py` | Ingest tickets from sample_conversations.json |
| `scripts/crawl_whmcs_tickets.py` | Crawl WHMCS tickets (CLI) |
| `scripts/whmcs_login_browser.py` | Open browser to login WHMCS, get cookies |

## Frontend

```bash
cd frontend && npm install && npm run dev
# http://localhost:5173
```

Or use Docker: `docker-compose up -d frontend` → http://localhost:5174

**Main pages**: Conversations, Sample conversations (tickets), Documents, Crawl (WHMCS), Dashboard.

## Project Structure

```
app/
  main.py              # FastAPI app
  api/routes/          # conversations, tickets, documents, admin, health, dashboard
  services/            # retrieval, LLM, ingestion, ticket_db, ticket_loaders, source_loaders
  search/              # OpenSearch, Qdrant, reranker, embeddings
  crawlers/            # WHMCS crawler (Playwright)
  db/                  # Models, session
  core/                # Config, auth, logging, rate limit, tracing
worker/
  celery_app.py
  tasks.py             # Ingestion tasks
frontend/              # React + Vite (CRUD, chat, crawl UI)
alembic/              # Migrations
scripts/               # init_db, ingest_from_source, ingest_tickets_from_source, crawl_whmcs_tickets
source/                # sample_docs.json, sample_conversations.json, custom_docs.json
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
