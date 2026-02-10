# AAC Image Generator and Optimizer (Web v1)

This repository implements a local web system that turns a concept key `(word, part_of_sentence, category)` into AAC-friendly kid-focused imagery with full traceability.

## Stack
- Backend: FastAPI + SQLAlchemy + SQLite
- Worker: separate Python process polling queued runs
- Frontend: React (Vite)
- Storage: local filesystem under `/Users/anna.cohen/Documents/Image generation/runtime_data`

## Features Implemented
- Entry creation (`POST /api/v1/entries`) with unique key enforcement.
- CSV import (`POST /api/v1/entries/import-csv`) with current column compatibility.
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
- `REPLICATE_CF_BASE_URL`
- optional assistant overrides and thresholds

## Backend Run
```bash
cd /Users/anna.cohen/Documents/Image generation/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.db.init_db
python run_api.py
```

## Worker Run
In a second terminal:
```bash
cd /Users/anna.cohen/Documents/Image generation/backend
source .venv/bin/activate
python -m app.worker
```

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
- Runs: `/Users/anna.cohen/Documents/Image generation/runtime_data/runs/{run_id}/`
- Exports: `/Users/anna.cohen/Documents/Image generation/runtime_data/exports/{export_id}/`

Generated files per run:
- `stage2_draft_*.jpg`
- `stage3_upgraded_attempt_{n}.jpg`
- `stage4_white_bg_attempt_{n}.jpg`
- `metadata_attempt_{n}.json`
