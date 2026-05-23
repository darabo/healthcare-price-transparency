# Healthcare Price Transparency Agent

An AI-powered patient advocate chatbot that helps consumers understand, compare, and negotiate medical bills and procedure estimates. Users describe their situation in plain language — "I got a $2,200 bill for a knee MRI in New York with Aetna" — and the agent returns a structured, evidence-backed assessment with real pricing data, fairness analysis, and actionable negotiation tools.

## What It Does

- **Bill & Estimate Review** — Determines whether a quoted or billed amount is fair by comparing it against real negotiated rates from hospital machine-readable files, Medicare benchmarks, and public pricing databases.
- **Price Comparison** — Finds lower-cost care options for the same procedure, showing what other facilities and payers are charging.
- **Negotiation & Dispute Support** — Generates phone scripts, email templates, and step-by-step checklists to help patients challenge overcharges, backed by the benchmark data the agent already gathered.
- **General Healthcare Pricing Questions** — Answers open-ended questions about procedure costs, insurance terminology, and billing practices using web-sourced evidence.
- **LLM Observability** — Datadog Lapdog is used for local LLM observability — **no Datadog account required**. Every agent run, tool call, and Gemini LLM invocation is emitted as a structured span you can inspect in a browser dashboard.

## How It Works

The agent uses a deterministic tool pipeline — not just an LLM — to gather and verify pricing data before generating a response. AI (Google Gemini) is used only as the final explanation layer; all pricing benchmarks, CPT mapping, fairness scoring, and guardrails are handled by structured, auditable tools.

```
User message
  → Classify intent (bill review / find cheaper / negotiate / general)
  → Extract facts (procedure, CPT code, amount, payer, location)
  → Query pricing data sources:
      • Medical Costs API — national Medicare benchmarks (free, no key)
      • PRA API — real hospital negotiated rates for New York
      • MedicalCosts API — negotiated rate lookups by CPT, state, payer
      • CMS Open Data — hospital identification and quality ratings
      • Nimble — rendered public-source web extraction (optional)
      • ClickHouse — normalized MRF charge warehouse (optional)
  → Compute fairness score (low / typical / high vs. benchmarks)
  → Generate negotiation artifacts if the bill is actionable
  → AI explanation — Gemini synthesizes all structured data into a
    clear, patient-friendly response
  → Return structured JSON with cards, evidence, and guardrails
```

## Data Sources

