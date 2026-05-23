import unittest

from healthcare_agent.clickhouse_store import ClickHouseChargeStore
from healthcare_agent.mrf import MrfChargeMatch, MrfSourceService


class FakeClickHouseClient:
    enabled = True

    def __init__(self):
        self.queries = []

    def query(self, sql):
        self.queries.append(sql)
        return ""

    def query_json(self, sql):
        self.queries.append(sql)
        return {
            "data": [
                {
                    "source": "ClickHouse fixture",
                    "code": "73721",
                    "code_type": "CPT",
                    "description": "MRI lower extremity joint without contrast",
                    "setting": "outpatient",
                    "hospital_name": "Example Hospital",
                    "payer_name": "Aetna",
                    "plan_name": "Open Access",
                    "negotiated_dollar": 780,
                    "gross_charge": 2500,
                    "discounted_cash": 900,
                    "median_allowed": 760,
                    "p10_allowed": 620,
                    "p90_allowed": 1180,
                    "min_negotiated": None,
                    "max_negotiated": None,
                    "methodology": "fee schedule",
                    "file_format": "csv",
                    "schema_reference": "https://github.com/CMSgov/hospital-price-transparency",
                }
            ]
        }


class ClickHouseStoreTests(unittest.TestCase):
    def test_store_maps_clickhouse_rows_to_mrf_matches(self):
        client = FakeClickHouseClient()
        store = ClickHouseChargeStore(client=client, table="mrf_charges")
        matches = store.find_charges("73721", payer="Aetna")
        self.assertEqual(matches[0].status, "found")
        self.assertEqual(matches[0].source, "ClickHouse fixture")
        self.assertEqual(matches[0].negotiated_dollar, 780.0)
        self.assertIn("FROM `mrf_charges`", client.queries[0])

    def test_store_inserts_found_matches_as_json_each_row(self):
        client = FakeClickHouseClient()
        store = ClickHouseChargeStore(client=client, table="mrf_charges")
        count = store.insert_matches(
            [
                MrfChargeMatch(
                    source="fixture.csv",
                    status="found",
                    code="73721",
                    payer_name="Aetna",
                    negotiated_dollar=780,
                )
            ]
        )
        self.assertEqual(count, 1)
        self.assertIn("FORMAT JSONEachRow", client.queries[0])
        self.assertIn('"code":"73721"', client.queries[0])

    def test_mrf_source_service_prefers_clickhouse_when_configured(self):
        store = ClickHouseChargeStore(client=FakeClickHouseClient(), table="mrf_charges")
        service = MrfSourceService(sources=[], clickhouse=store)
        matches = service.find_charges("73721", payer="Aetna")
        self.assertEqual(matches[0].status, "found")
        self.assertEqual(matches[0].hospital_name, "Example Hospital")


if __name__ == "__main__":
    unittest.main()
