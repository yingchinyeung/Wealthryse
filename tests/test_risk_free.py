from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from ibkr_etl.risk_free import (
    fetch_treasury_3m_rates,
    parse_treasury_3m_xml,
    treasury_year_url,
)


TREASURY_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns="http://www.w3.org/2005/Atom">
  <entry><content><m:properties>
    <d:NEW_DATE m:type="Edm.DateTime">2025-01-17T00:00:00</d:NEW_DATE>
    <d:BC_3MONTH m:type="Edm.Double">4.31</d:BC_3MONTH>
  </m:properties></content></entry>
  <entry><content><m:properties>
    <d:NEW_DATE m:type="Edm.DateTime">2025-01-21T00:00:00</d:NEW_DATE>
    <d:BC_3MONTH m:type="Edm.Double">4.29</d:BC_3MONTH>
  </m:properties></content></entry>
</feed>"""


class RiskFreeTests(unittest.TestCase):
    def test_parse_treasury_three_month_series(self) -> None:
        rows = parse_treasury_3m_xml(TREASURY_XML)
        self.assertEqual([row.rate_date for row in rows], ["2025-01-17", "2025-01-21"])
        self.assertAlmostEqual(rows[-1].annual_rate, 0.0429)

    def test_fetch_filters_to_requested_period(self) -> None:
        with patch("ibkr_etl.risk_free._get", return_value=TREASURY_XML) as get:
            rows = fetch_treasury_3m_rates(
                date(2025, 1, 20), end_date=date(2025, 1, 31)
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rate_date, "2025-01-21")
        self.assertEqual(get.call_args.args[0], treasury_year_url(2025))

    def test_implausible_rate_is_rejected(self) -> None:
        invalid = TREASURY_XML.replace(b"4.31", b"99.00").replace(b"4.29", b"99.00")
        with self.assertRaises(ValueError):
            parse_treasury_3m_xml(invalid)


if __name__ == "__main__":
    unittest.main()
