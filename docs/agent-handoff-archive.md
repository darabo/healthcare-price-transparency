# Healthcare Price Transparency MVP - Agent Handoff Archive

Generated: 2026-05-23

## User Goal

Build a working MVP patient billing advocate agent for healthcare price transparency. The user wants the MVP to:

- Accept a user question about a bill, estimate, CPT code, hospital, payer, and location.
- Explain likely CPT/HCPCS codes in plain English.
- Assess whether a quoted/billed amount looks high, typical, or low.
- Use AI for the patient-facing explanation.
- Use real public evidence where possible: Nimble, CMS public hospital data, CMS hospital price transparency references, hospital MRF parsing, optional ClickHouse storage.
- Generate negotiation/dispute scripts and next-step checklists.

## Current Workspace

Repository:

```text
/Users/darabonakdar/Documents/healthcare-price-transparency
```

Run locally:

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:8000
```

Test:

```bash
python3 -m unittest discover -s tests
```

Latest verification:

```text
14 tests passing
node --check static/app.js passing
local server running at http://127.0.0.1:8000
```

## Secret Handling

Secrets were provided in chat and placed into local `.env`. Do not commit or print them.

Configured locally:

- `NIMBLE_API_KEY`
- `GOOGLE_AI_API_KEY`
- `GEMINI_MODEL=gemini-2.5-flash`
- `CLICKHOUSE_CLOUD_API_KEY_ID`
- `CLICKHOUSE_CLOUD_API_KEY_SECRET`

Not yet configured:

- `CLICKHOUSE_URL`
- `CLICKHOUSE_USER`
- `CLICKHOUSE_PASSWORD`
- `HOSPITAL_MRF_SOURCES`

Important: the ClickHouse Cloud API key ID/secret are management API credentials. They are not enough for SQL queries. The app still needs the ClickHouse database HTTP endpoint and database user/password.

## Implemented Architecture

```text
Static chat UI
  -> POST /api/chat
  -> PatientAdvocateAgent
  -> deterministic tools:
       classify_case_type
       extract_case_facts
       procedure_lookup
       rate_distribution_lookup
       find_care_options
       source_verify
       cms_open_hospital_lookup
       medical_costs_api_lookup
       public_evidence_lookup
       hospital_mrf_parse
       generate_advocacy_artifact
  -> AI layer:
       ai_patient_explanation via Gemini
  -> structured JSON response:
       answer, facts, cards, artifact, tool_trace, guardrails
```

Core files:

- `app.py`: stdlib HTTP server serving `/` and `/api/chat`.
- `healthcare_agent/agent.py`: orchestrates all tools and AI response generation.
- `healthcare_agent/ai.py`: Gemini API client and patient-facing explanation prompt.
- `healthcare_agent/tools.py`: deterministic classification, CPT mapping, fact extraction, benchmark lookup, care options, artifact generation.
- `healthcare_agent/external.py`: Nimble, Medical Costs API, CMS open hospital data, public evidence extraction.
- `healthcare_agent/mrf.py`: CMS-template hospital machine-readable file parser.
- `healthcare_agent/clickhouse_store.py`: optional ClickHouse HTTP client/store for normalized MRF rows.
- `scripts/ingest_mrf_to_clickhouse.py`: parse MRF rows for a CPT/payer and load them into ClickHouse.
- `static/index.html`, `static/styles.css`, `static/app.js`: chat UI and cards.
- `tests/`: deterministic tests and fixtures.

## Current AI Behavior

Gemini is used for the final patient-facing explanation only. It does not pick CPT codes or invent prices. The app computes structured facts first, then sends a compact evidence payload to Gemini.

Model:

```text
gemini-2.5-flash
```

Reason: the originally provided alias `gemini-flash-latest` returned high-demand or incomplete responses in live tests. `gemini-2.5-flash` returned complete structured JSON.

Live smoke test prompt:

```text
I was quoted $2200 for a knee MRI in Hoboken with Aetna. Explain the CPT and whether this is fair.
```

Live result:

- CPT candidate: `73721`
- Procedure: MRI lower extremity joint without contrast
- Fairness: high
- Sample benchmark: p25 `$520`, median `$760`, p75 `$1,180`
- Gemini explanation succeeded through `ai_patient_explanation`

The AI prompt in `healthcare_agent/ai.py` instructs Gemini to:

- Use only provided JSON facts.
- Avoid inventing CPT codes, providers, prices, payer names, legal claims, or medical advice.
- Explain likely CPT code.
- Say whether the amount looks high/fair/low based on benchmark.
- Give concrete next steps.
- Mention published rates are not a guarantee of final out-of-pocket cost.
- Return JSON with `{"answer":"..."}`.

## Data and Evidence Sources

### Sample Data

The MVP includes mock/sample benchmark data and care options so it works immediately.

Current CPT mapping includes knee MRI:

```text
CPT 73721 - MRI lower extremity joint without contrast
```

Sample local benchmark for Hoboken/Aetna:

```text
p25 $520
median $760
p75 $1,180
```

### Nimble

Nimble is configured via `.env` and used for rendered public-source extraction/discovery.

Current usage:

- `POST /v1/extract`
- `POST /v1/search`
- base URL: `https://sdk.nimbleway.com`
- search depth changed to `lite` because `fast` was rejected by the account.

