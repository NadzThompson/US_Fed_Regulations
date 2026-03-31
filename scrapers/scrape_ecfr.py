"""
eCFR Scraper — Fetches all 12 CFR Chapter II regulation parts from the eCFR API.

Produces:
  ecfr/json/  — NOVA metadata per regulation part (no embedded content)
  ecfr/md/    — Readable markdown text (authoritative content)
  ecfr/html/  — Formatted HTML with metadata tags

Usage:
    python -m scrapers.scrape_ecfr                  # Scrape all parts
    python -m scrapers.scrape_ecfr --parts 204 217  # Scrape specific parts
    python -m scrapers.scrape_ecfr --date 2026-03-09  # Scrape as of a date

API docs: https://www.ecfr.gov/developer/documentation/api/v1
"""

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, date
from html import escape as html_escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from scrapers.config import (
    CFR_TITLE,
    ECFR_API_BASE,
    ECFR_DELAY_SECONDS,
    ECFR_DIR,
    ECFR_PARTS,
    PART_TO_REG_LETTER,
    PARSER_VERSIONS,
    ENRICHER_VERSIONS,
    SCRAPE_DATE,
    nova_tier_for_part,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NOVA-eCFR-Scraper/1.0 (regulatory research)",
    "Accept": "application/json, text/html",
})


# ─── eCFR API helpers ─────────────────────────────────────────────────────────

def fetch_structure(as_of_date: str) -> dict:
    """Fetch the table-of-contents structure for 12 CFR from the eCFR versioner API."""
    url = f"{ECFR_API_BASE}/structure/{as_of_date}/title-{CFR_TITLE}.json"
    log.info("Fetching eCFR structure for title %d as of %s", CFR_TITLE, as_of_date)
    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_part_html(as_of_date: str, part_number: int) -> str:
    """Fetch the full rendered HTML of a single CFR part from the eCFR renderer API."""
    url = (
        f"https://www.ecfr.gov/api/renderer/v1/content/enhanced/"
        f"{as_of_date}/title-{CFR_TITLE}"
    )
    params = {"part": part_number}
    log.info("Fetching eCFR Part %d HTML as of %s", part_number, as_of_date)
    resp = SESSION.get(url, params=params, timeout=120)
    resp.raise_for_status()
    return resp.text


def fetch_part_versions(part_number: int) -> list[dict]:
    """Get the amendment history for a part (used to find effective date)."""
    url = f"{ECFR_API_BASE}/versions/title-{CFR_TITLE}"
    params = {"part": part_number}
    resp = SESSION.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("content_versions", [])


# ─── Parsing ──────────────────────────────────────────────────────────────────

def extract_part_metadata_from_structure(structure: dict, part_number: int) -> dict | None:
    """Walk the eCFR structure JSON to find metadata for a specific part."""
    children = structure.get("children", [])
    for child in children:
        # Walk into chapters
        if child.get("type") == "chapter":
            for sub in child.get("children", []):
                if sub.get("type") == "part" and _part_num(sub) == part_number:
                    return sub
                # Also check subchapters
                if sub.get("type") == "subchapter":
                    for part_node in sub.get("children", []):
                        if part_node.get("type") == "part" and _part_num(part_node) == part_number:
                            return part_node
        # Direct parts
        if child.get("type") == "part" and _part_num(child) == part_number:
            return child
    return None


def _part_num(node: dict) -> int | None:
    ident = node.get("identifier", "")
    try:
        return int(ident)
    except (ValueError, TypeError):
        return None


