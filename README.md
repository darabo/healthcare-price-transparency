# Healthcare Price Transparency Agent MVP

A no-dependency local MVP for a chatbot-style patient advocate agent. It covers the first three workflows from the implementation plan:

- Review whether an estimate or bill looks high.
- Find lower-cost care options for a procedure.
- Generate negotiation or dispute artifacts.

The app uses a small standard-library Python backend, a static chat UI, deterministic agent orchestration, and mock normalized price data. The tool contracts are shaped so a real price API such as Serif Health, HealthCorum, CMS benchmarks, or a ClickHouse-backed cache can replace the sample data later.

## Configure Real Evidence Sources

Create a local `.env` file. Do not commit it.

```bash
cp .env.example .env
```

Set:

```text
NIMBLE_API_KEY=your_key_here
GOOGLE_AI_API_KEY=your_google_ai_studio_key_here
GEMINI_MODEL=gemini-2.5-flash
```

Nimble is used for rendered public-source extraction and discovery against:

- `hospitalpricingfiles.org`
- CMS Hospital Price Transparency pages

CMS open hospital data is used without a key via the Provider Data Catalog `Hospital General Information` dataset (`xubh-q36u`) to identify likely hospital/facility matches.

Google AI Studio/Gemini is used for the patient-facing explanation. The app still computes CPT candidates, benchmarks, CMS evidence, MRF matches, and guardrails with deterministic tools first; Gemini receives those structured facts and turns them into a concise billing explanation.

CMS Procedure Price Lookup is also wired, but CMS API access requires CMS credentials and AMA terms acceptance. After you have the endpoint and key, set:

```text
CMS_PPL_API_KEY=your_cms_key_here
CMS_PPL_BASE_URL=your_cms_ppl_cost_search_endpoint
```

### Hospital MRF Parsing

The app can now parse CMS-template hospital machine-readable files directly from local paths or direct file URLs. The parser is based on the CMS Hospital Price Transparency GitHub technical guide, which documents CSV tall, CSV wide, and JSON templates:

- `https://github.com/CMSgov/hospital-price-transparency`

Configure one or more sources:

```text
HOSPITAL_MRF_SOURCES=/path/to/hospital-mrf.csv,https://example.org/hospital-mrf.json
HOSPITAL_MRF_MAX_BYTES=52428800
```

Nimble should be used to discover or render pages that link to MRFs. The app then downloads and parses direct CSV/JSON files locally, because real MRFs can be large and should not be treated as ordinary web-page scraping output.

Current parser coverage:

- CMS-style CSV tall rows with `payer_name`, `plan_name`, and `standard_charge | negotiated_dollar`.
- CMS-style CSV wide columns such as `standard_charge | Aetna | Open Access | negotiated_dollar`.
- CMS-style JSON rows with nested billing-code and standard-charge objects.
- `.gz` and `.zip` containers when they contain a CSV or JSON file.
- CPT/HCPCS matching, payer filtering, and extraction of gross, cash, negotiated, median, 10th, and 90th percentile amounts where present.

### ClickHouse

ClickHouse is useful as the normalized analytics store for parsed MRF rows. It should not replace Nimble; use Nimble to discover hospital MRF links, parse those direct files locally, then load normalized charge rows into ClickHouse for fast CPT/payer/facility queries.

Configure the database HTTP endpoint:

```text
CLICKHOUSE_URL=https://your-clickhouse-host:8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your_database_password
CLICKHOUSE_DATABASE=healthcare
CLICKHOUSE_MRF_TABLE=mrf_charges
```

If ClickHouse is configured, the agent checks it first for `hospital_mrf_parse` matches. To load rows from a direct MRF file or URL:

```bash
python3 scripts/ingest_mrf_to_clickhouse.py /path/to/hospital-mrf.csv --cpt 73721 --payer Aetna
```

The attached ClickHouse Cloud API key ID/secret can be stored as `CLICKHOUSE_CLOUD_API_KEY_ID` and `CLICKHOUSE_CLOUD_API_KEY_SECRET`, but those management API credentials are not sufficient by themselves for SQL queries. The app still needs `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, and `CLICKHOUSE_PASSWORD`.

## Run

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:8000
```

## Test

```bash
python3 -m unittest discover -s tests
```

## MVP Architecture

```text
Static chat UI
  -> /api/chat
  -> Agent orchestrator
  -> Tools:
       classify_case_type
       procedure_lookup
       rate_distribution_lookup
       find_care_options
       source_verify
       cms_open_hospital_lookup
       cms_ppl_lookup
       public_evidence_lookup
       hospital_mrf_parse
       ai_patient_explanation
       generate_advocacy_artifact
  -> structured response + next-step artifact
```

## API

`POST /api/chat`

```json
{
  "message": "I was quoted $2200 for a knee MRI in Hoboken with Aetna. Is that fair?",
  "case_id": "optional-stable-id"
}
```

Returns a structured agent response with:

- `answer`: patient-facing explanation.
- `case_type`: classified workflow.
- `facts`: extracted procedure, amount, payer, location, and CPT candidates.
- `tool_trace`: tools called and compact outputs.
- `cards`: comparison and care-option cards.
- `artifact`: negotiation or dispute script when relevant.
- `guardrails`: safety and price-certainty caveats.

## Notes

This MVP does not process real PHI securely and should not be deployed with real patient data as-is. It deliberately keeps document upload to client-side text extraction for `.txt` files and pasted bill text. Production deployment needs authentication, audit logging, secure storage, data licensing, and a real normalized price API.
