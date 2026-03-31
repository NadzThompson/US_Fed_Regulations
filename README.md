# US Federal Reserve Regulations Corpus

Regulatory content from the Board of Governors of the Federal Reserve System, structured for the NOVA RAG pipeline. Content sourced from the [Electronic Code of Federal Regulations (eCFR)](https://www.ecfr.gov/), the [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1), and the [Federal Reserve Board website](https://www.federalreserve.gov/supervisionreg/srletters/).

## Contents

### eCFR Regulations (12 CFR Chapter II)

**59 documents** across 65 active parts:

| Category | Count | Parts | Description |
|----------|-------|-------|-------------|
| Lettered Regulations (A-ZZ) | 45 | 201-253 | Core Federal Reserve regulations governing banking, credit, capital, liquidity, consumer protection, and holding company supervision |
| Administrative/Procedural | 14 | 250-281 | Board rules of procedure, employee conduct, delegation of authority, FOIA, equal opportunity, labor relations, FOMC operations |

**5 reserved parts** (not scraped): 200, 203, 216, 230, 236

### Federal Register Documents

**2,431 documents** from the Federal Register API:

| Type | Count | Description |
|------|-------|-------------|
| Final Rules | 946 | Published final rules (all time) |
| Proposed Rules | 521 | Notices of proposed rulemaking (all time) |
| Notices | 964 | Federal Register notices (2023-present) |

### SR Letters

**338 documents** scraped from the Federal Reserve Board website:

| Coverage | Count | Description |
|----------|-------|-------------|
| SR 90-xx through SR 25-xx | 338 | Supervision and Regulation letters (1990-2025) |

## Folder Structure

Each source follows the same pattern: **raw content** (HTML/PDF) paired 1:1 with **enriched metadata** (JSON). Every document has all four formats.

```
US_Fed_Regulations/
  ecfr/                     <- eCFR regulations (59 parts of 12 CFR Chapter II)
    html/                   <- Raw scraped HTML content (from eCFR renderer API)
    pdf/                    <- Rendered PDF of regulation content
    md/                     <- Markdown text (derived from HTML for readability)
    json/                   <- Enriched NOVA metadata (1:1 with raw content)
  federal_register/         <- Federal Register documents (2,431)
    html/                   <- Raw HTML content (from FR API)
    pdf/                    <- PDF from GovInfo
    md/                     <- Markdown summaries with abstracts
    json/                   <- Enriched NOVA metadata (1:1 with raw content)
  SR_Letters/               <- Supervision & Regulation letters (338)
    html/                   <- Raw scraped HTML content (from federalreserve.gov)
    pdf/                    <- Rendered PDF of letter content
    md/                     <- Markdown text (derived from HTML)
    json/                   <- Enriched NOVA metadata (1:1 with raw content)
  scrapers/                 <- Scraping and enrichment code (reproducible)
    config.py               <- API endpoints, part lists, tier mappings
    scrape_ecfr.py          <- eCFR API scraper
    scrape_federal_register.py <- Federal Register API scraper
    scrape_sr_letters.py    <- SR Letters web scraper
    enrich_metadata.py      <- NOVA 3-layer metadata enrichment + validation
    run_all.py              <- Master pipeline runner
    requirements.txt        <- Python dependencies
  docs/                     <- Reference documentation
```

## What to Ingest into the NOVA RAG Pipeline

The RAG model needs **two files per document**: a raw/original content file and its paired enriched metadata JSON.

### The two files per document

```
                     RAW CONTENT                              ENRICHED METADATA
                     (what the parser reads)                  (what drives the pipeline)
                     ─────────────────────────                ──────────────────────────
eCFR:                ecfr/html/Reg_Q_...12CFR217.html    +   ecfr/json/Reg_Q_...12CFR217.json
Federal Register:    federal_register/html/00-13309...html +  federal_register/json/00-13309...json
SR Letters:          SR_Letters/html/SR_25-6_Status...html +  SR_Letters/json/SR_25-6_Status...json
```

**The JSON file contains NO embedded content** -- it is purely metadata. The raw content that gets parsed, chunked, and embedded lives in the HTML file. The two files are paired 1:1 by filename stem.

### Which raw format to use

| Source | Primary Raw Format | Use This | Why |
|--------|-------------------|----------|-----|
| **eCFR** | **HTML** | `ecfr/html/*.html` | Contains full regulation text with structural markup (`<div class="section">`, `<h3>`, indentation classes). The parser uses this structure to build `heading_path`, `section_path`, and detect `structural_level`. This is the original format returned by the eCFR renderer API. |
| **Federal Register** | **HTML** | `federal_register/html/*.html` | Contains the document summary, abstract, action type, and links to the full FR and GovInfo PDF. For full-text ingestion, follow the `html_url` and `pdf_url` fields in the JSON metadata. |
| **SR Letters** | **HTML** | `SR_Letters/html/*.html` | Contains the full letter text as scraped from federalreserve.gov, including header block, applicability statement, body content, and attachments. |

### Supporting formats (not primary RAG input)

| Format | Role | When to Use |
|--------|------|-------------|
| **MD** (Markdown) | Authoritative plain text derived from HTML | For chunking, embedding (`embed_texts()`), and BM25 indexing. This is the text that gets the Layer 1 semantic header prepended before embedding. Use this if your parser expects plain text rather than HTML. |
| **PDF** | Rendered/archived content | Alternative input for parsers that prefer PDF (e.g., Azure Document Intelligence). Also serves as the human-readable archival format. |
| **JSON** | Enriched NOVA metadata | **Always required alongside the raw content.** Drives all three NOVA metadata layers: `semantic_header()` (Layer 1), index filters (Layer 2), and `render_hit_for_prompt()` (Layer 3). |

### How it flows through the pipeline

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  BRONZE LAYER (raw ingestion)                                          │
  │                                                                        │
  │  ecfr/html/Reg_Q_...html  ───► Parser reads HTML structure             │
  │  ecfr/json/Reg_Q_...json  ───► Enriched metadata loaded alongside      │
  │                                                                        │
  ├─────────────────────────────────────────────────────────────────────────┤
  │  SILVER LAYER (canonicalization)                                        │
  │                                                                        │
  │  Parser produces CanonicalDocument + CanonicalUnits                     │
  │  Metadata from JSON populates: doc_id, heading_path, normative_weight, │
  │    structural_level, authority_class, nova_tier, effective_date_start   │
  │                                                                        │
  │  OR: ecfr/md/Reg_Q_...md  ───► Use MD directly if parser expects text  │
  │                                                                        │
  ├─────────────────────────────────────────────────────────────────────────┤
  │  GOLD LAYER (chunking + embedding)                                     │
  │                                                                        │
  │  Layer 1: semantic_header() prepends metadata to chunk text             │
  │  Layer 2: index fields stored in ES + PGVector                         │
  │  Layer 3: prompt fields assembled for LLM context                      │
  │  Layer 4: operational fields stored for audit                           │
  └─────────────────────────────────────────────────────────────────────────┘
```

### Raw content origin by source

| Source | Original Raw Format | API/URL Used to Scrape | What the HTML Contains |
|--------|-------------------|------------------------|------------------------|
| **eCFR** | HTML | `ecfr.gov/api/renderer/v1/content/enhanced/{date}/title-12?part={N}` | Full regulation text with section structure, indentation, authority citations, appendices, tables |
| **Federal Register** | HTML | Constructed from `federalregister.gov/api/v1/documents.json` response | Document summary with abstract, action, FR/PDF links. Full text available via `html_url` and `pdf_url` in the JSON |
| **SR Letters** | HTML | Scraped from `federalreserve.gov/supervisionreg/srletters/SR{YYNN}.htm` | Full letter text with header block, applicability statement, body, attachments |

## Enriched Metadata (JSON) - NOVA 3-Layer Architecture

Each JSON file contains metadata fields organized across the NOVA 3-layer architecture. All 2,828 files have 100% field coverage.

### Layer 1: Embedding Fields ("Baked Into the Vector")

These fields are prepended to chunk text via `semantic_header()` before calling `embed_texts()`. They change what the passage MEANS in vector space.

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

These fields are stored in Elasticsearch/PGVector for filtering and boosting at query time. They never touch the embedding vector.

| Field | Description | Example |
|-------|-------------|---------|
| `status` | Document status | `active` |
| `jurisdiction` | Geographic scope | `United States` |
| `nova_tier` | Authority rank (1-4) | `1` (core prudential) |
| `authority_class` | Normative vs interpretive | `primary_normative` |
| `current_version_flag` | Is this the latest version | `true` |
| `effective_date_start` | When it took effect (true effective date, not scrape date) | `2025-12-01` |
| `effective_date_end` | When it expires (null = current) | `null` |
| `paragraph_role` | Semantic role | `scope_statement`, `rationale` |
| `contains_definition` | Has definitions | `true` |
| `contains_requirement` | Has "shall"/"must" obligations | `true` |
| `contains_formula` | Has formulas/calculations | `false` |
| `contains_deadline` | Has date-bound requirements | `true` |
| `contains_parameter` | Has thresholds/limits | `true` |
| `is_appendix` | Is appendix content | `false` |
| `doc_family_id` | Groups versions of same doc | `usfed.regq.217` |
| `superseded_by_doc_id` | Points to replacement | `null` |
| `bm25_text` | Pre-built BM25 search text | (concatenated title + headings) |

### Layer 3: Prompt Injection Fields ("What the LLM Reasons About")

These fields are injected into the LLM context via `render_hit_for_prompt()` at answer time. They help the LLM generate accurate, caveated answers.

| Field | Description | Example |
|-------|-------------|---------|
| `title` | Full document title | `Regulation Q: Capital Adequacy of Bank Holding Companies` |
| `citation_anchor` | Precise citation | `#12cfr217` |
| `version_id` | Temporal version (= effective_date_start) | `2025-12-01` |
| `version_label` | Human-readable version year | `2025` |
| `normative_weight` | Obligation level | `mandatory` |
| `paragraph_role` | What this content does | `scope_statement` |
| `authority_class` | How authoritative | `primary_normative` |
| `nova_tier` | Priority ranking | `1` |

### Layer 4: Operational Fields (Audit Trail)

Never shown to the embedding model or LLM. Used for debugging, lineage, and quality tracking.

| Field | Description | Example |
|-------|-------------|---------|
| `normalized_text_sha256` | Content hash for change detection | `4952f722...` |
| `normalized_md_path` | Path to MD file | `ecfr/md/Reg_Q_...12CFR217.md` |
| `canonical_json_path` | Path to this JSON file | `ecfr/json/Reg_Q_...12CFR217.json` |
| `parser_version` | Scraper version that produced this | `nova-ecfr-scraper-v1` |
| `normalizer_version` | Enricher version | `nova-ecfr-enricher-v2` |
| `quality_score` | Data quality score (0-100) | `100` |
| `quality_flags` | Any quality issues | `[]` |
| `scraped_on` | Date scraped | `2026-03-26` |
| `enriched_timestamp` | Last enrichment timestamp | `2026-03-31T14:37:56Z` |

### Temporal Fields: Effective Date vs Scrape Date

These are different dates that serve different purposes. `effective_date_start` is the date the regulation or guidance came into force. `scraped_on` / `ecfr_current_as_of` is when the data was collected.

| Field | What It Represents | Example (Reg Q) |
|-------|-------------------|-----------------|
| `effective_date_start` | When the current version of the regulation came into effect (latest amendment date) | `2025-12-01` |
| `original_effective_date` | When the regulation first appeared (eCFR only) | `2017-01-01` |
| `effective_date_end` | When it expires or was superseded (null = still current) | `null` |
| `ecfr_current_as_of` | The eCFR snapshot date used for scraping (eCFR only) | `2026-03-09` |
| `scraped_on` | Date the scraper ran | `2026-03-26` |
| `enriched_timestamp` | When metadata enrichment last ran | `2026-03-31T15:22:24Z` |
| `version_id` | Same as `effective_date_start` (for temporal versioning) | `2025-12-01` |
| `publication_date` | Date published in the Federal Register (FR only) | `2000-05-26` |
| `document_date_iso` | Letter issuance date (SR only) | `2025-12-19` |

**How `effective_date_start` is determined per source:**

| Source | How Effective Date Is Set |
|--------|--------------------------|
| **eCFR** | Latest amendment date from the eCFR versioner API (`/api/versioner/v1/versions/title-12?part={N}`). This is when the most recent change to the regulation took effect. |
| **Federal Register** | The `effective_on` field from the FR API. Null for proposed rules and notices (they don't have formal effective dates). |
| **SR Letters** | The letter issuance date (`document_date_iso`). SR letters are effective upon issuance. |

### Additional metadata by source

**eCFR-specific:** `title_number`, `chapter`, `part_number`, `part_name`, `regulation_letter`, `cfr_citation`, `authority`, `section_count`, `appendix_count`, `section_headings`, `ecfr_current_as_of`, `original_effective_date`, applicability flags (`applies_to_state_member_banks`, `applies_to_bank_holding_companies`, etc.)

**Federal Register-specific:** `document_number`, `citation`, `publication_date`, `abstract`, `action`, `html_url`, `pdf_url`, `cfr_references`, `docket_ids`, `page_length`

**SR Letter-specific:** `sr_letter_number`, `sr_year`, `sr_sequence`, `document_date_iso`, `applicability_raw`, `applies_to_large_institutions`

## NOVA Tier Assignment

| Tier | Description | Parts |
|------|-------------|-------|
| **1** | Core prudential (capital, liquidity, supervision) | 217 (Reg Q), 225 (Reg Y), 249 (Reg WW), 252 (Reg YY), 248 (Reg VV), 238 (Reg LL), 208 (Reg H) |
| **2** | Important regulatory (most lettered regulations, SR letters) | 201-244 (most), 246, 251, 253, all SR letters |
| **3** | Supplementary/administrative (FR notices, admin parts) | 207, 209, 212-214, 219, 224, 231-232, 241-242, 261-272, 281 |
| **4** | Reference/interpretive | 250 (Miscellaneous Interpretations) |

## Corpus Statistics

| Metric | Value |
|--------|-------|
| Total documents | 2,828 |
| eCFR regulations | 59 |
| Federal Register documents | 2,431 |
| SR Letters | 338 |
| Total sections (eCFR) | 1,449 |
| Total words (eCFR) | 1,114,528 |
| eCFR date | 2026-03-24 |
| Federal Register coverage | All rules/proposed rules + notices 2023-present |
| SR Letter coverage | 1990-2025 |
| Metadata completeness | 100% (all NOVA 3-layer fields) |
| File pairing | 100% (every document has HTML + PDF + MD + JSON) |

## Reproducing the Scrape

The `scrapers/` directory contains the complete code to re-scrape and re-enrich the entire corpus.

```bash
# Install dependencies
pip install -r scrapers/requirements.txt

# Full pipeline: scrape all 3 sources + enrich metadata
python -m scrapers.run_all

# Individual scrapers
python -m scrapers.scrape_ecfr --date 2026-03-09
python -m scrapers.scrape_federal_register --types final_rules proposed_rules notices
python -m scrapers.scrape_sr_letters --years 2024 2025

# Re-enrich existing metadata (no re-scrape)
python -m scrapers.enrich_metadata

# Validate without modifying files
python -m scrapers.enrich_metadata --validate-only
```

## Sources

- eCFR API: https://www.ecfr.gov/api/versioner/v1/
- Federal Register API: https://www.federalregister.gov/api/v1/
- SR Letters: https://www.federalreserve.gov/supervisionreg/srletters/
- 12 CFR Chapter II: https://www.ecfr.gov/current/title-12/chapter-II

## Related Documentation

See the `docs/` folder for additional reference materials, including the NOVA Corpus Guide and Metadata Build Specification.