def parse_part_html(raw_html: str, part_number: int) -> dict:
    """
    Parse the eCFR rendered HTML for a single part, extracting:
      - part_name, authority, source
      - sections (heading + body text)
      - appendices
      - tables, footnotes
    """
    soup = BeautifulSoup(raw_html, "lxml")

    # Part title
    part_heading = soup.find("div", class_="head")
    part_name = ""
    if part_heading:
        part_name = part_heading.get_text(strip=True)
    if not part_name:
        h1 = soup.find("h1")
        part_name = h1.get_text(strip=True) if h1 else f"Part {part_number}"

    # Authority and Source
    authority = ""
    source = ""
    for auth_div in soup.find_all("div", class_="authority"):
        authority = auth_div.get_text(" ", strip=True).replace("Authority:", "").strip()
    for src_div in soup.find_all("div", class_="source"):
        source = src_div.get_text(" ", strip=True).replace("Source:", "").strip()

    # Sections
    sections = []
    section_divs = soup.find_all("div", class_=re.compile(r"section"))
    for sec_div in section_divs:
        heading_el = sec_div.find(re.compile(r"h[2-6]"))
        if heading_el:
            heading = heading_el.get_text(" ", strip=True)
        else:
            heading = ""
        body = sec_div.get_text("\n", strip=True)
        sections.append({"heading": heading, "body": body})

    # Appendices
    appendices = []
    for app_div in soup.find_all("div", class_=re.compile(r"appendix")):
        app_heading = app_div.find(re.compile(r"h[2-6]"))
        appendices.append({
            "heading": app_heading.get_text(strip=True) if app_heading else "",
            "body": app_div.get_text("\n", strip=True),
        })

    # Counts
    tables = soup.find_all("table")
    footnotes = soup.find_all(class_=re.compile(r"footnote"))

    return {
        "part_name": part_name,
        "authority": authority,
        "source": source,
        "sections": sections,
        "appendices": appendices,
        "section_count": len(sections),
        "appendix_count": len(appendices),
        "has_tables": len(tables) > 0,
        "table_count": len(tables),
        "has_footnotes": len(footnotes) > 0,
        "footnote_count": len(footnotes),
        "raw_html": raw_html,
    }


# ─── Output generation ───────────────────────────────────────────────────────

def build_filename(part_number: int, part_name: str) -> str:
    """Build the canonical filename stem, e.g. 'Reg_D_Reserve_Requirements_..._12CFR204'."""
    reg_letter = PART_TO_REG_LETTER.get(part_number)
    if reg_letter:
        clean_name = re.sub(r"[^\w\s]", "", part_name)
        clean_name = re.sub(r"\s+", "_", clean_name.strip())
        # Remove leading "PART NNN" from name
        clean_name = re.sub(r"^PART_\d+_*", "", clean_name)
        # Remove trailing REGULATION X
        clean_name = re.sub(r"_*REGULATION_[A-Z]+_*$", "", clean_name)
        return f"Reg_{reg_letter}_{clean_name}_12CFR{part_number}"
    else:
        clean_name = re.sub(r"[^\w\s]", "", part_name)
        clean_name = re.sub(r"\s+", "_", clean_name.strip())
        clean_name = re.sub(r"^PART_\d+_*", "", clean_name)
        return f"12CFR{part_number}_{clean_name}"


def generate_markdown(part_number: int, parsed: dict, as_of_date: str) -> str:
    """Generate the authoritative markdown content file for a regulation part."""
    reg_letter = PART_TO_REG_LETTER.get(part_number)
    header = f"# PART {part_number} — {parsed['part_name']}\n\n"
    if reg_letter:
        header += f"**Regulation {reg_letter}** | 12 CFR Part {part_number} | Current as of {as_of_date}\n\n"
    else:
        header += f"12 CFR Part {part_number} | Current as of {as_of_date}\n\n"

    if parsed["authority"]:
        header += f"**Authority:** {parsed['authority']}\n\n"
    header += "---\n\n"

    body_parts = [header]
    for sec in parsed["sections"]:
        if sec["heading"]:
            body_parts.append(f"## {sec['heading']}\n\n")
        # Reformat the body: preserve paragraph structure with indentation
        body_text = sec["body"]
        # Remove the heading from the body if it's duplicated at the top
        if sec["heading"] and body_text.startswith(sec["heading"]):
            body_text = body_text[len(sec["heading"]):].strip()
        body_parts.append(f"  {body_text}\n\n")

    for app in parsed["appendices"]:
        body_parts.append(f"## {app['heading']}\n\n{app['body']}\n\n")

    return "".join(body_parts)


