Build a production pipeline for **US + UK sustainability/ESG/CSR reports, 2017–2025**.

Primary sources:

```text
1. SustainabilityReports.com Bulk Portal
2. Official company sustainability/reporting archives + CDNs
```

The tested production pattern is:

```text
COMPANY UNIVERSE
      ↓
SustainabilityReports bulk metadata
      ↓
resolve company + year + report type
      ↓
BULK ZIP where available
      ↓
official company CDN fallback
      ↓
local SSD
      ↓
validate PDF
      ↓
SHA256
      ↓
database
```

## 1. SustainabilityReports.com

Use the **authorized Bulk Portal**, not normal-page scraping.

Target filters:

```text
Country:
United States
United Kingdom

Years:
2017–2025

Language:
English

Report types:
Sustainability
ESG
CSR
Integrated Report
Impact Report
```

The service supports selecting hundreds/thousands of reports and downloading them as bulk ZIPs with metadata.

Desired workflow:

```text
bulk query
↓
metadata.csv
↓
filter against our company universe
↓
report manifest
↓
download ZIP
↓
parallel extract locally
```

Expected manifest columns:

```text
company_name
ticker
ISIN
country
report_year
report_title
report_type
language
source_url
filename
page_count
```

Do not perform:

```text
500 PDFs = 500 browser requests
```

Prefer:

```text
500 PDFs
↓
1/few ZIPs
↓
local extraction
```

The bulk-download approach is the preferred high-throughput path.

---

## 2. Official company CDN fallback

For reports missing or ambiguous in the bulk source, resolve from the company's official sustainability archive.

Use this discovery order:

```text
company sustainability/reporting page
↓
archive page
↓
extract direct PDF links
↓
store all URLs first
↓
download afterward
```

Do not crawl and download sequentially.

Build the complete URL manifest before starting mass downloads.

---

## 3. Tested URL patterns

### Microsoft

Official PDFs can resolve through:

```text
cdn-dynmedia-1.microsoft.com/is/content/...
```

Tested examples include direct CSR PDFs from 2018 and 2019.

Recognize report names such as:

```text
Corporate Social Responsibility Report
CSR Report
Impact Summary
Environmental Sustainability Report
ESG Report
```

Normalize these into:

```text
SR
```

where they satisfy the project's sustainability-report rules.

Do not search only for filenames containing:

```text
sustainability-report
```

because Microsoft changes naming over time.

---

### Coca-Cola

Official reports commonly use:

```text
coca-colacompany.com/content/dam/
```

Tested report families include:

```text
Business & Sustainability Report
Business and ESG Report
Environmental Update
Portfolio Update
People & Communities Update
```

The report structure changes by year.

Do not force a topic-specific update into the standalone sustainability-report slot.

Use:

```text
STANDALONE_SR
INTEGRATED_REPORT
TOPIC_UPDATE
NO_SINGLE_STANDALONE_SR
```

---

### BP

Tested official URL family:

```text
bp.com/content/dam/bp/
business-sites/en/global/corporate/pdfs/
sustainability/group-reports/
```

Common pattern:

```text
bp-sustainability-report-{YEAR}.pdf
```

Examples were confirmed for multiple years, including 2020, 2021, 2022 and 2024.

Once the pattern has been confirmed:

```text
generate candidate URLs locally
↓
GET only candidates
↓
validate response
```

Do not use Google/search engines for each year.

---

### Shell

Official reporting archive covers the required historical period and exposes direct report PDFs.

Observed pattern:

```text
reports.shell.com/
sustainability-report/{YEAR}/
_assets/downloads/
shell-sustainability-report-{YEAR}.pdf
```

Important classification:

```text
2017–2023
standalone sustainability reports

2024+
sustainability disclosure may be integrated into Annual Report
```

Store:

```text
report_type = SR
```

or:

```text
report_type = IAR
```

accordingly.

---

## 4. Downloader architecture

Use:

```text
Python
asyncio
httpx
SQLite
aiofiles
PyMuPDF
hashlib
```

