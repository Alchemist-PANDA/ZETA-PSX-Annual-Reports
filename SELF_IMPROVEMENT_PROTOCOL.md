# Self-Improvement Protocol: High-Performance Pipeline Architecture

## 1. Context & Purpose
During the TOPIX 100 report collection, the initial Annual Report (`AR`) harvest suffered severe slowdowns due to sequential I/O bottlenecks and unpartitioned network requests. Conversely, the Sustainability Report (`SR`) harvest completed hundreds of files in ~20 minutes with zero corruption and 100% PyMuPDF validation.

This protocol formally codifies the elimination of low-yielding patterns and establishes the high-performing architecture as the mandatory operational standard across all future runs.

---

## 2. Low-Yielding Methods (Permanently Deprecated)

| # | Deprecated Low-Yielding Pattern | Why It Failed / Bottleneck | Mandatory Replacement |
|---|---|---|---|
| **D1** | **Sequential Single-Threaded Crawling** | Synchronous iteration halted the entire pipeline whenever a single host stalled or timed out. | **Bounded asynchronous worker pool**; benchmark worker counts with `benchmarks/optimize.py`. |
| **D2** | **Interleaved Discovery & Download** | Live search engine queries and URL extraction while holding open download threads created idle thread waste. | **Two-Phase Decoupled Architecture**: Pre-resolve all URLs into a structured in-memory matrix before opening download sockets. |
| **D3** | **Triple Disk Round-Trips** | Download to disk $\to$ close $\to$ reopen for PyMuPDF $\to$ close $\to$ reopen for SHA-256 hash $\to$ DB sync caused severe Windows NTFS lock contention. | **Zero-Copy In-RAM Stream Pipeline**: Stream directly into RAM buffer $\to$ validate PyMuPDF $\to$ compute SHA-256 in memory $\to$ single atomic flush to disk. |
| **D4** | **Unbounded Timeouts (60s–600s)** | Throttled or dead TCP sockets held workers hostage for up to 10 minutes per file. | **15s Hard Fail-Fast Timeout**: Drop slow/stalled sockets immediately, push to secondary targeted retry queue. |
| **D5** | **Single Host Queue Flooding** | Firing multiple concurrent requests at a single corporate domain triggered IP bans and severe bandwidth throttling. | **Domain-Sharded Semaphore Scheduling**: Host-level semaphores (`max_concurrent = 2`) with round-robin interleaved queue dispatching. |
| **D6** | **In-Place Partial Writes** | Writing directly to target filenames risked leaving corrupt or truncated partial files if interrupted. | **Atomic `.part` Staging**: Always write to `.part`, validate 100% in RAM, then atomic rename. Zero partial file remnants. |

---

## 3. High-Performing Operational Doctrine

### Phase 1: Pre-Execution Matrix Resolution
- Extract all corporate entities, 20-character ISO 17442 LEIs, 12-character ISO 6166 ISINs, and tickers.
- Resolve and verify direct PDF endpoints offline into a JSON catalog.
- Group and interleave tasks round-robin across distinct host domains to prevent queue hot-spots.

### Phase 2: In-RAM High-Concurrency Stream Engine
- **Network Layer**: `curl_cffi` with TLS/JA3 Chrome fingerprint impersonation, connection pooling, and 15s connect/read timeouts.
- **Validation Layer**: In-memory PyMuPDF validation inspecting `%PDF` header magic bytes, uncorrupted cross-reference tables, and `page_count > 0`.
- **Integrity Layer**: In-memory SHA-256 computation before filesystem touch.
- **Storage Layer**: Atomic write via `.part` file $\to$ atomic rename. Support Windows extended path formatting (`\\?\`) for paths exceeding 260 characters.

### Phase 3: Secondary Specialized Pass for Throttled Hosts
- Keep throttled or failed hosts in a bounded retry pass. Range chunking is an experiment only after the server's range support and source policy are verified; it is not implemented in the current downloader.

### Phase 4: Thread-Safe Database WAL Synchronization
- SQLite configured with `PRAGMA journal_mode = WAL;` and `PRAGMA synchronous = NORMAL;`.
- Use thread-local connections or dedicated write-queue to guarantee zero lock contention during high concurrency.

---

## 4. Verification Checkpoint Checklist
Every automated run must produce:
1. **Zero Orphaned `.part` Files**: Verified via filesystem scan.
2. **100% Validated Page Trees**: Zero 0-page or truncated PDFs.
3. **Canonical V2 Naming**: `<COUNTRY>/<MIC>/<LEI>_<ISIN>_<TICKER>/FY<YEAR>/<LEI>_<COUNTRY>_<MIC>_<TICKER>_<ISIN>_FY<YEAR>_<TYPE>_EN.pdf`.
4. **Automated E2E Test Suite**: Full pass prior to concluding task.

## 5. Measured improvement loop

Future agents must follow `AGENTS.md`. Run `py benchmarks/optimize.py fixture` after a downloader change. It tests fresh outputs for every candidate, checks each file against known SHA-256 and page count, verifies the SQLite ledger, rejects failures and throttling, and saves the fastest valid setting only after two repetitions and at least a 5% median-time gain. The next run tests nearby settings automatically.

For a representative real-source sample, prepare an independently reviewed `golden.csv` and run `py benchmarks/optimize.py real --manifest sample.csv --golden golden.csv --repeats 2`. Do not create the golden file from the very output under test. Record the source mix, bytes, elapsed time, retry counts, rate-limit responses, and any unresolved semantic checks. A structural PDF pass alone does not prove company, fiscal year, or report type.
