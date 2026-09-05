# BIS Assistant — BIS Compliance Copilot

> Turn a single product name into a complete, source-cited BIS (Bureau of Indian Standards) compliance workspace.

A Flask web application that takes one input — a product name — and builds a personalised compliance workspace covering the applicable Indian Standard, certification scheme, testing requirements, licensing steps, required documents and BIS-recognised labs.

Every claim is grounded in a curated knowledge base built from real `bis.gov.in` pages. An optional AI layer (Google Gemini) rephrases and extends those grounded answers — it never invents facts. When the system can't ground an answer in evidence, it refuses and logs the gap instead of guessing.

---

## Table of Contents

- [Highlights](#highlights)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [How the AI Is Actually Used](#how-the-ai-is-actually-used)
- [User Workflow](#user-workflow)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Knowledge Base](#knowledge-base)
- [Build & Dev Tooling](#build--dev-tooling)
- [Design Decisions](#design-decisions)

---

## Highlights

| Feature | What it does |
|---|---|
| **Conversational onboarding** | Three questions (user type → product → location) create a product workspace |
| **7-area compliance dashboard** | Standard, Certification, Scheme, Testing, Related Standards, Documents/Labs, Licensing — each cited to a real BIS URL |
| **Grounded-first answers** | Deterministic BIS content always available; AI is an optional polish layer, never the source of truth |
| **Measured refusal** | Questions that can't be grounded above a relevance threshold are refused and logged, not answered |
| **Documentation Gap Report** | Admin view of every unanswerable question — turns refusals into a content roadmap |
| **Multilingual** | English / Hindi / Telugu across UI chrome *and* dynamically generated answer bodies |
| **PDF export** | Multilingual compliance report with bundled Noto fonts (IS numbers stay in Latin script) |
| **Lab navigation** | BIS-recognised labs sorted by proximity, Google Maps deep links, browser geolocation directions |
| **Lab enquiry flow** | Drafts an enquiry email with an explicit approval step before anything is sent |
| **Works with zero API keys** | Full offline BIS mode — Gemini and SMTP are both strictly optional |

---

## Tech Stack

| Layer | Choice |
|---|---|
| **Backend framework** | Flask 3.1 (Python), server-rendered with Jinja2 — no SPA, no separate frontend framework |
| **Language** | Python 3.12+ |
| **Database** | SQLite (`bis_compliance.db`) via raw `sqlite3` — no ORM |
| **Auth** | Flask session-based, `werkzeug.security` password hashing, email + OTP registration, Google-OAuth-style dev shortcut |
| **AI / LLM** | Google Gemini only (`google-genai` SDK) — `gemini-flash-lite-latest` for chat, `gemini-embedding-001` for embeddings. Free-tier key, fully optional |
| **Retrieval** | Hybrid BM25 (`rank-bm25`) + cosine-similarity embedding search over ingested BIS PDF chunks (`numpy`) |
| **Translation** | `deep-translator` (keyless) for Hindi/Telugu, Gemini as fallback translator |
| **PDF generation** | `reportlab` with bundled Noto Sans / Noto Sans Devanagari / Noto Sans Telugu TTFs |
| **Email (OTP)** | `smtplib` over Gmail SMTP (App Password), console-logging fallback in dev |
| **KB scraping/ingestion** | `requests` + `beautifulsoup4` (HTML) + `pdfplumber` (PDF text) — build-time only |
| **Frontend** | Plain HTML/Jinja2 + vanilla JavaScript + hand-written CSS (CSS custom-property theme system) |
| **Font** | Google Fonts "Poppins" (app-wide) |
| **Maps** | Google Maps deep links (no API key) + browser Geolocation API |
| **Dev tooling** | `python-dotenv`, custom Flask-test-client route smoke test (no pytest) |

`requirements.txt` pins every dependency — nothing is installed ad hoc.

---

## Architecture

### Request flow

```
Browser
  │
  ▼
app.py            thin entry point — just starts the server
  │
  ▼
server.py         creates the Flask `app`, loads .env, boots the DB,
                  checks Gemini connectivity at startup, registers the
                  nav/language/active-case context processor and the
                  "must finish onboarding" gate
  │
  ▼
routes/           imported once by server.py; each file attaches routes
                  to the same shared `app`
  │
  ▼
services/         all business logic — routes only call into these
templates/        server-rendered Jinja2 HTML
static/           CSS / JS / fonts (browser-side only)
```

`constants.py` and `helpers.py` sit alongside `server.py` — shared config and shared helper functions that more than one route file needs, kept separate to avoid circular imports.

### Route table

| File | Owns |
|---|---|
| `routes/auth.py` | Login, register, OTP verify, Google sign-in, logout |
| `routes/onboarding.py` | The 3-question conversational setup (type → product → location) |
| `routes/workspace.py` | `/`, `/home` (the 7-answer personalised dashboard), language switch |
| `routes/features.py` | Standards, Schemes, Licensing, Documents, Testing & Labs, Photo Check, Checklist |
| `routes/cases.py` | My Cases list, aggregated case view, switch workspace, PDF export |
| `routes/api.py` | Every `/api/*` JSON endpoint — chat, AI orchestrator, history, lab enquiry |
| `routes/admin.py` | Documentation Gap Report |
| `routes/legacy.py` | 301 redirects from earlier URL names, so old links still resolve |

---

## Project Structure

### Root

| File | Job |
|---|---|
| `app.py` | The entry point you run (`python app.py`). Imports `server` (creates the app), then `routes` (registers views), then starts the dev server. ~15 lines |
| `server.py` | App creation, `.env` loading, DB init + seeding, Gemini connectivity check + startup banner, nav/language/active-case template context, the onboarding gate, and the `md` Markdown template filter |
| `constants.py` | Static config — nav links, supported languages (EN/HI/TE), the onboarding question script, Indian states, the 7-row Checklist definition, the BIS portal list (Manak Online / CRS / LIMS / BIS Care) |
| `helpers.py` | Cross-route helpers — resuming the last workspace on login, creating a user post-OTP, loading the active case, saving chat history, sorting labs by proximity, Markdown → safe-HTML rendering |
| `database.py` | SQLite connection, full schema (`CREATE TABLE IF NOT EXISTS ...`), and `ensure_schema_compatibility()` — a migration helper that adds new columns to existing databases without wiping data |
| `seed_data.py` | Populates the primary demo user and a ~900-row `compulsory_products` reference table on first run (idempotent) |
| `translations.py` | Static UI-chrome text in EN/HI/TE — nav labels, buttons, PDF title. Dynamic answer bodies are translated live by `services/llm.py` |
| `requirements.txt` | Pinned dependencies, each with a comment explaining why it's there |
| `.env.example` | Template for the git-ignored `.env` — Flask config, `GEMINI_API_KEY`, SMTP creds, primary-user seed values |
| `.gitignore` | Excludes `.env`, the SQLite DB, `__pycache__`, `.venv/`, `.claude/`, and the KB scrape cache |

### `services/` — the actual logic

| File | Job |
|---|---|
| `llm.py` | The **only** file that talks to Gemini. Chat completion, embeddings, hi/te translation — all wrapped so a missing key, quota error or timeout never crashes a request; it returns a sentinel and the caller falls back to deterministic content. Includes a **circuit breaker**: after repeated failures it stops calling Gemini for 3 minutes |
| `answer_engine.py` | The core 70% BIS / 30% AI pipeline. Builds a grounded context block from the KB plus verbatim PDF excerpts, optionally asks Gemini to phrase/extend it under a strict "answer only from this context, cite everything" prompt, and returns the deterministic version if Gemini is unavailable or replies `NOT_COVERED`. Also runs the general-chatbot fallback and logs unanswerable questions for the Gap Report |
| `knowledge_base.py` | Loads and caches `knowledge_base/*.json`, matches free-text product names to a known slug, and flattens one of the 7 areas into a title + Markdown body + source list |
| `ai_orchestrator.py` | The "Ask Anything" intent router — classifies a free-text question (regex-based) into standard / scheme / licensing / testing / labs / documents / overview / product-info / unsupported, then either navigates to the right feature page (carrying the question) or answers in place |
| `rag_engine.py` | Hybrid BM25 + embedding search over `document_chunks`. Supplies verbatim excerpts and the measured-refusal threshold constant. Also retains `fanout_7_searches` / `generate_rag_response` from an earlier iteration for two legacy endpoints |
| `rule_engine.py` | Deterministic rule-based logic for two legacy endpoints — `analyze_scheme_applicability` and `inspect_isi_hallmark_photo` |
| `action_agent.py` | Permission-tiered action definitions (open BIS portal / generate checklist / dispatch lab enquiry) and execution of a user-approved action; backs `/api/actions/execute` |
| `mailer.py` | SMTP email sending (registration OTP) via Gmail, with a no-credentials dev mode that prints the email and OTP to the console |
| `pdf_generator.py` | Builds the multilingual compliance PDF with `reportlab`, registering the bundled Noto fonts so Hindi/Telugu render correctly |
| `bis_fetch.py` | Best-effort HTTP fetch with an on-disk cache for `bis.gov.in` / `lims.bis.gov.in` / `crsbis.in` — build-time tooling only, never called at request time |

### `templates/`

```
base.html                     shared layout — header, nav, footer
index.html                    marketing landing page
auth_layout.html              login.html · register.html · verify_otp.html
onboarding.html               3-question setup
home.html                     dashboard — workspace header, 7 answer cards,
                              AI chat panel, search history
standards.html                one page per feature area
schemes.html
licensing.html
documents.html
testing_labs.html
photo_check.html
checklist.html
my_cases.html                 workspace list
case_detail.html              aggregated case view
gap_report.html               admin
partials/
  ├── app_nav.html
  ├── answer_card.html
  └── area_section.html
```

### `static/`

```
css/
  ├── styles.css              shared design system — CSS variables, buttons,
  │                           cards, the pastel theme
  ├── index.css               landing-page only
  └── <one per page/area>
js/
  ├── home.js                 chat + history
  ├── feature.js              shared feature-page behaviour
  ├── ai_upgrade.js           swaps the deterministic answer for the
  │                           Gemini-enhanced one after page load
  ├── testing_labs.js         map links, geolocation, enquiry modal
  ├── photo_check.js
  └── index.js                landing page background animation
```

### `knowledge_base/` — the actual BIS content

`_index.json` lists the five currently supported products with their IS numbers, schemes and name aliases (used for matching free-text input):

- Electric Iron
- Two-Wheeler Helmet
- LED Lamp
- Steel Pipe
- Portland Cement

Each `<slug>.json` holds that product's full 7-area content, with **every fact tagged with a real `bis.gov.in` / `lims.bis.gov.in` / `manakonline.in` source URL**. `knowledge_base/_cache/` (git-ignored) holds the fetched HTML/PDF used to build these files.

---

## How the AI Is Actually Used

This is **not** "send every question to an LLM." The design is a grounded-first pipeline, in this order:

**1. Deterministic first (100% BIS)**
Every answer starts from the curated `knowledge_base/*.json` — real IS numbers, scheme names, licensing steps, each cited to a real source URL. This alone is a complete, always-available answer.

**2. Optional AI polish (the 70/30 blend)**
If `GEMINI_API_KEY` is set and Gemini is reachable, it receives that same grounded context plus a strict prompt — *answer only from this context, cite every claim, say `NOT_COVERED` if you can't* — and is asked to phrase and extend it. Roughly 70% verbatim facts, 30% connective explanation. If Gemini fails, times out or returns `NOT_COVERED`, the app silently falls back to the deterministic answer. **The user never sees an error.**

**3. General chatbot mode (Home only)**
For a question genuinely outside the knowledge base, Gemini may answer from general knowledge — but only if it judges the question BIS / standards / product / technical / compliance related. Anything off-topic gets a fixed scope-refusal message instead of a guess.

**4. Measured refusal**
If nothing — KB or retrieval — scores above the fixed relevance threshold, the app refuses outright and logs the query to `audit_logs` for the Documentation Gap Report. This is a deliberate *refuse rather than hallucinate* stance, not a bug.

**5. Progressive enhancement**
Feature pages render instantly with the deterministic answer, then `ai_upgrade.js` calls `/api/ai/area` in the background and quietly swaps in the AI-polished version — so a slow or rate-limited free tier never blocks page load.

**6. Circuit breaker**
Repeated Gemini failures open a 3-minute "don't bother calling it" window, so a Google-side outage degrades gracefully instead of adding latency to every single request.

---

## User Workflow

1. **Landing page** (`/`) → **Register** (name / email / password) → **email OTP verification** → account created.
2. **Onboarding** (`/onboarding`) — three questions, one at a time: what best describes you (manufacturer / importer / consumer / …) → which product (free text, matched against the KB) → city and state.
3. That creates a **Case** (a product workspace) and lands on **Home** (`/home`): workspace header, 7 answer cards (Standard, Certification, Scheme, Testing, Related Standards, Documents/Labs, Licensing), a sidebar of past questions, and an embedded AI chat that answers from the KB, answers from general knowledge, or refuses on scope.
4. The nav bar leads to **dedicated feature pages** — Standards, Schemes, Licensing, Documents, Testing & Labs, Photo Check, Checklist — each showing the same grounded content in more depth, with a *mark reviewed* action that feeds the Checklist's progress.
5. **Testing & Labs** additionally lists BIS-recognised labs sorted by proximity to the user's city/state, with a Google Maps link, a *directions from my location* button (browser geolocation), and a lab-enquiry email draft/send flow **with an approval step before anything is actually emailed**.
6. **My Cases** lets a user switch between multiple product workspaces (*Start Another Product*) and download a multilingual PDF report aggregating the whole case.
7. A **language switcher** (top-right pill) changes both the UI chrome and every dynamically generated answer body.
8. Anything the system can't ground in evidence is **refused and logged**, visible in the admin **Documentation Gap Report** (`/admin/gap-report`).

---

## Getting Started

### Prerequisites

- Python 3.12 or newer

### Install and run

```bash
python -m pip install -r requirements.txt
python app.py
```

The app opens at **http://127.0.0.1:5000**.

It works **fully with zero API keys** in offline BIS mode — the deterministic knowledge base is the source of truth, so nothing external is required to get a complete answer.

---

## Configuration

Copy `.env.example` to `.env` and fill in what you need. Everything below is optional.

| Variable | Effect if set | Behaviour if unset |
|---|---|---|
| `GEMINI_API_KEY` | Enables the AI-polish layer, general chatbot mode and Gemini fallback translation | App runs in deterministic-only mode; every feature still works |
| `SMTP_USER` / `SMTP_PASSWORD` | Actually sends registration OTP emails via Gmail (App Password) | OTP is printed to the console in dev mode |
| Flask config | Standard Flask settings | Sensible dev defaults |
| Primary-user seed values | Seeds the demo account | Defaults from `seed_data.py` |

`.env` is git-ignored — never commit it.

---

## Build & Dev Tooling

Scripts in `tools/` are **build-time and dev-time only** — the running app never imports them.

| Script | Purpose |
|---|---|
| `build_kb.py` | Generates the `knowledge_base/` JSON files from live `bis.gov.in` pages |
| `ingest_pdfs.py` | Extracts, chunks and embeds BIS Product Manual PDFs into `document_chunks` for verbatim-quote retrieval |
| `smoke_test.py` | Flask-test-client crawler that hits every route and asserts no 404 / 500 — the project's test suite |
| `refusal_eval.py` | Prints a table of supported vs. off-topic questions against the measured-refusal threshold, demonstrating that refusal works |

---

## Design Decisions

A few choices that are intentional rather than accidental:

- **No ORM.** Raw `sqlite3` with an explicit schema in `database.py` and a hand-written migration helper, so the data layer stays readable and there's no hidden query behaviour.
- **No SPA.** Server-rendered Jinja2 with vanilla JS. Pages are useful before any JavaScript runs; the AI layer is progressive enhancement on top.
- **AI is optional by construction.** `services/llm.py` is the single point of contact with Gemini, and every caller has a deterministic fallback path. Removing the API key degrades the product, it doesn't break it.
- **Refusal is a feature.** An unanswerable question produces a logged gap and an honest refusal. The Gap Report turns that into a prioritised list of knowledge base work.
- **Legacy code kept deliberately.** `routes/legacy.py`, parts of `rag_engine.py`, `rule_engine.py` and `action_agent.py` back earlier endpoints. They're superseded, not dead — old links and older API consumers still work.

---

## License

Add your chosen license here (MIT is a common default for hackathon and portfolio projects).

---

## Acknowledgements

All compliance content is sourced from public Bureau of Indian Standards resources — `bis.gov.in`, `lims.bis.gov.in`, `manakonline.in` and `crsbis.in`. This project is an independent tool and is not affiliated with or endorsed by the Bureau of Indian Standards.