| Source                                                                                       | Coverage                                                           | Key Required?  |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------- |
| [Medical Costs API](https://medical-costs-api.david-568.workers.dev)                         | National Medicare procedure benchmarks & negotiated rates by state | No             |
| [PRA / NYC Hospital Price Finder](https://nychospitalpricefinder.patientrightsadvocate.org/) | Real negotiated rates from NY hospital MRFs (all payers)           | No             |
| [CMS Provider Data Catalog](https://data.cms.gov)                                            | Hospital identification, quality ratings, general info             | No             |
| [Google Gemini](https://ai.google.dev)                                                       | AI-generated patient explanations                                  | Yes            |
| [Nimble](https://nimbledata.com)                                                             | Rendered web extraction for hospital pricing pages                 | Yes (optional) |
| ClickHouse                                                                                   | Self-hosted warehouse for parsed MRF charge rows                   | Self-hosted    |

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd healthcare-price-transparency
cp .env.example .env
```

Edit `.env` and set at minimum:

```text
GEMINI_API_KEY=your_gemini_key_here
```

### 2. Run

```bash
python3 app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

### 3. Try it

Type something like:

> I was quoted $2,200 for a knee MRI (CPT 73721) in New York with Aetna. Is that fair?

The agent will return a fairness assessment, benchmark comparisons, real negotiated rates from NY hospitals, and — if the price looks high — a generated negotiation script.

## Configuration

### Required

| Variable         | Purpose                                                     |
| ---------------- | ----------------------------------------------------------- |
| `GEMINI_API_KEY` | Google AI Studio API key for patient-facing AI explanations |

### Optional

| Variable                 | Purpose                                                            |
| ------------------------ | ------------------------------------------------------------------ |
| `NIMBLE_API_KEY`         | Nimble rendered web extraction for hospital pricing pages          |
| `HOSPITAL_MRF_SOURCES`   | Comma-separated local paths or URLs to hospital MRF CSV/JSON files |
| `HOSPITAL_MRF_MAX_BYTES` | Max bytes to download per MRF file (default: 50 MB)                |
| `CLICKHOUSE_URL`         | ClickHouse HTTP endpoint for normalized MRF charge queries         |
| `CLICKHOUSE_USER`        | ClickHouse username                                                |
| `CLICKHOUSE_PASSWORD`    | ClickHouse password                                                |
| `CLICKHOUSE_DATABASE`    | ClickHouse database name                                           |
| `CLICKHOUSE_MRF_TABLE`   | ClickHouse table name for MRF charge rows                          |

### Loading MRF data into ClickHouse

```bash
python3 scripts/ingest_mrf_to_clickhouse.py /path/to/hospital-mrf.csv --cpt 73721 --payer Aetna
```

### LLM Observability with Datadog Lapdog

The agent is instrumented with [Datadog Lapdog](https://docs.datadoghq.com/llm_observability/lapdog/) for local LLM observability — **no Datadog account required**. Every agent run, tool call, and Gemini LLM invocation is emitted as a structured span you can inspect in a browser dashboard.

**Install Lapdog:**

```bash
# macOS (Homebrew)
brew install datadog/lapdog/lapdog

# Or via pip (any OS)
pip install ddtrace
```

**Run with Lapdog:**

```bash
lapdog python app.py
```

Then open [lapdog.datadoghq.com](https://lapdog.datadoghq.com) to see a real-time trace of every agent session — including the full Gemini prompt/response, tool execution order, and latency breakdown.

If Lapdog / `ddtrace` is not installed, the instrumentation is a complete no-op and the app runs exactly as before.

## API

### `POST /api/chat`

```json
{
  "message": "I was quoted $2200 for a knee MRI in Hoboken with Aetna. Is that fair?",
  "case_id": "optional-stable-id"
}
```

**Response** includes:

| Field        | Description                                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `answer`     | AI-generated patient-facing explanation                                                                                        |
| `case_type`  | Classified workflow (`estimate_review`, `find_cheaper_care`, `negotiate_or_dispute`, `general_inquiry`)                        |
| `facts`      | Extracted procedure, amount, payer, location, and CPT candidates                                                               |
| `cards`      | Structured data cards: fairness score, rate distribution, care options, CMS benchmarks, hospital matches, MRF negotiated rates |
| `artifact`   | Negotiation phone script, email template, and checklist (when applicable)                                                      |
| `tool_trace` | Full audit trail of every tool invoked and its output                                                                          |
| `guardrails` | Safety and price-certainty caveats                                                                                             |

## Architecture

```
healthcare-price-transparency/
├── app.py                          # Zero-dependency HTTP server (stdlib only)
├── static/                         # Chat UI (HTML/CSS/JS)
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── healthcare_agent/
│   ├── agent.py                    # Orchestrator — sequences tools and composes response
│   ├── tools.py                    # Deterministic tools: CPT lookup, fairness, care options
│   ├── mrf.py                      # MRF data layer: Medical Costs API + PRA API
│   ├── external.py                 # CMS, Nimble, and web evidence services
│   ├── ai.py                       # Gemini integration for AI explanations
│   ├── models.py                   # Shared data models
│   ├── observability.py             # Lapdog / LLM Observability integration (no-op without ddtrace)
│   ├── sample_data.py              # Fallback sample benchmarks
│   ├── clickhouse_store.py         # ClickHouse charge warehouse client
│   ├── cache.py                    # Response caching
│   └── config.py                   # Environment variable loader
├── scripts/                        # CLI utilities (MRF ingestion, etc.)
├── tests/                          # Unit tests
└── docs/                           # Documentation and handoff notes
```

## Tests

```bash
python3 -m unittest discover -s tests
```

## Disclaimer

This application provides price-navigation support, **not medical or legal advice**. Published or sampled rates are not a guarantee of final out-of-pocket cost. Always verify CPT/HCPCS codes, network status, prior authorization, and all billing entities with your provider and insurer before acting.

This MVP does not process real PHI securely and should not be deployed with real patient data as-is. Production deployment requires authentication, audit logging, secure storage, data licensing, and compliance review.