def generate_html(part_number: int, parsed: dict, as_of_date: str) -> str:
    """Generate styled HTML with metadata tags for rendering/PDF."""
    reg_letter = PART_TO_REG_LETTER.get(part_number)

    meta_tags = [
        f'  <meta charset="UTF-8">',
        f'  <meta name="cfr-title" content="{CFR_TITLE}">',
        f'  <meta name="cfr-part" content="{part_number}">',
    ]
    if reg_letter:
        meta_tags.append(f'  <meta name="regulation-letter" content="{reg_letter}">')
    meta_tags.append(f'  <meta name="ecfr-date" content="{as_of_date}">')

    title_text = f"12 CFR Part {part_number}"
    if reg_letter:
        title_text += f" - Regulation {reg_letter}: {_clean_part_name(parsed['part_name'])}"
    else:
        title_text += f" - {_clean_part_name(parsed['part_name'])}"

    css = """  <style>
    body { font-family: "Times New Roman", serif; max-width: 900px; margin: 2em auto; line-height: 1.6; color: #1a1a1a; }
    h1 { font-size: 1.4em; border-bottom: 2px solid #333; padding-bottom: 0.3em; }
    h2 { font-size: 1.2em; margin-top: 1.5em; }
    h3 { font-size: 1.1em; margin-top: 1.2em; }
    .authority, .source { font-size: 0.9em; color: #555; margin: 0.5em 0; }
    .section { margin: 1.5em 0; }
    .section-heading { font-weight: bold; margin-bottom: 0.5em; }
    .indent-0 { margin-left: 0; }
    .indent-1 { margin-left: 2em; }
    .indent-2 { margin-left: 4em; }
    .indent-3 { margin-left: 6em; }
    .indent-4 { margin-left: 8em; }
    .appendix { margin: 2em 0; border-top: 1px solid #ccc; padding-top: 1em; }
  </style>"""

    head = f"""<!DOCTYPE html>
<html lang="en">
<head>
{chr(10).join(meta_tags)}
  <title>{html_escape(title_text)}</title>
{css}
</head>"""

    body_lines = [f"<body>"]
    body_lines.append(f"  <h1>PART {part_number}\u2014{html_escape(parsed['part_name'])}</h1>")
    if parsed["authority"]:
        body_lines.append(
            f'  <p class="authority"><strong>Authority:</strong> {html_escape(parsed["authority"])}</p>'
        )

    for sec in parsed["sections"]:
        sec_id = _section_id(part_number, sec["heading"])
        body_lines.append(f'  <div class="section" id="{sec_id}">')
        if sec["heading"]:
            body_lines.append(f'    <h3 class="section-heading">{html_escape(sec["heading"])}</h3>')
        # Parse body into indented paragraphs
        body_text = sec["body"]
        if sec["heading"] and body_text.startswith(sec["heading"]):
            body_text = body_text[len(sec["heading"]):].strip()
        for para in _split_paragraphs(body_text):
            indent = _detect_indent_level(para)
            body_lines.append(
                f'    <p class="indent-{indent}">{html_escape(para.strip())}</p>'
            )
        body_lines.append("  </div>")

    for app in parsed["appendices"]:
        body_lines.append(f'  <div class="appendix">')
        body_lines.append(f"    <h2>{html_escape(app['heading'])}</h2>")
        body_lines.append(f"    <p>{html_escape(app['body'])}</p>")
        body_lines.append("  </div>")

    body_lines.append("</body>\n</html>")

    return head + "\n" + "\n".join(body_lines)