Flow:

```text
URL MANIFEST
     ↓
GLOBAL ASYNC QUEUE
     ↓
host-specific workers
     ↓
local SSD
     ↓
validation
     ↓
SHA256
     ↓
VERIFIED
```

For official CDN fallbacks:

```text
global concurrency = 50–100
```

but keep individual hosts conservative:

```text
2–5 concurrent downloads per host initially
```

Increase only when responses remain stable.

The tested corporate sources use static/CDN-style paths, which are suitable for direct HTTP retrieval once URLs are known.

---

## 5. Never mix discovery and download

Wrong:

```text
company
↓
search
↓
download
↓
next company
```

Correct:

```text
STEP 1
discover every report URL

STEP 2
store all URLs in SQLite

STEP 3
queue all valid URLs

STEP 4
download asynchronously

STEP 5
validate in parallel
```

The target is:

```text
100,000 resolved URLs
↓
one download queue
```

not repeated interactive web searches.

---

## 6. Database schema

Create:

```text
companies
reports
downloads
validation
errors
```

Important `reports` fields:

```text
company_id
country
ticker
ISIN

report_year
publication_year

report_type

STANDALONE_SR
ESG
CSR
IMPACT
IAR
TOPIC_UPDATE

source
source_url

filename
local_path
bytes
sha256

status
attempts
```

Statuses:

```text
DISCOVERED
QUEUED
DOWNLOADING
DOWNLOADED
VERIFIED

RETRY
INVALID
NOT_FOUND
DUPLICATE
MANUAL_REVIEW
```

---

## 7. PDF validation

For each downloaded object:

```text
HTTP 200
↓
Content-Type
↓
%PDF magic bytes
↓
file size sanity
↓
PyMuPDF opens
↓
page count > 0
↓
first 3–5 pages
↓
company/year/type validation
```

Reject HTML error pages saved as `.pdf`.

---

## 8. Hash while downloading

Calculate SHA-256 during streaming:

```python
sha = hashlib.sha256()

for chunk in stream:
    file.write(chunk)
    sha.update(chunk)
```

Store:

```text
sha256
```

in SQLite.

If hash already exists:

```text
DUPLICATE
```

Keep source provenance but avoid storing another physical copy.

---

## 9. Speed optimization

Always download to:

```text
local SSD
```

not directly to Google Drive.

Use:

```text
HTTP keep-alive
streaming GET
async workers
connection pooling
host-specific concurrency
```

Avoid:

```text
browser automation
HEAD before every GET
OCR
full-PDF parsing during download
Google searches per report
```

---

## 10. Report classification

Search titles and first pages for:

```text
sustainability report
ESG report
CSR report
corporate responsibility
impact report
environmental sustainability
business & sustainability
business and ESG
integrated report
```

Classification order:

```text
1. Standalone Sustainability/ESG/CSR
2. Integrated Report fallback
3. Topic-specific disclosure
4. unavailable
```

Never invent a standalone sustainability report where the company only published topic updates.

---

## 11. Production execution

The final system should run like:

```text
python sustainability.py discover \
    --countries US UK \
    --years 2017:2025
```

Then:

```text
python sustainability.py download \
    --workers 100
```

Then:

```text
python sustainability.py status
```

Display:

```text
Expected company-years
Discovered reports
Standalone SR
Integrated fallback
Topic disclosures
Unavailable

Queued
Downloaded
Verified
Duplicates
Failed

Total GB
Documents/sec
Documents/min
MB/sec
Completion %
```

---

## Final source order

Use:

```text
1. SustainabilityReports.com authorized bulk data / ZIP
2. Official company reporting archive/CDN
3. Corporate Register metadata for completeness checking
4. Manual review only for unresolved cases
```

The key production rule is:

```text
RESOLVE EVERYTHING FIRST
↓
DOWNLOAD EVERYTHING SECOND
```

Once the direct URLs are resolved, mass PDF downloading becomes a simple high-concurrency file-transfer problem rather than a web-search problem.