Used against:

- `https://hospitalpricingfiles.org/`
- `https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency`

Key boundary: Nimble should discover or render pages that link to MRFs. It should not be used as the parser for giant MRF files. Direct file parsing is done locally.

### CMS Open Hospital Data

CMS Provider Data Catalog Hospital General Information dataset is queried without a key.

Dataset:

```text
xubh-q36u
```

Endpoint pattern:

```text
https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0
```

Purpose:

- Facility matching
- Basic hospital information
- Not pricing

### Medical Costs API

Used for national Medicare procedure benchmarks. Replaces the old CMS Procedure Price Lookup.

- Base URL: `https://medical-costs-api.david-568.workers.dev`
- No API key required.

### CMS Hospital Price Transparency GitHub

Reference:

```text
https://github.com/CMSgov/hospital-price-transparency
```

Used as schema/technical guide reference for hospital MRF parsing. It is not a turnkey parser.

### Hospital MRF Parser

Implemented in `healthcare_agent/mrf.py`.

Supports:

- CMS-style CSV tall rows with `payer_name`, `plan_name`, `standard_charge | negotiated_dollar`.
- CMS-style CSV wide columns such as `standard_charge | Aetna | Open Access | negotiated_dollar`.
- CMS-style JSON rows with nested billing-code and standard-charge objects.
- `.gz` and `.zip` containers when they contain CSV or JSON.
- CPT/HCPCS matching.
- Payer filtering.
- Gross, cash, negotiated, median, 10th, and 90th percentile amounts where present.

Configuration:

```text
HOSPITAL_MRF_SOURCES=/path/to/hospital-mrf.csv,https://example.org/hospital-mrf.json
HOSPITAL_MRF_MAX_BYTES=52428800
```

Current live state: no real `HOSPITAL_MRF_SOURCES` configured, so the app correctly reports `not_configured`.

### ClickHouse

Implemented as optional warehouse for normalized MRF rows.

Files:

- `healthcare_agent/clickhouse_store.py`
- `scripts/ingest_mrf_to_clickhouse.py`

Configuration needed:

```text
CLICKHOUSE_URL=https://your-clickhouse-host:8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your_database_password
CLICKHOUSE_DATABASE=healthcare
CLICKHOUSE_MRF_TABLE=mrf_charges
```

Ingest command:

```bash
python3 scripts/ingest_mrf_to_clickhouse.py /path/to/hospital-mrf.csv --cpt 73721 --payer Aetna
```

Agent behavior:

- If ClickHouse is configured, `hospital_mrf_parse` checks ClickHouse first.
- If no ClickHouse matches, it falls back to configured direct MRF sources.
- If neither is configured, it returns `not_configured`.

## UI Status

The static UI supports:

- Chat input
- Preset prompts
- Upload/paste text into prompt
- Comparison cards
- Artifact cards
- Tool trace tab