def build_metadata(
    part_number: int,
    parsed: dict,
    as_of_date: str,
    filename_stem: str,
    md_content: str,
) -> dict:
    """
    Build the full NOVA 3-layer aligned metadata JSON for a regulation part.

    Layer 1 (Embedding): doc_id, short_title, document_class, heading_path,
        section_path, regulator, structural_level, normative_weight
    Layer 2 (Index/Filter): status, jurisdiction, nova_tier, authority_class,
        effective_date_start/end, current_version_flag, version_id, version_label,
        normative_weight, structural_level, paragraph_role, contains_* flags,
        cross_references, bm25_text
    Layer 3 (Prompt Injection): title, citation_anchor, version_id, version_label,
        current_version_flag, effective_date_start/end, status, authority_class,
        nova_tier, jurisdiction, normative_weight, paragraph_role
    Layer 4 (Operational): canonical_json_path, normalized_md_path, raw_sha256,
        parser_version, normalizer_version, quality_score
    """
    reg_letter = PART_TO_REG_LETTER.get(part_number)
    tier = nova_tier_for_part(part_number)
    part_name_clean = _clean_part_name(parsed["part_name"])

    # doc_id: e.g. "usfed.regd.204.20260309.part"
    reg_slug = f"reg{reg_letter.lower()}" if reg_letter else f"part{part_number}"
    doc_id = f"usfed.{reg_slug}.{part_number}.{as_of_date.replace('-', '')}.part"
    doc_family_id = f"usfed.{reg_slug}.{part_number}"

    # Title
    if reg_letter:
        title = f"Regulation {reg_letter}: {part_name_clean}"
        short_title = f"Reg {reg_letter}"
    else:
        title = part_name_clean
        short_title = f"Part {part_number}"

    slug = f"12cfr-part-{part_number}"

    # Authority class
    if part_number in {250}:
        authority_class = "reference_interpretive"
        document_class = "interpretive_guidance"
    elif part_number >= 261:
        authority_class = "procedural_administrative"
        document_class = "rules_of_procedure"
    else:
        authority_class = "primary_normative"
        document_class = "federal_regulation"

    # Section headings from parsed sections
    section_headings = [s["heading"] for s in parsed["sections"] if s["heading"]]

    # Word count
    word_count = len(md_content.split())

    # Content hash
    text_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()

    # ── NOVA Layer 1 & 2: Structural metadata ─────────────────────────────
    # heading_path: hierarchical breadcrumb for embedding context
    heading_path = ["Federal Reserve System", "12 CFR Chapter II"]
    if reg_letter:
        heading_path.append(f"Regulation {reg_letter}")
    heading_path.append(f"Part {part_number}")

    # structural_level: document-level for whole-part files
    structural_level = "part"

    # normative_weight: regulations are mandatory; interpretive/procedural vary
    if authority_class == "primary_normative":
        normative_weight = "mandatory"
    elif authority_class == "procedural_administrative":
        normative_weight = "advisory"
    else:
        normative_weight = "informational"

    # paragraph_role: at part level, this is a scope_statement
    paragraph_role = "scope_statement"

    # contains_* content flags (scan the MD content)
    contains_definition = bool(re.search(
        r"(?:means|defined as|definition of|the term)", md_content[:10000], re.IGNORECASE
    ))
    contains_formula = bool(re.search(
        r"(?:formula|=\s*\(|calculated as|ratio\s*=)", md_content, re.IGNORECASE
    ))
    contains_requirement = bool(re.search(
        r"\b(?:shall|must|required to|is required)\b", md_content[:10000], re.IGNORECASE
    ))
    contains_deadline = bool(re.search(
        r"\b(?:within \d+ days|no later than|by \w+ \d{1,2},? \d{4})\b", md_content, re.IGNORECASE
    ))
    contains_parameter = bool(re.search(
        r"\b(?:threshold|minimum|maximum|at least|not exceed|percent|basis points)\b",
        md_content[:10000], re.IGNORECASE
    ))

    # Cross-references (Layer 2: indexed for graph navigation)
    cross_refs = _extract_ecfr_cross_references(md_content)
    if reg_letter:
        cross_refs.append(f"Regulation {reg_letter}")
    cross_refs = sorted(set(cross_refs))

    # Applicability flags (heuristic based on part scope)
    applies_smb = part_number not in {241, 242, 250}
    applies_bhc = part_number in {
        208, 217, 225, 238, 239, 243, 248, 249, 251, 252, 253
    } or part_number >= 261
    applies_slhc = part_number in {238, 239}
    applies_fbo = part_number in {211, 252}
    applies_edge = part_number in {211}
    applies_nonbank = part_number in {242, 248, 252}

    # ── NOVA Layer 2: BM25 and vector text ─────────────────────────────────
    toc_text = " ".join(section_headings[:15])
    bm25 = f"{short_title} {title} {slug.replace('-', ' ')} {toc_text}"
    vector_prefix = f"US Federal Reserve 12 CFR Part {part_number} {short_title}: {title}"

    # Prudential weight
    weight_map = {1: 0.9, 2: 0.7, 3: 0.4, 4: 0.2}
    prudential_weight = weight_map.get(tier, 0.5)

    # ── NOVA Layer 3: Version fields for prompt injection ──────────────────
    version_id = as_of_date
    version_label = as_of_date[:4]  # e.g. "2026"

    return {
        # ── Identity ──────────────────────────────────────────────────────
        "doc_id": doc_id,
        "doc_family_id": doc_family_id,
        "title": title,
        "short_title": short_title,
        "slug": slug,
        "title_normalized": part_name_clean,

        # ── Classification ────────────────────────────────────────────────
        "regulator": "Federal Reserve System",
        "regulator_acronym": "FRS",
        "jurisdiction": "United States",
        "document_class": document_class,
        "publication_type": "eCFR Regulation",
        "publication_type_normalized": "ecfr_regulation",
        "authority_class": authority_class,
        "authority_level": "binding_regulation",
        "nova_tier": tier,
        "prudential_weight": prudential_weight,
        "status": "active",

        # ── Layer 1: Embedding fields ─────────────────────────────────────
        "heading_path": heading_path,
        "section_path": f"Federal Reserve System > 12 CFR Chapter II > "
                        + (f"Regulation {reg_letter} > " if reg_letter else "")
                        + f"Part {part_number}",
        "structural_level": structural_level,
        "normative_weight": normative_weight,

        # ── Layer 2: Index/filter fields ──────────────────────────────────
        "current_version_flag": True,
        "is_primary_normative": authority_class == "primary_normative",
        "is_supporting_interpretive": authority_class == "reference_interpretive",
        "is_context_only": False,
        "paragraph_role": paragraph_role,
        "contains_definition": contains_definition,
        "contains_formula": contains_formula,
        "contains_requirement": contains_requirement,
        "contains_deadline": contains_deadline,
        "contains_parameter": contains_parameter,
        "contains_assignment": False,
        "is_appendix": False,
        "depth": 1,

        # ── Layer 3: Prompt injection fields ──────────────────────────────
        "version_id": version_id,
        "version_label": version_label,
        "citation_anchor": f"#12cfr{part_number}",

        # ── Hierarchy ─────────────────────────────────────────────────────
        "title_number": CFR_TITLE,
        "title_name": "Banks and Banking",
        "chapter": "II",
        "chapter_name": "Federal Reserve System",
        "part_number": part_number,
        "part_name": parsed["part_name"],
        "regulation_letter": reg_letter,
        "regulation_name": part_name_clean,
        "cfr_citation": f"12 CFR Part {part_number}",
        "authority": parsed["authority"],
        "source": parsed["source"],
        "issuing_agency": "Board of Governors of the Federal Reserve System",
        "issuing_division": "Board of Governors",

        # ── Temporal ──────────────────────────────────────────────────────
        # ecfr_current_as_of = the eCFR snapshot date used for scraping
        # effective_date_start = latest amendment date (from versions API)
        # These are different: as_of is when we scraped, effective is when
        # the current version of the regulation came into force
        "ecfr_current_as_of": as_of_date,
        "effective_date_start": None,  # Populated by enrich_metadata from versions API
        "effective_date_end": None,
        "original_effective_date": None,  # Populated by enrich_metadata
        "scraped_on": SCRAPE_DATE,
        "enriched_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),

        # ── Versioning ────────────────────────────────────────────────────
        "superseded_by_doc_id": None,
        "supersedes_doc_id": None,

        # ── Content metrics ───────────────────────────────────────────────
        "section_count": parsed["section_count"],
        "appendix_count": parsed["appendix_count"],
        "word_count_raw_body": word_count,
        "section_headings": section_headings,
        "toc_depth": 1,
        "has_appendices": parsed["appendix_count"] > 0,
        "has_tables": parsed["has_tables"],
        "table_count": parsed["table_count"],
        "has_footnotes": parsed["has_footnotes"],
        "footnote_count": parsed["footnote_count"],
        "inline_cross_references": cross_refs,

        # ── Corpus inclusion ──────────────────────────────────────────────
        "include_in_primary_corpus": tier <= 2,
        "include_in_context_corpus": True,
        "include_in_support_corpus": tier >= 4,

        # ── Applicability ─────────────────────────────────────────────────
        "applies_to_state_member_banks": applies_smb,
        "applies_to_bank_holding_companies": applies_bhc,
        "applies_to_savings_loan_holding": applies_slhc,
        "applies_to_foreign_banking_orgs": applies_fbo,
        "applies_to_edge_agreement_corps": applies_edge,
        "applies_to_nonbank_financial": applies_nonbank,

        # ── Source ────────────────────────────────────────────────────────
        "content_source": "eCFR API",
        "source_url": f"https://www.ecfr.gov/current/title-{CFR_TITLE}/chapter-II/part-{part_number}",
        "canonical_url": f"https://www.ecfr.gov/current/title-{CFR_TITLE}/chapter-II/part-{part_number}",

        # ── Layer 2: Search fields ────────────────────────────────────────
        "bm25_text": bm25[:500],
        "vector_text_prefix": vector_prefix[:200],

        # ── Layer 4: Operational / audit trail ────────────────────────────
        "normalized_text_sha256": text_hash,
        "normalized_md_path": f"ecfr/md/{filename_stem}.md",
        "canonical_json_path": f"ecfr/json/{filename_stem}.json",
        "parser_version": PARSER_VERSIONS["ecfr"],
        "normalizer_version": ENRICHER_VERSIONS["ecfr"],
        "quality_score": 100,
        "quality_flags": [],
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_ecfr_cross_references(text: str) -> list[str]:
    """Extract regulatory cross-references from regulation text."""
    refs = set()
    # CFR references: "12 CFR 204", "12 CFR Part 217"
    for m in re.finditer(r"\d+\s*CFR\s*(?:Part\s*)?\d+", text[:20000]):
        refs.add(m.group(0).strip())
    # Section cross-refs: "§ 204.2"
    for m in re.finditer(r"§\s*\d+\.\d+", text[:20000]):
        refs.add(m.group(0).strip())
    # Regulation letter references: "Regulation D", "Regulation YY"
    for m in re.finditer(r"Regulation\s+[A-Z]{1,2}\b", text[:20000]):
        refs.add(m.group(0).strip())
    return sorted(refs)


def _clean_part_name(raw_name: str) -> str:
    """Remove 'PART NNN —' prefix and trailing '(REGULATION X)' from part name."""
    name = re.sub(r"^PART\s+\d+\s*[—\-–]\s*", "", raw_name)
    # Extract regulation name from parenthetical if present
    paren_match = re.search(r"\(REGULATION\s+[A-Z]+\)", name)
    if paren_match:
        name = name[:paren_match.start()].strip()
    return name


def _section_id(part_number: int, heading: str) -> str:
    """Generate an HTML id from a section heading, e.g. '12-CFR-204.1'."""
    sec_match = re.search(r"§\s*(\d+\.\d+)", heading)
    if sec_match:
        return f"12-CFR-{sec_match.group(1)}"
    return f"12-CFR-{part_number}-section"


def _split_paragraphs(text: str) -> list[str]:
    """Split body text into paragraphs on newlines, filtering empties."""
    return [p for p in text.split("\n") if p.strip()]


def _detect_indent_level(text: str) -> int:
    """Detect CFR indent level from paragraph marker pattern: (a), (1), (i), (A), etc."""
    stripped = text.strip()
    if re.match(r"^\(\s*[a-z]\s*\)", stripped):
        return 1
    if re.match(r"^\(\s*\d+\s*\)", stripped):
        return 2
    if re.match(r"^\(\s*[ivxlc]+\s*\)", stripped):
        return 3
    if re.match(r"^\(\s*[A-Z]\s*\)", stripped):
        return 4
    return 0


# ─── Main pipeline ────────────────────────────────────────────────────────────

def scrape_part(part_number: int, as_of_date: str) -> dict:
    """Scrape a single eCFR part: fetch HTML, parse, generate all output formats."""
    raw_html = fetch_part_html(as_of_date, part_number)
    parsed = parse_part_html(raw_html, part_number)

    filename_stem = build_filename(part_number, parsed["part_name"])
    md_content = generate_markdown(part_number, parsed, as_of_date)
    html_content = generate_html(part_number, parsed, as_of_date)
    metadata = build_metadata(part_number, parsed, as_of_date, filename_stem, md_content)

    # Write outputs
    json_dir = ECFR_DIR / "json"
    md_dir = ECFR_DIR / "md"
    html_dir = ECFR_DIR / "html"
    for d in [json_dir, md_dir, html_dir]:
        d.mkdir(parents=True, exist_ok=True)

    json_path = json_dir / f"{filename_stem}.json"
    md_path = md_dir / f"{filename_stem}.md"
    html_path = html_dir / f"{filename_stem}.html"

    json_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(md_content, encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")

    log.info(
        "Part %d (%s): %d sections, %d words → %s",
        part_number,
        metadata["short_title"],
        parsed["section_count"],
        metadata["word_count_raw_body"],
        filename_stem,
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Scrape eCFR regulations (12 CFR Chapter II)")
    parser.add_argument("--parts", nargs="*", type=int, default=None,
                        help="Specific part numbers to scrape (default: all)")
    parser.add_argument("--date", type=str, default=None,
                        help="eCFR 'as of' date in YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    as_of_date = args.date or date.today().isoformat()
    parts_to_scrape = args.parts or ECFR_PARTS

    log.info("eCFR scrape starting: %d parts as of %s", len(parts_to_scrape), as_of_date)

    results = []
    for part_num in parts_to_scrape:
        try:
            meta = scrape_part(part_num, as_of_date)
            results.append(meta)
        except requests.HTTPError as e:
            log.error("HTTP error scraping Part %d: %s", part_num, e)
        except Exception as e:
            log.error("Error scraping Part %d: %s", part_num, e, exc_info=True)
        time.sleep(ECFR_DELAY_SECONDS)

    log.info(
        "eCFR scrape complete: %d/%d parts scraped, %d total sections, %d total words",
        len(results),
        len(parts_to_scrape),
        sum(r["section_count"] for r in results),
        sum(r["word_count_raw_body"] for r in results),
    )


if __name__ == "__main__":
    main()
