# Pakistan Source Strategy

## Principle
Official issuer/corporate websites are the PRIMARY source for Pakistani
annual reports. The Pakistan Stock Exchange (PSX) itself is used for
identity reference and verification only, NOT for high-volume PDF downloads.

## Source Priority Hierarchy

| Tier | Source | Usage |
|------|--------|-------|
| 0 | Cached source profile | Fastest — reuse previously verified URLs |
| 1 | Official company IR/annual-report page | Primary acquisition source |
| 2 | Other official company domain pages | Fallback on official domain |
| 3 | Official corporate CDN/subdomain | Alternative official hosting |
| 4 | Authorized regulator/exchange source | PSX only with explicit permission |
| 5 | Search-engine discovery (official domain) | Discovery tool only |
| 6 | Archived official source | Legitimate web archives |
| 7 | Reliable secondary archive | Gap recovery with stronger validation |

## Discovery Process

1. **Source Profile Check**: If the company's annual-report page URL is cached
   from a prior run, go directly there.

2. **IR Page Scan**: Try common investor-relations paths on the official domain:
   - `/investor-relations`
   - `/investors`
   - `/annual-reports`
   - `/financial-reports`
   - `/reports`
   - `/downloads`

3. **Link Classification**: Every PDF link found is classified as:
   - `ANNUAL` — Strong positive evidence (title says "Annual Report", etc.)
   - `NOT_ANNUAL` — Strong negative evidence (quarterly, half-year, AGM, etc.)
   - `REVIEW` — Ambiguous, needs human review

4. **Fiscal Year Extraction**: From link text, URL, or page context.
   Fiscal year ≠ publication year.

5. **Scoring**: Each candidate receives a deterministic quality score based on:
   - Source tier (official domain highest)
   - Classification confidence
   - Year match quality
   - Link text quality
   - Domain match with official website

## PSX Access Policy

**Default**: PSX is NOT used for automated bulk PDF downloads.

Pakistani stock exchange websites may restrict systematic automated retrieval.
The system respects:
- `robots.txt` directives
- Rate limiting (429 responses)
- Access controls (403 responses)
- WAF protections

If authorized PSX bulk access is later provided, enable it via:
```
--psx-bulk-authorized
```
or environment variable `PSX_BULK_AUTHORIZED=true`.

## Key Discovery Patterns on Pakistani Corporate Sites

Common URL patterns for annual reports:
```
/annual-reports/annual-report-2024.pdf
/investors/annual-report-fy2024.pdf
/reports/AnnualReport2024.pdf
/downloads/Annual-Report-2024.pdf
/financial-reports/annual-accounts-2024.pdf
```

The system learns these patterns per company and caches them for efficiency.
