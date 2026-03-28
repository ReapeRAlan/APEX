# ============================================================
# APEX — Developer Commands
# ============================================================
# Usage: make <target>
# On Windows without make: use the equivalent docker-compose commands directly
# ============================================================

.PHONY: dev test migrate shell lint typecheck build up down logs clean

# ── Development ──
dev:
	docker compose up --build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f backend gee-worker gpu-worker scheduler

# ── Database ──
migrate:
	docker compose exec backend python -m backend.db.migrate_to_postgres

shell:
	docker compose exec backend python -c "from backend.db.session import engine; \
		from sqlalchemy import text; \
		print(engine.execute(text('SELECT version()')).fetchone())"

# ── Testing ──
test:
	docker compose exec backend python -m pytest tests/ -v --tb=short

# ── Code Quality ──
lint:
	docker compose exec backend python -m ruff check backend/

typecheck:
	docker compose exec backend python -m mypy backend/ --ignore-missing-imports

# ── Build (production) ──
build:
	docker compose build --no-cache

# ── Cleanup ──
clean:
	docker compose down -v --remove-orphans
	docker system prune -f