Cards include:

- Fairness
- AI Explanation
- Benchmark
- Medical Costs
- CMS Hospital Data
- Hospital MRF
- Care Options
- Public Evidence

## Tests

Test files:

- `tests/test_agent.py`
- `tests/test_external.py`
- `tests/test_mrf.py`
- `tests/test_clickhouse_store.py`
- `tests/fakes.py`
- fixtures under `tests/fixtures/`

Tests use fake AI services to avoid live Gemini calls.

Latest result:

```text
..............
----------------------------------------------------------------------
Ran 14 tests in 0.007s

OK
```

## Known Gaps / Next Best Steps

1. Add real hospital MRF discovery.

   Use Nimble to search/render hospitalpricingfiles.org or hospital pages, extract direct MRF URLs, then place those URLs into `HOSPITAL_MRF_SOURCES` or ingest them into ClickHouse.

2. Configure ClickHouse SQL credentials.

   The Cloud API credentials are already stored locally, but SQL querying needs `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, and `CLICKHOUSE_PASSWORD`.

3. Ingest real MRF rows.

   Once a direct MRF URL is available:

   ```bash
   python3 scripts/ingest_mrf_to_clickhouse.py "DIRECT_MRF_URL" --cpt 73721 --payer Aetna
   ```

4. Improve CPT mapping.

   Current MVP has a small deterministic procedure map. Add a CPT dictionary or licensed terminology source if production use requires broader CPT coverage. Avoid embedding restricted AMA CPT descriptions without proper licensing.

5. Add conversation memory.

   The backend stores case history in process memory only. Production needs durable case storage and privacy controls.

6. Improve PHI/security.

   This MVP is local-only and not production-ready for real PHI. Production needs authentication, audit logs, encryption, retention policy, and careful HIPAA/security review.

7. Add streaming/latency handling.

   Gemini calls are synchronous. Add loading states and timeout handling if this becomes a hosted service.

8. Add source citations to final answer.

   The structured cards already hold source evidence; the generated explanation could be improved to reference specific cards or sources by name.

## Important Decisions Made

- AI explains, tools decide. Gemini is not trusted as the pricing source of truth.
- Nimble discovers/renders webpages, local parser handles MRF files.
- Medical Costs API provides free, open-access national Medicare benchmarks.
- ClickHouse is optional but fits the long-term normalized MRF query layer.
- Secrets are stored only in local `.env`; never commit them.
- The app remains no-dependency Python stdlib for now.

## Useful Smoke Test

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"I was quoted $2200 for a knee MRI in Hoboken with Aetna. Explain the CPT and whether this is fair."}'
```

Expected:

- `case_type`: `estimate_review`
- `facts.cpt_candidates`: `["73721"]`
- `cards.fairness.status`: `high`
- `cards.ai_explanation.status`: `success` if Gemini key is configured
- `tool_trace` contains `ai_patient_explanation`

## Handoff Prompt For Another Coding Agent

Use this prompt:

```text
You are taking over a local MVP in /Users/darabonakdar/Documents/healthcare-price-transparency.

The project is a Python stdlib healthcare price transparency patient advocate agent. It has a static chat UI, a deterministic agent tool pipeline, Gemini AI explanations, Nimble public-source extraction, CMS open hospital lookup, a CMS-template MRF parser, and optional ClickHouse storage.

Read docs/agent-handoff-archive.md first, then inspect:
- healthcare_agent/agent.py
- healthcare_agent/ai.py
- healthcare_agent/mrf.py
- healthcare_agent/external.py
- healthcare_agent/clickhouse_store.py
- static/app.js
- README.md

Do not print or commit .env secrets. Keep AI as the response/explanation layer only; deterministic tools should continue to own CPT mapping, pricing benchmarks, source evidence, and guardrails.

Next priority: use Nimble to discover direct hospital MRF URLs, configure HOSPITAL_MRF_SOURCES or ingest them into ClickHouse, then make the agent return real hospital MRF charge matches for CPT/payer queries.

Before finalizing changes, run:
python3 -m unittest discover -s tests
node --check static/app.js
```
