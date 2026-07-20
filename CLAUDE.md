# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup and Running
- **Start local stack:** `./run-dev.sh` (Starts 4 containers: Frontend, Backend, PostgreSQL, Redis with hot-reload)
- **Stop local stack:** `docker compose -f docker-compose.dev.yml down`

### Backend Commands (run inside container or via `docker compose exec backend ...`)
- **Linting:** `ruff check .`
- **Formatting:** `black .`
- **Run all tests:** `pytest`
- **Run single test:** `pytest tests/test_file.py::test_function_name`
- **Check coverage:** `pytest --cov=app --cov-report=term-missing`
- **Database migrations:** `alembic upgrade head` (to apply), `alembic revision --autogenerate -m "msg"` (to create)

### Frontend Commands (run inside container or via `docker compose exec frontend ...` or locally if Node is installed)
- **Run dev server locally (if Node.js available):** `npm run dev`
- **Build:** `npm run build`
- **Linting:** `npm run lint`
- **Testing:** `npm run test` or `npm run test:run` (vitest)

## Architecture Overview

FreeLingo is an open-source AI language learning platform. It evaluates CEFR levels, generates study plans, and guides users through lessons.

### Key Components

- **Frontend (Next.js):** 
  - Located in `./frontend/`.
  - Uses React 19, Next.js 16 App Router, TailwindCSS 4, Shadcn, Zustand for state management.
  - Implements the learning interface, voice conversation with STT/TTS (VAD integration), and flashcards.
  - Uses route groups `(app)` for authenticated sidebar layout and `(auth)` for public routes.

- **Backend (FastAPI/Python 3.14):** 
  - Located in `./backend/`.
  - Driven by SQLAlchemy 2.0 ORM with PostgreSQL and asyncpg.
  - Handles deterministic curriculum, progress tracking, user subscriptions (Stripe), spaced repetition (Flashcards).
  - Uses structured responses via Pydantic for LLM-generated exercise/lesson validation.
  
- **Service Layer (Backend):**
  - **LLM Adapter:** Provider-agnostic singleton wrapping Ollama, OpenAI, Anthropic, DeepSeek.
  - **Generators:** Deterministic Lesson Generator and Study Plan Generator.
  - **Voice Pipeline:** Real-time WebSocket voice orchestrator chaining STT -> LLM -> TTS.
  - **Prompts:** Centralized under `services/prompts/` handling language overlays and CJK support.

- **Data Models:**
  - 21 SQLAlchemy ORM models across 5 domains (Core, Study Plan, Spaced Repetition, Conversations, AI/Static content).
  - Postgres JSON columns store unstructured curriculum payloads (lessons, plans, skill scores).

- **Data/Content:**
  - Pre-defined curriculums, assessment banks, and vocabulary are statically organized in `backend/data/` by locale (e.g. `en_GB/`, `es/`, `ja/`).

### Common Configurations
- Environment variables control all configurations (`app/core/config.py`).
- Dev setup (`.env.dev`) defaults to `openai` for STT/TTS bypassing heavy local Kokoro/Whisper containers for simpler hot-reloading. 
- API dependencies are accessed through the service layer, the frontend never directly calls the models.