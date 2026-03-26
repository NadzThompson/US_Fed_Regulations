# US Federal Reserve Regulations Corpus

Regulatory content from the Board of Governors of the Federal Reserve System, structured for the NOVA metadata pipeline. Content sourced from the [Electronic Code of Federal Regulations (eCFR)](https://www.ecfr.gov/) and the [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1).

## Contents

### eCFR Regulations (12 CFR Chapter II)

**59 documents** across 65 active parts:

| Category | Count | Parts | Description |
|----------|-------|-------|-------------|
| Lettered Regulations (A–ZZ) | 45 | 201–253 | Core Federal Reserve regulations governing banking, credit, capital, liquidity, consumer protection, and holding company supervision |
| Administrative/Procedural | 14 | 250–281 | Board rules of procedure, employee conduct, delegation of authority, FOIA, equal opportunity, labor relations, FOMC operations |

**5 reserved parts** (not scraped): 200, 203, 216, 230, 236

### Federal Register Documents

**2,431 documents** from the Federal Register API:

| Type | Count | Description |
|------|-------|-------------|
| Final Rules | 946 | Published final rules (all time) |
| Proposed Rules | 521 | Notices of proposed rulemaking (all time) |
| Notices | 964 | Federal Register notices (2023–present) |

## Folder Structure

```
US_Fed_Regulations/
  ecfr/                <- eCFR regulations (59 parts of 12 CFR Chapter II)
    json/              <- NOVA metadata per document (no embedded content)
    md/                <- Readable text content (authoritative)
    html/              <- Formatted HTML with metadata tags
    pdf/               <- Rendered PDF
  federal_register/    <- Federal Register documents
    json/              <- NOVA metadata per document
    md/                <- Document summaries with abstracts
    html/              <- Formatted HTML with metadata tags
  docs/                <- Reference documentation
```

## File Formats

- **JSON** (metadata only): NOVA-aligned metadata fields covering identification, classification, hierarchy, temporal, and pipeline fields. No content is embedded.
- **MD** (content): The authoritative text content for each regulation part. Use this for ingestion, search indexing, and embedding.
- **HTML** (formatted): Styled HTML with semantic metadata tags for rendering and PDF generation.
- **PDF** (rendered): PDF rendering of the regulation content.

## Key Metadata Fields

| Field | Description | Coverage |
|-------|-------------|----------|
| `doc_id` | Unique identifier (e.g., `usfed.regq.217.20260309.part`) | 100% |
| `title` | Full document title | 100% |
| `short_title` | Short reference (e.g., `Reg Q`, `Part 263`) | 100% |
| `status` | Document status (`active`) | 100% |
| `regulator` | `Federal Reserve System` | 100% |
| `regulator_acronym` | `FRS` | 100% |
| `document_class` | Type: `federal_regulation`, `rules_of_procedure`, etc. | 100% |
| `nova_tier` | NOVA importance tier (1 = core prudential, 4 = reference) | 100% |
| `jurisdiction` | `United States` | 100% |
| `authority_class` | `primary_normative`, `procedural_administrative`, `reference_interpretive` | 100% |
| `cfr_citation` | CFR citation (e.g., `12 CFR Part 217`) | 100% eCFR |
| `regulation_letter` | Regulation letter (A–ZZ) for lettered regulations | 45 docs |
| `section_count` | Number of sections in the part | 100% eCFR |
| `word_count_raw_body` | Word count of regulation text | 100% eCFR |

## NOVA Tier Assignment

| Tier | Description | Parts |
|------|-------------|-------|
| **1** | Core prudential (capital, liquidity, supervision) | 217 (Reg Q), 225 (Reg Y), 249 (Reg WW), 252 (Reg YY), 248 (Reg VV), 238 (Reg LL), 208 (Reg H) |
| **2** | Important regulatory (most lettered regulations) | 201–244 (most), 246, 251, 253 |
| **3** | Supplementary/administrative | 207, 209, 212–214, 219, 224, 231–232, 241–242, 261–272, 281 |
| **4** | Reference/interpretive | 250 (Miscellaneous Interpretations) |

## Corpus Statistics

| Metric | Value |
|--------|-------|
| Total eCFR documents | 59 |
| Total Federal Register documents | 2,431 |
| Total sections (eCFR) | 1,449 |
| Total words (eCFR) | 1,114,528 |
| eCFR date | 2026-03-24 |
| Federal Register coverage | All rules/proposed rules + notices 2023–present |

## Sources

- eCFR API: https://www.ecfr.gov/api/versioner/v1/
- Federal Register API: https://www.federalregister.gov/api/v1/
- 12 CFR Chapter II: https://www.ecfr.gov/current/title-12/chapter-II

## Related Documentation

See the `docs/` folder for additional reference materials, including the NOVA Corpus Guide and Metadata Build Specification.
