.PHONY: backend-install api worker test maintenance frontend-install frontend-dev

backend-install:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

api:
	cd backend && . .venv/bin/activate && python run_api.py

worker:
	cd backend && . .venv/bin/activate && python -m app.worker

test:
	cd backend && . .venv/bin/activate && pytest app/tests -q

maintenance:
	cd backend && . .venv/bin/activate && python nightly_maintenance.py

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev
