from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from ibkr_etl.util import canonical_json


class CanonicalJsonTests(unittest.TestCase):
    def test_database_dates_datetimes_and_decimals_are_exactly_normalized(self) -> None:
        payload = {
            "date": date(2026, 9, 29),
            "datetime": datetime(2026, 9, 29, 0, 30, 15, tzinfo=timezone.utc),
            "decimal": Decimal("182535.5700"),
        }

        self.assertEqual(
            canonical_json(payload),
            '{"date":"2026-09-29","datetime":"2026-09-29T00:30:15+00:00",'
            '"decimal":"182535.5700"}',
        )

    def test_unknown_objects_still_fail_closed(self) -> None:
        with self.assertRaisesRegex(TypeError, "not JSON serializable"):
            canonical_json({"value": object()})


if __name__ == "__main__":
    unittest.main()
