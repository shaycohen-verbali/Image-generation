# AAC Image Generator and Optimizer (Web v1)

This repository implements a local web system that turns a concept key `(word, part_of_sentence, category)` into AAC-friendly kid-focused imagery with full traceability.

## Stack
- Backend: FastAPI + SQLAlchemy + SQLite
- Worker: separate Python process polling queued runs (Render Background Worker in production)
- Frontend: React (Vite)
- Storage: local filesystem for development; Supabase Storage for production assets/exports

## Features Implemented
- Entry creation (`POST /api/v1/entries`) with unique key enforcement.
- CSV import (`POST /api/v1/entries/import-csv`) with current column compatibility.
- Approved Supabase word sources (`GET /api/v1/word-sources`), currently supporting read/import/writeback for `word_inventory`.
- Run queueing (`POST /api/v1/runs`) and retry (`POST /api/v1/runs/{id}/retry`).
- Run listing and detailed lineage (`GET /api/v1/runs`, `GET /api/v1/runs/{id}`).
- 4-stage worker pipeline:
  - Stage 1: OpenAI Assistant first prompt
  - Stage 2: FLUX Schnell draft image
  - Stage 3: Vision critique + upgraded prompt + FLUX 1.1 Pro (Imagen fallback)
  - Stage 4: Nano-banana white background
  - Quality gate: GPT-4o-mini rubric score and auto-looping
- Asset lookup (`GET /api/v1/assets/{id}`)
- Exports (`POST /api/v1/exports`, `GET /api/v1/exports/{id}`): CSV + ZIP + manifest JSON
- Runtime config endpoints (`GET/PUT /api/v1/config`)
- Structured JSON logging
- Unit/integration test suite scaffold for core behavior

## Security Note
Your original Colab snippets included plaintext OpenAI and Replicate keys. Rotate both keys before using this code in production.

## Environment
Copy `.env.example` to `.env` and set:
- `OPENAI_API_KEY`
- `REPLICATE_API_TOKEN`
- optional `REPLICATE_BASE_URL` (defaults to `https://api.replicate.com` for direct requests)
- `INVENTORY_DATABASE_URL` to enable the Supabase `word_inventory` picker and writeback
- optional assistant overrides and thresholds
- `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_ALLOWED_USER_IDS` to enable the Slack bot — see [docs/slack_setup.md](docs/slack_setup.md)
- `PROCESS_ROLE=web|worker|all` selects the process role (`all` is local-development compatibility only).
- `WORKER_HARD_MAX_PARALLEL=2` caps per-instance worker concurrency even when the database runtime setting is higher.
- `WORKER_CLAIMING_ENABLED=false` starts a heartbeat-only worker for safe Render cutovers.
- `STORAGE_CACHE_MAX_BYTES` and `STORAGE_CACHE_MAX_AGE_SECONDS` bound remote download materialization.

## Backend Run (local development)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.db.init_db
PROCESS_ROLE=web python run_api.py
```

## Worker Run (local development)
In a second terminal:
```bash
cd backend
source .venv/bin/activate
PROCESS_ROLE=worker python -m app.worker
```

Production uses two independent Render services: `uvicorn app.main:app` for the
web/API service and `python -m app.worker` for the background worker. The web
service owns `/livez`, `/healthz`, the UI API, and Slack commands; it never
claims provider work. Keep `WORKER_CLAIMING_ENABLED=false` while validating a
new worker, then enable it only after the web service is healthy.

## Frontend Run
Node is required for the UI.
```bash
cd /Users/anna.cohen/Documents/Image generation/frontend
npm install
npm run dev
```

Set `VITE_API_BASE` if backend is not at `http://localhost:8000/api/v1`.

## Tests
```bash
cd /Users/anna.cohen/Documents/Image generation/backend
source .venv/bin/activate
pytest app/tests -q
```

## Runtime Data Layout
- Runs: `runtime_data/runs/{run_id}/` (local development metadata/temp files)
- Exports: `runtime_data/exports/{export_id}/` (local staging only; production artifacts use remote storage)

Generated files per run:
- `stage2_draft_*.jpg`
- `stage3_upgraded_attempt_{n}.jpg`
- `stage4_white_bg_attempt_{n}.jpg`
- `metadata_attempt_{n}.json`
