from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthcare_agent.clickhouse_store import ClickHouseChargeStore
from healthcare_agent.mrf import HospitalMrfParser


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a CMS hospital MRF and load matched rows into ClickHouse.")
    parser.add_argument("source", help="Local path or direct URL to a CMS-template CSV/JSON MRF.")
    parser.add_argument("--cpt", required=True, help="CPT/HCPCS code to extract, e.g. 73721.")
    parser.add_argument("--payer", help="Optional payer filter, e.g. Aetna.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum parsed rows to insert.")
    args = parser.parse_args()

    mrf_parser = HospitalMrfParser()
    matches = mrf_parser.parse_source(args.source, cpt=args.cpt, payer=args.payer, limit=args.limit)
    found = [match for match in matches if match.status == "found"]
    if not found:
        for match in matches:
            print(f"{match.status}: {match.message or 'no rows inserted'}")
        return 1

    store = ClickHouseChargeStore()
    if not store.enabled:
        print("Set CLICKHOUSE_URL, CLICKHOUSE_USER, and CLICKHOUSE_PASSWORD before ingesting.")
        return 1
    store.ensure_schema()
    inserted = store.insert_matches(found)
    print(f"Inserted {inserted} MRF charge row(s) into ClickHouse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
