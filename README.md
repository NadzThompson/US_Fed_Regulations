# US Federal Reserve Regulations Corpus

Regulatory content from the Board of Governors of the Federal Reserve System, structured for the NOVA RAG pipeline. Content sourced from the [Electronic Code of Federal Regulations (eCFR)](https://www.ecfr.gov/), the [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1), and the [Federal Reserve Board website](https://www.federalreserve.gov/supervisionreg/srletters/).

## Contents

### eCFR Regulations (12 CFR Chapter II)

**59 regulation parts** across the full Federal Reserve regulatory framework:

| Category | Count | Parts | Description |
|----------|-------|-------|-------------|
| Lettered Regulations (A-ZZ) | 45 | 201-253 | Core Federal Reserve regulations governing banking, credit, capital, liquidity, consumer protection, and holding company supervision |
| Administrative/Procedural | 14 | 250-281 | Board rules of procedure, employee conduct, delegation of authority, FOIA, equal opportunity, labor relations, FOMC operations |

### Federal Register Documents

**~3,037 documents** from the Federal Register API (Federal Reserve System agency only):

| Type | Count | Date Range | Description |
|------|-------|------------|-------------|
| Final Rules | ~754 | 2000-present | Published final rules (binding regulations) |
| Proposed Rules | ~268 | 2010-present | Notices of proposed rulemaking |
| Notices | ~2,015 | 2020-present | Federal Register notices |

### SR Letters (Supervision and Regulation)

**338 SR letters** with **~490 PDF attachments** scraped from the Federal Reserve Board website:

| Coverage | Source |
|----------|--------|
| SR 90-xx through SR 25-xx (1990-2025) | [All years index](https://www.federalreserve.gov/supervisionreg/srletters/sr-letters-all-years.htm) |

---

## What to Ingest into the NOVA RAG Pipeline

### The key principle: raw content + enriched metadata JSON

Every document has a **JSON metadata file** that links to all of its associated raw content files. The JSON contains the `connected_files` field that maps out every artifact for that document.

**Always ingest the JSON metadata file + all raw content files it references.**

### SR Letters: Multi-file documents

SR letters are the most complex because a single letter can have **multiple related files** that should all be ingested together. The JSON metadata's `connected_files` field maps the complete document graph.

**Example: SR 11-7 (Guidance on Model Risk Management)**

```
Ingest ALL of these together:

  SR_Letters/json/SR_11-7_...json              <- Enriched metadata (the index)
  SR_Letters/raw_html/SR_11-7_...html          <- Original HTML page from federalreserve.gov
  SR_Letters/pdf/SR 11-7 - Letter.pdf          <- The SR letter itself (PDF)
  SR_Letters/pdf/SR 11-7 - Attachment 1 - Model Risk Management Guidance.pdf
                                                <- The attached guidance document (PDF)
```

The JSON metadata connects these via `connected_files`:

```json
{
  "connected_files": {
    "source_html_url": "https://www.federalreserve.gov/supervisionreg/srletters/sr1107.htm",
    "raw_html_local": "SR_Letters/raw_html/SR_11-7_...html",
    "metadata_json": "SR_Letters/json/SR_11-7_...json",
    "markdown_summary": "SR_Letters/md/SR_11-7_...md",
    "pdf_attachments": [
      {
        "filename": "SR 11-7 - Letter.pdf",
        "source_url": "https://www.federalreserve.gov/supervisionreg/srletters/sr1107.pdf",
        "local_path": "SR_Letters/pdf/SR 11-7 - Letter.pdf",
        "description": "PDF"
      },
      {
        "filename": "SR 11-7 - Attachment 1 - Model Risk Management Guidance.pdf",
        "source_url": "https://www.federalreserve.gov/supervisionreg/srletters/sr1107a1.pdf",
        "local_path": "SR_Letters/pdf/SR 11-7 - Attachment 1 - Model Risk Management Guidance.pdf",
        "description": "Model Risk Management Guidance"
      }
    ]
  }
}
```

**To ingest SR letters:** iterate over each JSON file, read `connected_files.pdf_attachments` to find all PDFs that belong to this letter, and ingest them together with the JSON metadata applied to each.

### eCFR Regulations: Single-document files

Each eCFR regulation part is a single document with one raw HTML, one PDF, and one JSON. The `raw_html/` directory contains the **original HTML from the eCFR API** with full structural markup, hyperlinks, and hierarchy metadata. The `html/` directory contains a styled version for display.

**Example: Reg YY (12 CFR Part 252 - Enhanced Prudential Standards)**

```
Ingest these together:

  ecfr/json/Reg_YY_...12CFR252.json            <- Enriched metadata
  ecfr/raw_html/Reg_YY_...12CFR252.html        <- Original eCFR API HTML (best for RAG)
  ecfr/pdf/Reg_YY_...12CFR252.pdf              <- Generated PDF from raw HTML
```

The JSON metadata connects these via `connected_files`:

```json
{
  "connected_files": {
    "source_url": "https://www.ecfr.gov/current/title-12/chapter-II/part-252",
    "source_api_url": "https://www.ecfr.gov/api/renderer/v1/content/enhanced/2026-03-09/title-12?part=252",
    "raw_html_local": "ecfr/raw_html/Reg_YY_...12CFR252.html",
    "styled_html_local": "ecfr/html/Reg_YY_...12CFR252.html",
    "metadata_json": "ecfr/json/Reg_YY_...12CFR252.json",
    "markdown": "ecfr/md/Reg_YY_...12CFR252.md",
    "pdf_local": "ecfr/pdf/Reg_YY_...12CFR252.pdf"
  }
}
```

**For RAG ingestion, use `raw_html/` (not `html/`).** The `raw_html/` contains the original eCFR API response with proper paragraph structure, section hierarchy, and USC hyperlinks. The `html/` is a re-rendered styled version.

### Federal Register: Full body HTML + PDF

Each Federal Register document has the **full document body HTML** fetched from the FR API's `body_html_url` endpoint, plus the **official PDF** from GovInfo.

**Example: Final Rule 2025-21579**

```
Ingest these together:

  federal_register/json/2025-21579_...json      <- Enriched metadata
  federal_register/raw_html/2025-21579_...html  <- Full body HTML from FR API
  federal_register/pdf/2025-21579_...pdf        <- Official PDF from GovInfo
```

The JSON metadata connects these via `connected_files`:

```json
{
  "connected_files": {
    "source_html_url": "https://www.federalregister.gov/documents/...",
    "body_html_url": "https://www.federalregister.gov/documents/full_text/html/...",
    "pdf_source_url": "https://www.govinfo.gov/content/pkg/FR-.../pdf/...",
    "raw_html_local": "federal_register/raw_html/2025-21579_...html",
    "styled_html_local": "federal_register/html/2025-21579_...html",
    "metadata_json": "federal_register/json/2025-21579_...json",
    "markdown": "federal_register/md/2025-21579_...md",
    "pdf_local": "federal_register/pdf/2025-21579_...pdf"
  }
}
```

---

## Folder Structure

```
US_Fed_Regulations/
  ecfr/                        <- eCFR regulations (59 parts of 12 CFR Chapter II)
    raw_html/                  <- ORIGINAL HTML from eCFR API (ingest this)
    html/                      <- Styled HTML for display (re-rendered)
    pdf/                       <- PDF generated from raw HTML
    md/                        <- Markdown text (derived for readability)
    json/                      <- Enriched NOVA metadata with connected_files

  federal_register/            <- Federal Register documents (~3,037)
    raw_html/                  <- Full body HTML from FR API body_html_url (ingest this)
    html/                      <- Styled HTML with metadata header + body
    pdf/                       <- Official PDF from GovInfo (ingest this)
    md/                        <- Markdown with full document text
    json/                      <- Enriched NOVA metadata with connected_files

  SR_Letters/                  <- Supervision & Regulation letters (338 letters, ~490 PDFs)
    raw_html/                  <- Original HTML from federalreserve.gov (ingest this)
    pdf/                       <- Letter PDFs + attachment PDFs (ingest ALL per letter)
    md/                        <- Markdown summary with attachment links
    json/                      <- Enriched NOVA metadata with connected_files

  scrapers/                    <- Scraping and enrichment code
    config.py                  <- API endpoints, part lists, tier mappings
    scrape_ecfr.py             <- eCFR API scraper (v2 with raw HTML + version enrichment)
    scrape_federal_register.py <- Federal Register API scraper (v2 with full body HTML)
    scrape_sr_letters.py       <- SR Letters web scraper (v2 with PDF attachments)
    enrich_metadata.py         <- NOVA 3-layer metadata enrichment + validation
    run_all.py                 <- Master pipeline runner
```

## Ingestion Summary

| Source | JSON (metadata) | Files to Ingest with Each JSON | Total Raw Files |
|--------|----------------|-------------------------------|-----------------|
| **eCFR** | 59 | 1 raw HTML + 1 PDF per regulation part | 118 |
| **Federal Register** | ~3,037 | 1 raw HTML + 1 PDF per document | ~6,074 |
| **SR Letters** | 338 | 1 raw HTML + 1-3 PDFs per letter (letter + attachments) | ~828 |

**How to determine which files belong to an SR letter:**
```python
import json

with open("SR_Letters/json/SR_11-7_....json") as f:
    meta = json.load(f)

# The raw HTML page
html_path = meta["connected_files"]["raw_html_local"]

# All PDFs for this letter (letter + attachments)
for pdf in meta["connected_files"]["pdf_attachments"]:
    pdf_path = pdf["local_path"]       # e.g. "SR_Letters/pdf/SR 11-7 - Letter.pdf"
    source_url = pdf["source_url"]     # original URL on Fed website
    description = pdf["description"]   # e.g. "Model Risk Management Guidance"
```

---

## Enriched Metadata (JSON) - NOVA 3-Layer Architecture

Each JSON file contains metadata fields organized across the NOVA 3-layer architecture plus source-specific fields and the `connected_files` linking structure.

### Layer 1: Embedding Fields ("Baked Into the Vector")

Prepended to chunk text via `semantic_header()` before embedding.

| Field | Description | Example |
|-------|-------------|---------|
| `doc_id` | Unique document identifier | `usfed.regq.217.20260309.part` |
| `short_title` | Short reference name | `Reg Q` |
| `document_class` | Document type | `federal_regulation`, `sr_letter` |
| `heading_path` | Hierarchical breadcrumb (array) | `["Federal Reserve System", "12 CFR Chapter II", "Regulation Q", "Part 217"]` |
| `section_path` | Flattened breadcrumb | `Federal Reserve System > 12 CFR Chapter II > Regulation Q > Part 217` |
| `regulator` | Issuing authority | `Federal Reserve System` |
| `structural_level` | Position in hierarchy | `part`, `document` |
| `normative_weight` | Obligatory force | `mandatory`, `advisory`, `informational` |

### Layer 2: Index/Filter Fields ("Gates and Boosts Retrieval")

Stored in Elasticsearch/PGVector for filtering and boosting at query time.

| Field | Description | Example |
|-------|-------------|---------|
| `status` | Document status | `active` |
| `nova_tier` | Authority rank (1-4) | `1` (core prudential) |
| `authority_class` | Normative vs interpretive | `primary_normative` |
| `current_version_flag` | Is this the latest version | `true` |
| `effective_date_start` | When it took effect | `2025-12-01` |
| `effective_date_end` | When it expires (null = current) | `null` |
| `contains_definition` | Has definitions | `true` |
| `contains_requirement` | Has "shall"/"must" obligations | `true` |
| `contains_formula` | Has formulas/calculations | `false` |
| `contains_deadline` | Has date-bound requirements | `true` |
| `contains_parameter` | Has thresholds/limits | `true` |
| `doc_family_id` | Groups versions of same doc | `usfed.regq.217` |
| `superseded_by_doc_id` | Points to replacement | `null` |
| `bm25_text` | Pre-built BM25 search text | (concatenated title + headings) |

### Layer 3: Prompt Injection Fields ("What the LLM Reasons About")

Injected into LLM context via `render_hit_for_prompt()` at answer time.

| Field | Description | Example |
|-------|-------------|---------|
| `title` | Full document title | `Regulation Q: Capital Adequacy of Bank Holding Companies` |
| `citation_anchor` | Precise citation | `#12cfr217` |
| `version_id` | Temporal version | `2025-12-01` |
| `version_label` | Human-readable version year | `2025` |
| `normative_weight` | Obligation level | `mandatory` |
| `authority_class` | How authoritative | `primary_normative` |
| `nova_tier` | Priority ranking | `1` |

### Layer 4: Operational Fields (Audit Trail)

| Field | Description | Example |
|-------|-------------|---------|
| `normalized_text_sha256` | Content hash for change detection | `4952f722...` |
| `raw_html_path` | Path to original HTML file | `ecfr/raw_html/Reg_Q_...html` |
| `normalized_md_path` | Path to markdown file | `ecfr/md/Reg_Q_...md` |
| `canonical_json_path` | Path to this JSON file | `ecfr/json/Reg_Q_...json` |
| `connected_files` | Links to all artifacts for this document | (see examples above) |
| `parser_version` | Scraper version | `nova-ecfr-scraper-v1` |
| `quality_score` | Data quality score (0-100) | `100` |
| `scraped_on` | Date scraped | `2026-04-01` |

### Versioning and Temporal Fields

| Field | What It Represents | eCFR | Federal Register | SR Letters |
|-------|-------------------|------|-----------------|------------|
| `effective_date_start` | When current version took effect | Latest amendment date from versions API | `effective_on` from FR API | Letter issuance date |
| `original_effective_date` | When first enacted | Earliest amendment date | N/A | N/A |
| `effective_date_end` | When expired/superseded | null = current | null | null |
| `amendment_count` | Total amendments over lifetime | From versions API (e.g. 28 for Reg YY) | N/A | N/A |
| `amendment_dates` | Recent amendment dates | Last 5 dates | N/A | N/A |
| `supersedes` | What this document replaces | `supersedes_doc_id` | `correction_of` | SR numbers superseded |
| `superseded_by_doc_id` | What replaced this document | doc_id of replacement | N/A | N/A |
| `correction_of` | Document this corrects (FR only) | N/A | doc number | N/A |
| `corrections` | Documents that correct this (FR only) | N/A | list of doc numbers | N/A |

### Source-Specific Metadata

**eCFR:** `title_number`, `chapter`, `part_number`, `part_name`, `regulation_letter`, `cfr_citation`, `authority`, `section_count`, `appendix_count`, `section_headings`, `ecfr_current_as_of`, `original_effective_date`, `amendment_count`, `amendment_dates`, applicability flags (`applies_to_state_member_banks`, `applies_to_bank_holding_companies`, etc.)

**Federal Register:** `document_number`, `citation`, `publication_date`, `abstract` (full, not truncated), `action`, `html_url`, `body_html_url`, `pdf_url`, `cfr_references`, `docket_ids`, `regulation_id_numbers`, `page_length`, `start_page`, `end_page`, `correction_of`, `corrections`, `is_correction`, `has_corrections`

**SR Letters:** `sr_letter_number`, `sr_year`, `sr_sequence`, `topic_code`, `joint_letters`, `document_date_iso`, `applicability_raw`, `supersedes`, `related_letters`, `pdf_attachments`, `pdf_attachments_detail` (with source URLs), `attachment_count`

---

## Raw Content Provenance

| Source | `raw_html/` Origin | `pdf/` Origin |
|--------|-------------------|---------------|
| **eCFR** | eCFR renderer API: `ecfr.gov/api/renderer/v1/content/enhanced/{date}/title-12?part={N}` | Generated from raw HTML using xhtml2pdf |
| **Federal Register** | Full body HTML from FR API: `federalregister.gov/documents/full_text/html/{date}/{doc}.html` | Official government PDF from GovInfo: `govinfo.gov/content/pkg/FR-{date}/pdf/{doc}.pdf` |
| **SR Letters** | Original page from: `federalreserve.gov/supervisionreg/srletters/SR{YYNN}.htm` | Downloaded from Fed website (letter PDF + all attachment PDFs) |

## NOVA Tier Assignment

| Tier | Description | Parts |
|------|-------------|-------|
| **1** | Core prudential (capital, liquidity, supervision) | 217 (Reg Q), 225 (Reg Y), 249 (Reg WW), 252 (Reg YY), 248 (Reg VV), 238 (Reg LL), 208 (Reg H) |
| **2** | Important regulatory (most lettered regulations, SR letters) | 201-244 (most), 246, 251, 253, all SR letters |
| **3** | Supplementary/administrative (FR notices, admin parts) | 207, 209, 212-214, 219, 224, 231-232, 241-242, 261-272, 281 |
| **4** | Reference/interpretive | 250 (Miscellaneous Interpretations) |

## Scraping Architecture

### How the scrapers work

```
                           US Federal Reserve Regulatory Sources
                           =====================================

  eCFR API                    Federal Register API           Federal Reserve Website
  (ecfr.gov)                  (federalregister.gov)          (federalreserve.gov)
  ──────────                  ────────────────────           ───────────────────────
  REST API                    REST API                       Web scraping (HTML)
  ──────────                  ────────────────────           ───────────────────────
       |                            |                              |
       v                            v                              v
  1. Fetch structure         1. Paginate through          1. Fetch all-years index
     for 12 CFR Ch. II          documents by type            (1990-2025)
       |                         (newest first)                |
  2. For each of 59 parts:      |                        2. For each of 36 year pages:
     - GET /renderer/v1/     2. For each document:          - Extract SR letter links
       content/enhanced/        - Fetch metadata             |
       {date}/title-12?         - GET body_html_url       3. For each of 338 SR letters:
       part={N}                 - Download PDF from          - Fetch individual page
       |                          GovInfo                    - Extract date, title,
  3. Save raw HTML              |                              applicability
     (original API          3. Save:                        - Find all PDF links
      response)                - raw_html/ (full body)      - Download letter PDF
       |                       - pdf/ (GovInfo PDF)           + all attachment PDFs
  4. Parse sections,           - html/ (styled)               |
     authority, source         - md/ (full text)          4. Save:
       |                       - json/ (metadata)            - raw_html/ (original page)
  5. Enrich with                                             - pdf/ (named descriptively)
     versions API                                            - md/ (summary + body)
     (amendment dates)                                       - json/ (metadata with
       |                                                       connected_files linking)
  6. Generate:
     - raw_html/ (original)
     - html/ (styled)
     - pdf/ (from raw HTML)
     - md/ (text version)
     - json/ (metadata with
       connected_files +
       effective dates)
```

### SR Letter document relationships

SR letters often have multiple related files. The JSON metadata's `connected_files` field maps the complete document graph:

```
  SR 11-7: Guidance on Model Risk Management
  ============================================

  Source: https://www.federalreserve.gov/supervisionreg/srletters/sr1107.htm
          (the HTML page on the Fed website)
           |
           |--- contains link to --->  sr1107.pdf    (the letter itself)
           |--- contains link to --->  sr1107a1.pdf  (Model Risk Management Guidance)
           |--- references ----------> SR 09-1       (related SR letter)
           |
           v
  Scraper produces:
  ┌──────────────────────────────────────────────────────────────────────────────┐
  |                                                                              |
  |  SR_Letters/json/SR_11-7_...json         <- ENRICHED METADATA (the index)    |
  |    |                                                                         |
  |    |-- connected_files.source_html_url   -> federalreserve.gov/.../sr1107.htm|
  |    |-- connected_files.raw_html_local    -> SR_Letters/raw_html/SR_11-7_...  |
  |    |-- connected_files.pdf_attachments:                                      |
  |    |     [0] SR 11-7 - Letter.pdf        -> SR_Letters/pdf/...               |
  |    |         source_url: .../sr1107.pdf                                      |
  |    |     [1] SR 11-7 - Attachment 1 -                                        |
  |    |         Model Risk Management       -> SR_Letters/pdf/...               |
  |    |         Guidance.pdf                                                    |
  |    |         source_url: .../sr1107a1.pdf                                    |
  |    |                                                                         |
  |    |-- supersedes: []                                                        |
  |    |-- related_letters: ["SR 09-1"]                                          |
  |    |-- effective_date_start: "2011-04-04"                                    |
  |    |-- status: "active"                                                      |
  |                                                                              |
  |  SR_Letters/raw_html/SR_11-7_...html     <- ORIGINAL HTML from Fed website   |
  |  SR_Letters/pdf/SR 11-7 - Letter.pdf     <- THE SR LETTER (PDF)              |
  |  SR_Letters/pdf/SR 11-7 - Attachment 1   <- GUIDANCE DOCUMENT (PDF)          |
  |       - Model Risk Management                                               |
  |         Guidance.pdf                                                         |
  |  SR_Letters/md/SR_11-7_...md             <- MARKDOWN SUMMARY                 |
  |                                                                              |
  └──────────────────────────────────────────────────────────────────────────────┘

  For RAG ingestion: ingest the JSON + raw_html + ALL PDFs listed in
  connected_files.pdf_attachments as a single document group.
```

### eCFR document relationships

eCFR regulations are self-contained. Each part is a single document with one raw HTML and one PDF:

```
  Reg YY (12 CFR Part 252): Enhanced Prudential Standards
  ========================================================

  Source: https://www.ecfr.gov/current/title-12/chapter-II/part-252
  API:    https://www.ecfr.gov/api/renderer/v1/content/enhanced/2026-03-09/title-12?part=252
           |
           v
  ┌────────────────────────────────────────────────────────────────┐
  |                                                                |
  |  ecfr/json/Reg_YY_...json              <- ENRICHED METADATA   |
  |    |-- connected_files.source_url                              |
  |    |-- connected_files.raw_html_local  -> ecfr/raw_html/...   |
  |    |-- connected_files.pdf_local       -> ecfr/pdf/...        |
  |    |-- effective_date_start: "2025-12-01"                      |
  |    |-- original_effective_date: "2016-12-27"                   |
  |    |-- amendment_count: 28                                     |
  |    |-- amendment_dates: ["2021-04-05", ..., "2025-12-01"]      |
  |                                                                |
  |  ecfr/raw_html/Reg_YY_...html          <- ORIGINAL eCFR HTML  |
  |    (full structured HTML with paragraph hierarchy,             |
  |     USC hyperlinks, data-hierarchy-metadata attributes)        |
  |                                                                |
  |  ecfr/pdf/Reg_YY_...pdf                <- GENERATED PDF       |
  |  ecfr/html/Reg_YY_...html              <- STYLED HTML         |
  |  ecfr/md/Reg_YY_...md                  <- MARKDOWN TEXT       |
  |                                                                |
  └────────────────────────────────────────────────────────────────┘
```

### Federal Register document relationships

Each FR document has a body HTML (full text from the API) and an official PDF from GovInfo. The versioning tracks corrections:

```
  Federal Register Final Rule 2025-21579
  ========================================

  Source: https://www.federalregister.gov/documents/2025/10/24/2025-21579/...
  Body:   https://www.federalregister.gov/documents/full_text/html/.../2025-21579.html
  PDF:    https://www.govinfo.gov/content/pkg/FR-.../pdf/2025-21579.pdf
           |
           v
  ┌────────────────────────────────────────────────────────────────┐
  |                                                                |
  |  federal_register/json/2025-21579_...json  <- ENRICHED META   |
  |    |-- connected_files.source_html_url                         |
  |    |-- connected_files.body_html_url                           |
  |    |-- connected_files.pdf_source_url                          |
  |    |-- connected_files.raw_html_local                          |
  |    |-- connected_files.pdf_local                               |
  |    |-- correction_of: null  (or doc number if this corrects)   |
  |    |-- corrections: []      (docs that correct this one)       |
  |    |-- cfr_references: [{title: 12, part: 252, ...}]           |
  |    |-- effective_date_start: "2025-10-24"                      |
  |                                                                |
  |  federal_register/raw_html/2025-21579_...html <- FULL BODY    |
  |  federal_register/pdf/2025-21579_...pdf       <- GOVINFO PDF  |
  |  federal_register/html/2025-21579_...html     <- STYLED HTML  |
  |  federal_register/md/2025-21579_...md         <- FULL TEXT MD  |
  |                                                                |
  └────────────────────────────────────────────────────────────────┘
```

## Reproducing the Scrape

```bash
# Install dependencies
pip install requests beautifulsoup4 lxml xhtml2pdf

# Full pipeline
python -m scrapers.scrape_ecfr --date 2026-03-09
python -m scrapers.scrape_federal_register
python -m scrapers.scrape_sr_letters

# Specific subsets
python -m scrapers.scrape_ecfr --parts 217 252 249          # Specific regulation parts
python -m scrapers.scrape_federal_register --types final_rules --after 2024-01-01
python -m scrapers.scrape_sr_letters --years 2024 2025      # Specific years
python -m scrapers.scrape_sr_letters --letters "SR 11-7"    # Specific letters
```

## Sources

- eCFR API: https://www.ecfr.gov/api/versioner/v1/
- Federal Register API: https://www.federalregister.gov/api/v1/
- SR Letters (all years): https://www.federalreserve.gov/supervisionreg/srletters/sr-letters-all-years.htm
- 12 CFR Chapter II: https://www.ecfr.gov/current/title-12/chapter-II
