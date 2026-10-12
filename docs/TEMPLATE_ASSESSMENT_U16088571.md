# U16088571 Template Assessment

## Registered source files

| File | Format | Structure | SHA-256 |
| --- | --- | --- | --- |
| monthly factsheet template.pdf | PDF | 2 A4 pages, non-fillable | `460064F7B7CD0650189E71949F31859E07F1FA7256F266C9138B9B9214BA705D` |
| presentation deck template.pptx | PowerPoint | 14 slides, 16:9 | `CBD48E2B128AC96297AC457B9DCC2A4ABC64149D24D5BD8F587EDAC7AA5DB86F` |
| previously completed factsheet.pdf | PDF | 2 A4 pages, non-fillable | `8EF023EDB02BF5E9DA90F28669F8E8E9FE158376215F228372502708FB5BDEE6` |

The files remain in `private-inputs/U16088571` and are excluded from Git.

The monthly factsheet template was replaced on 23 September 2026 with the
user-supplied Canva export. The superseded template is retained under
`private-inputs/U16088571/archive/2026-09-23` for rollback and has SHA-256
`4275B211BD0131EAA89AECAD76F84463A50996943EEDD64ADFEC56D29CC25739`.

## Factsheet pattern

The monthly factsheet uses a two-page A4 layout.

Page 1 contains:

- company and strategy headings
- report date
- strategy summary and investment-strategy narrative
- top five holdings table
- sector and subsector allocation table
- rolling 12-month return
- month-end normalized NAV and return table from 20 January 2025
- performance and suitability disclosures

Page 2 contains:

- normalized NAV history chart
- report date and historical-performance period
- full legal disclosures, risk wording and copyright text

The supplied template PDF has no AcroForm fields. Automated generation therefore requires either the editable original source or a versioned reconstruction. The completed factsheet demonstrates the expected populated output and provides a reconciliation reference.

## Presentation pattern

The registered source remains a 14-slide, 16:9 branded template. The approved
monthly output now expands it to 17 slides by duplicating the blank branded
analytics layout. Its reusable output structure is:

1. cover
2. strategy section divider
3. deterministic executive summary
4. account overview
5. current allocation
6. exposure and concentration
7. historical performance
8. sector allocation
9. risk analytics
10. benchmark comparison
11. drawdown analytics
12. monthly returns
13. data coverage and provenance
14. conclusions and next-month watchlist
15. fees, rebalancing and update terms
16. disclaimer
17. contact details

The deck uses Sprout green and white branding, a Hong Kong cover image, persistent confidentiality footers and results/appendix navigation. Fonts referenced in the package include Arial, Aptos Display, Georgia, Lato, Proxima Nova, Raleway and Times New Roman. Font availability must be checked in the report-generation container to prevent substitution.

The blank analytics slides can support:

- portfolio versus the S&P 500 Price Index, with the NASDAQ Composite Price
  Index available as a secondary comparison
- monthly and cumulative return tables
- drawdown analysis
- annualized return, volatility and Sharpe ratio
- benchmark-relative beta, tracking error and information ratio
- contribution, concentration and sector analysis

## Approved identity and contact details

- legal issuer: Sprout Asset Management (Asia) Limited
- SFC central entity number: `BDW665`
- regulated activities in the approved presentation: Types 1, 4, 6 and 9
- contact email: `inquiry@www.sprout-asset.com`
- both factsheet PDFs already contain the approved issuer name
- the latest PowerPoint upload contains the approved issuer name and CE number `BDW665`
- the latest PowerPoint upload contains neither `BJU500` nor Wang On Asset Management Limited
- the latest PowerPoint upload retains the approved contact email
- the latest PowerPoint upload lists Types 1, 4, 6 and 9, matching both supplied factsheet PDFs

## Remaining approval blockers

1. The presentation uses the stable navigation label “Results” on every month. Page-number shapes are intentionally removed from the generated analytical and legal pages.
2. The factsheet is PDF-only and not fillable. The editable original would reduce reconstruction risk.
3. The template contains issuer-specific legal language. AI may draft commentary, but it must not rewrite or approve regulatory disclosures.
4. Yahoo Finance benchmark and sector-classification data are approved only for
   provisional internal development. The reporting workflow must keep
   `publication_allowed=false`, and outputs must not be released to clients until
   a licensed publication source replaces them. Visible internal-development
   labels were removed from the user-facing artifacts at the user's request.

## Approved financial mapping

- account: `U16088571`
- reporting currency: USD
- reporting start: 20 January 2025
- primary benchmark: S&P 500 Price Index
- official internal benchmark code: `GSPC` (provisional yfinance symbol `^GSPC`)
- index currency: USD
- secondary benchmark: NASDAQ Composite Price Index (`IXIC`; provisional
  yfinance symbol `^IXIC`)
- benchmark convention: price return for both configured indexes
- publication frequency: monthly factsheet and presentation deck after month-end data-quality checks
