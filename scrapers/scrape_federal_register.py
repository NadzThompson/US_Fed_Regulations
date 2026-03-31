"""
Federal Register Scraper — Fetches Fed-issued rules, proposed rules, and notices
from the Federal Register API.

Produces:
  federal_register/json/  — NOVA metadata per document
  federal_register/md/    — Document summaries with abstracts
  federal_register/html/  — Formatted HTML with metadata tags

Usage:
    python -m scrapers.scrape_federal_register                          # All types
    python -m scrapers.scrape_federal_register --types final_rules      # Just final rules
    python -m scrapers.scrape_federal_register --after 2023-01-01       # Only after date

API docs: https://www.federalregister.gov/developers/documentation/api/v1
"""

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

import requests

from scrapers.config import (
    FR_API_BASE,
    FR_CONDITIONS,
    FR_DELAY_SECONDS,
    FR_DIR,
    PARSER_VERSIONS,
    ENRICHER_VERSIONS,
    SCRAPE_DATE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NOVA-FedRegister-Scraper/1.0 (regulatory research)",
    "Accept": "application/json",
})

# Fields to request from the FR API
FR_FIELDS = [
    "abstract",
    "action",
    "agencies",
    "body_html_url",
    "cfr_references",
    "citation",
    "document_number",
    "docket_ids",
    "effective_on",
    "html_url",
    "page_length",
    "pdf_url",
    "publication_date",
    "regulation_id_numbers",
    "title",
    "type",
]


# ─── API fetching ─────────────────────────────────────────────────────────────

def fetch_documents(doc_type: str, after_date: str | None = None) -> list[dict]:
    """
    Paginate through all Federal Register documents matching the given type
    for the Federal Reserve System agency.

    Args:
        doc_type: One of 'final_rules', 'proposed_rules', 'notices'
        after_date: Optional ISO date string; only return docs published after this date

    Returns:
        List of document dicts from the FR API
    """
    condition = FR_CONDITIONS[doc_type]
    all_docs = []
    page = 1

    while True:
        params = {
            "per_page": 1000,
            "page": page,
            "order": "oldest",
            "fields[]": FR_FIELDS,
            **{f"conditions[{k}]": v for k, v in condition.items()},
        }
        if after_date:
            params["conditions[publication_date][gte]"] = after_date

        log.info("Fetching FR %s page %d ...", doc_type, page)
        resp = SESSION.get(f"{FR_API_BASE}/documents.json", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        all_docs.extend(results)
        log.info("  → got %d docs (total so far: %d)", len(results), len(all_docs))

        # Check for next page
        next_url = data.get("next_page_url")
        if not next_url:
            break
        page += 1
        time.sleep(FR_DELAY_SECONDS)

    return all_docs


# ─── Processing a single document ────────────────────────────────────────────

def process_document(doc: dict) -> dict:
    """
    Transform a raw FR API document into NOVA output files (JSON, MD, HTML).
    Returns the metadata dict.
    """
    doc_num = doc["document_number"]
    title = doc.get("title", "Untitled")
    doc_type = doc.get("type", "Notice")
    pub_date = doc.get("publication_date", "")
    citation = doc.get("citation", "")
    abstract = doc.get("abstract", "") or ""
    action = doc.get("action", "") or ""
    html_url = doc.get("html_url", "")
    pdf_url = doc.get("pdf_url", "")
    effective_on = doc.get("effective_on")
    page_length = doc.get("page_length", 0)
    cfr_refs = doc.get("cfr_references", []) or []
    docket_ids = doc.get("docket_ids", []) or []
    reg_id_nums = doc.get("regulation_id_numbers", []) or []

    filename_stem = build_filename(doc_num, title)

    # Determine NOVA type mapping
    type_map = {
        "Rule": "Final Rule",
        "Proposed Rule": "Proposed Rule",
        "Notice": "Notice",
    }
    pub_type_normalized = type_map.get(doc_type, doc_type)

    # NOVA tier for FR docs: rules=2, proposed=2, notices=3
    tier = 3 if doc_type == "Notice" else 2

    # Authority class
    if doc_type == "Rule":
        authority_class = "primary_normative"
        authority_level = "binding_regulation"
    elif doc_type == "Proposed Rule":
        authority_class = "guidance_interpretive"
        authority_level = "informational"
    else:
        authority_class = "guidance_interpretive"
        authority_level = "informational"

    # Generate MD content
    md_content = generate_markdown(doc_num, title, doc_type, citation, pub_date,
                                   action, abstract, html_url, pdf_url)
    word_count = len(md_content.split())
    text_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()

    # Generate HTML
    html_content = generate_html(doc_num, title, doc_type, citation, pub_date,
                                  action, abstract, html_url, pdf_url)

    # Cross-references from CFR refs
    cross_refs = []
    for ref in cfr_refs:
        if ref.get("part") and ref.get("title"):
            cross_refs.append(str(ref))

    # Applicability (heuristic from CFR refs)
    applies_insured_depository = any(
        ref.get("title") == 12 for ref in cfr_refs
    )

    # BM25 and vector text
    bm25 = f"{doc_num} {title} {action} {abstract[:200]}"
    vector_prefix = f"Federal Reserve {pub_type_normalized} {doc_num}: {title}"

    # ── NOVA Layer 1: Embedding structural fields ───────────────────────
    heading_path = ["Federal Reserve System", "Federal Register", pub_type_normalized, doc_num]
    structural_level = "document"
    normative_weight = "mandatory" if doc_type == "Rule" else "informational"
    paragraph_role = "scope_statement" if doc_type == "Rule" else "rationale"

    # ── NOVA Layer 2: Content flags ───────────────────────────────────────
    scan_text = (abstract or "") + " " + (action or "")
    contains_definition = bool(re.search(r"(?:defines|definition of|means)", scan_text, re.IGNORECASE))
    contains_requirement = bool(re.search(r"\b(?:shall|must|required)\b", scan_text, re.IGNORECASE))
    contains_deadline = bool(re.search(r"\b(?:effective|within \d+ days|by \w+ \d{1,2})", scan_text, re.IGNORECASE))

    # ── NOVA Layer 3: Version fields ──────────────────────────────────────
    version_id = pub_date
    version_label = pub_date[:4] if pub_date else None
    doc_family_id = f"usfed.fr.{doc_num}"

    metadata = {
        # ── Identity ──────────────────────────────────────────────────────
        "doc_id": f"usfed.fr.{doc_num}",
        "doc_family_id": doc_family_id,
        "title": title,
        "short_title": doc_num,
        "slug": doc_num,
        "title_normalized": title,

        # ── Classification ────────────────────────────────────────────────
        "regulator": "Federal Reserve System",
        "regulator_acronym": "FRS",
        "jurisdiction": "United States",
        "document_class": pub_type_normalized,
        "publication_type": "Notice",
        "publication_type_normalized": pub_type_normalized,
        "authority_class": authority_class,
        "authority_level": authority_level,
        "nova_tier": tier,
        "prudential_weight": 0.7 if doc_type == "Rule" else 0.6,
        "status": "active",

        # ── Layer 1: Embedding fields ─────────────────────────────────────
        "heading_path": heading_path,
        "section_path": f"Federal Reserve System > Federal Register > {pub_type_normalized} > {doc_num}",
        "structural_level": structural_level,
        "normative_weight": normative_weight,

        # ── Layer 2: Index/filter fields ──────────────────────────────────
        "current_version_flag": True,
        "is_primary_normative": doc_type == "Rule",
        "is_supporting_interpretive": doc_type != "Rule",
        "is_context_only": False,
        "paragraph_role": paragraph_role,
        "contains_definition": contains_definition,
        "contains_formula": False,
        "contains_requirement": contains_requirement,
        "contains_deadline": contains_deadline,
        "contains_parameter": False,
        "contains_assignment": False,
        "is_appendix": False,
        "depth": 0,

        # ── Layer 3: Prompt injection fields ──────────────────────────────
        "version_id": version_id,
        "version_label": version_label,
        "citation_anchor": f"#fr-{doc_num}",

        # ── FR-specific ───────────────────────────────────────────────────
        "document_number": doc_num,
        "citation": citation,
        "publication_date": pub_date,
        "effective_date_start": effective_on,
        "effective_date_end": None,
        "abstract": abstract[:500] if abstract else None,
        "action": action,
        "html_url": html_url,
        "pdf_url": pdf_url,
        "cfr_references": cfr_refs,
        "docket_ids": docket_ids,
        "regulation_id_numbers": reg_id_nums,
        "page_length": page_length,
        "issuing_agency": "Board of Governors of the Federal Reserve System",
        "issuing_division": "Board of Governors",
        "document_date_iso": pub_date,
        "document_date_raw": pub_date,

        # ── Content metrics ───────────────────────────────────────────────
        "word_count_raw_body": word_count,
        "section_headings": [],
        "toc_depth": 0,
        "has_appendices": False,
        "has_tables": False,
        "table_count": 0,
        "has_footnotes": False,
        "footnote_count": 0,
        "inline_cross_references": cross_refs,

        # ── Corpus inclusion ──────────────────────────────────────────────
        "include_in_primary_corpus": doc_type == "Rule",
        "include_in_context_corpus": True,
        "include_in_support_corpus": doc_type != "Rule",

        # ── Applicability ─────────────────────────────────────────────────
        "applies_to_state_member_banks": False,
        "applies_to_bank_holding_companies": False,
        "applies_to_savings_loan_holding": False,
        "applies_to_foreign_banking_orgs": False,
        "applies_to_nonbank_financial": False,
        "applies_to_insured_depository": applies_insured_depository,

        # ── Versioning ────────────────────────────────────────────────────
        "superseded_by_doc_id": None,
        "supersedes_doc_id": None,

        # ── Source ────────────────────────────────────────────────────────
        "content_source": "Federal Register API",
        "source_url": html_url,
        "canonical_url": html_url,
        "scraped_on": SCRAPE_DATE,
        "enriched_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),

        # ── Layer 2: Search fields ────────────────────────────────────────
        "bm25_text": bm25[:500],
        "vector_text_prefix": vector_prefix[:200],

        # ── Layer 4: Operational / audit trail ────────────────────────────
        "normalized_text_sha256": text_hash,
        "normalized_md_path": f"federal_register/md/{filename_stem}.md",
        "canonical_json_path": f"federal_register/json/{filename_stem}.json",
        "parser_version": PARSER_VERSIONS["fr"],
        "normalizer_version": ENRICHER_VERSIONS["fr"],
        "quality_score": 100,
        "quality_flags": [],
    }

    # Write outputs
    json_dir = FR_DIR / "json"
    md_dir = FR_DIR / "md"
    html_dir = FR_DIR / "html"
    for d in [json_dir, md_dir, html_dir]:
        d.mkdir(parents=True, exist_ok=True)

    (json_dir / f"{filename_stem}.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (md_dir / f"{filename_stem}.md").write_text(md_content, encoding="utf-8")
    (html_dir / f"{filename_stem}.html").write_text(html_content, encoding="utf-8")

    return metadata


# ─── Output generation ───────────────────────────────────────────────────────

def build_filename(doc_num: str, title: str) -> str:
    """Build filename stem: e.g. '00-12337_Disclosure_and_Reporting_of_CRA-Related_Agreements'."""
    clean = re.sub(r"[^\w\s\-]", "", title)
    clean = re.sub(r"\s+", "_", clean.strip())
    # Truncate to avoid filesystem path length limits
    if len(clean) > 100:
        clean = clean[:100]
    return f"{doc_num}_{clean}"


def generate_markdown(doc_num, title, doc_type, citation, pub_date,
                      action, abstract, html_url, pdf_url) -> str:
    """Generate the markdown content file for a Federal Register document."""
    lines = [
        f"# {title}",
        "",
        f"**{doc_type}** | {citation} | Published {pub_date}",
        "",
        f"**Document Number:** {doc_num}",
        "",
        "---",
        "",
    ]
    if action:
        lines += [f"## Action", "", action, ""]
    if abstract:
        lines += [f"## Abstract", "", abstract, ""]
    lines += [
        f"**Federal Register:** [{html_url}]({html_url})",
        f"**PDF:** [{pdf_url}]({pdf_url})",
    ]
    return "\n".join(lines) + "\n"


def generate_html(doc_num, title, doc_type, citation, pub_date,
                   action, abstract, html_url, pdf_url) -> str:
    """Generate styled HTML for a Federal Register document."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="document-number" content="{html_escape(doc_num)}">
  <meta name="publication-date" content="{html_escape(pub_date)}">
  <meta name="document-type" content="{html_escape(doc_type)}">
  <title>{html_escape(title)}</title>
  <style>
    body {{ font-family: "Times New Roman", serif; max-width: 900px; margin: 2em auto; line-height: 1.6; }}
    h1 {{ font-size: 1.3em; border-bottom: 2px solid #333; }}
    .meta {{ color: #666; font-size: 0.9em; }}
    .abstract {{ margin: 1em 0; padding: 1em; background: #f9f9f9; border-left: 3px solid #666; }}
  </style>
</head>
<body>
<h1>{html_escape(title)}</h1>
<div class="meta">
  <p><strong>Type:</strong> {html_escape(doc_type)} | <strong>Citation:</strong> {html_escape(citation)}</p>
  <p><strong>Published:</strong> {html_escape(pub_date)}</p>
  <p><strong>Document Number:</strong> {html_escape(doc_num)}</p>
</div>
{"<h2>Action</h2><p>" + html_escape(action) + "</p>" if action else ""}
{"<div class='abstract'><h2>Abstract</h2><p>" + html_escape(abstract) + "</p></div>" if abstract else ""}
<p><a href="{html_escape(html_url)}">View on Federal Register</a></p>
<p><a href="{html_escape(pdf_url)}">Download PDF</a></p>
</body>
</html>"""


# ─── Main pipeline ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape Federal Register documents")
    parser.add_argument("--types", nargs="*",
                        choices=["final_rules", "proposed_rules", "notices"],
                        default=["final_rules", "proposed_rules", "notices"],
                        help="Document types to scrape")
    parser.add_argument("--after", type=str, default=None,
                        help="Only fetch documents published after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    total_docs = 0
    for doc_type in args.types:
        log.info("── Fetching %s ──", doc_type)
        after = args.after
        # For notices, default to 2023+ unless overridden
        if doc_type == "notices" and not after:
            after = "2023-01-01"

        raw_docs = fetch_documents(doc_type, after_date=after)
        log.info("Processing %d %s documents...", len(raw_docs), doc_type)

        for i, doc in enumerate(raw_docs):
            try:
                process_document(doc)
            except Exception as e:
                log.error("Error processing %s: %s", doc.get("document_number", "?"), e)
            if (i + 1) % 100 == 0:
                log.info("  processed %d/%d", i + 1, len(raw_docs))

        total_docs += len(raw_docs)

    log.info("Federal Register scrape complete: %d total documents", total_docs)


if __name__ == "__main__":
    main()
