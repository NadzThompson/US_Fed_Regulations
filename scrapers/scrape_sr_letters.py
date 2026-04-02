"""
SR Letters Scraper v2 — Fetches Supervision and Regulation (SR) Letters from the
Federal Reserve Board website.

Crawl strategy:
  1. Fetch the all-years index (sr-letters-all-years.htm) to get year page URLs
  2. For each year page, extract links to individual SR letter pages
  3. Fetch each SR letter page, preserving the raw HTML
  4. Download all PDF attachments linked from each letter page
  5. Generate NOVA JSON and markdown summary outputs

Produces:
  SR_Letters/raw_html/  — Original HTML from the Federal Reserve website
  SR_Letters/pdf/       — Downloaded PDF files (letter + attachments)
  SR_Letters/json/      — NOVA metadata per SR letter
  SR_Letters/md/        — Readable markdown content

Usage:
    python -m scrapers.scrape_sr_letters                       # All years (1990-present)
    python -m scrapers.scrape_sr_letters --years 2024 2025     # Specific years
    python -m scrapers.scrape_sr_letters --letters sr25-6      # Specific letters

Source: https://www.federalreserve.gov/supervisionreg/srletters/sr-letters-all-years.htm
"""

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.config import (
    SR_DIR,
    SR_DELAY_SECONDS,
    SR_ALL_YEARS_URL,
    SR_LETTER_BASE,
    PARSER_VERSIONS,
    ENRICHER_VERSIONS,
    SCRAPE_DATE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NOVA-SR-Scraper/2.0 (regulatory research)",
    "Accept": "text/html",
})


# ─── Step 1: Discover year page URLs ────────────────────────────────────────

def fetch_year_urls() -> list[str]:
    """
    Fetch the all-years index page and extract URLs for each year page.
    Returns list of absolute URLs like:
      https://www.federalreserve.gov/supervisionreg/srletters/2024.htm
    """
    log.info("Fetching all-years index from %s", SR_ALL_YEARS_URL)
    resp = SESSION.get(SR_ALL_YEARS_URL, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    year_urls = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Match year page links: /supervisionreg/srletters/YYYY.htm
        if re.search(r"/supervisionreg/srletters/\d{4}\.htm$", href):
            full_url = urljoin(SR_ALL_YEARS_URL, href)
            if full_url not in year_urls:
                year_urls.append(full_url)

    # Sort chronologically
    year_urls.sort(key=lambda u: re.search(r"(\d{4})\.htm$", u).group(1))
    log.info("Found %d year pages (earliest: %s, latest: %s)",
             len(year_urls),
             year_urls[0].split("/")[-1] if year_urls else "?",
             year_urls[-1].split("/")[-1] if year_urls else "?")
    return year_urls


# ─── Step 2: Parse year pages to get individual SR letter links ─────────────

def fetch_letters_from_year_page(year_url: str) -> list[dict]:
    """
    Fetch a single year page and extract all SR letter entries.
    Returns list of dicts: {sr_number, title, url, topic_code, joint_letters}
    """
    year_match = re.search(r"(\d{4})\.htm$", year_url)
    year = int(year_match.group(1)) if year_match else 0
    log.info("Fetching year page: %s", year_url)

    resp = SESSION.get(year_url, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    letters = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)

        # Match SR letter links (both modern and legacy URL patterns)
        # Modern: /supervisionreg/srletters/SR2506.htm
        # Legacy: /boarddocs/srletters/1996/sr9639.htm
        if not re.search(r"SR\d{2,4}", href, re.IGNORECASE):
            continue
        # Skip if this is just a year page or the all-years page
        if re.search(r"/\d{4}\.htm$", href):
            continue

        sr_number = _extract_sr_number(href, text)
        if not sr_number:
            continue

        full_url = urljoin(year_url, href)

        # Extract topic code from text: "SR 96-39 (APP)" -> "APP"
        topic_code = ""
        topic_match = re.search(r"\(([A-Z][A-Z.]+)\)", text)
        if topic_match:
            topic_code = topic_match.group(1)

        # Extract joint letter info: "SR 24-8 / CA 24-6"
        joint_letters = []
        joint_match = re.findall(r"(CA\s*\d{2}-\d+)", text, re.IGNORECASE)
        for jm in joint_match:
            joint_letters.append(jm.strip())

        # Extract title from sibling text or parent element
        title = _extract_title_from_context(link)

        letters.append({
            "sr_number": sr_number,
            "url": full_url,
            "title": title,
            "topic_code": topic_code,
            "joint_letters": joint_letters,
            "year": year,
        })

    # Deduplicate by sr_number
    seen = set()
    unique = []
    for letter in letters:
        if letter["sr_number"] not in seen:
            seen.add(letter["sr_number"])
            unique.append(letter)

    log.info("  Year %d: found %d SR letters", year, len(unique))
    return unique


def _extract_title_from_context(link_tag) -> str:
    """Extract the SR letter title from the context surrounding the link."""
    # The title is usually the text right after the SR number link,
    # either as a sibling text node or in the same parent element
    text = link_tag.get_text(strip=True)

    # Check if next sibling has the title
    parent = link_tag.parent
    if parent:
        full_text = parent.get_text(" ", strip=True)
        # Remove the SR number prefix to get the title
        title = re.sub(r"^SR\s*\d{2}-\d+\s*(?:/\s*CA\s*\d{2}-\d+\s*)?(?:\([A-Z.]+\)\s*)?[-–—]?\s*",
                        "", full_text, flags=re.IGNORECASE).strip()
        if title and len(title) > 5:
            return title

    return text


def _extract_sr_number(href: str, text: str) -> str | None:
    """
    Extract normalized SR letter number from a link.
    E.g. 'SR2506.htm' -> 'SR 25-6', 'sr9639.htm' -> 'SR 96-39'
    """
    # Try extracting from href filename: SR2506.htm, sr9639.htm
    href_match = re.search(r"SR(\d{2})(\d+)\.htm", href, re.IGNORECASE)
    if href_match:
        year_part = href_match.group(1)
        seq_part = str(int(href_match.group(2)))  # Remove leading zeros
        return f"SR {year_part}-{seq_part}"

    # Try from text: "SR 25-6", "SR 96-39"
    text_match = re.match(r"SR\s*(\d{2})-(\d+)", text, re.IGNORECASE)
    if text_match:
        return f"SR {text_match.group(1)}-{text_match.group(2)}"

    return None


# ─── Step 3: Fetch individual SR letter pages ──────────────────────────────

def fetch_sr_letter_page(sr_info: dict) -> dict | None:
    """
    Fetch an individual SR letter page and extract all content and metadata.

    Returns dict with:
      raw_html, title, date_iso, applicability, subject, body_text,
      pdf_links, supersedes, related_letters, attachments
    """
    url = sr_info["url"]
    log.info("Fetching %s from %s", sr_info["sr_number"], url)

    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    raw_html = resp.text
    soup = BeautifulSoup(raw_html, "lxml")

    # ── Title ────────────────────────────────────────────────────────────
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    # Clean common prefixes
    title = re.sub(r"^(?:FRB:\s*|The Fed\s*-\s*)", "", title, flags=re.IGNORECASE).strip()
    if not title:
        h1 = soup.find("h1") or soup.find("h3")
        title = h1.get_text(strip=True) if h1 else sr_info["title"]

    # ── Date ─────────────────────────────────────────────────────────────
    date_iso = _parse_date_from_page(raw_html, sr_info)

    # ── Applicability ────────────────────────────────────────────────────
    applicability = ""
    app_match = re.search(
        r"Applicabil(?:ity|e\s+to)[:\s]+(.+?)(?=<br|<p|\n\n|SUBJECT|Dear)",
        raw_html, re.IGNORECASE | re.DOTALL
    )
    if app_match:
        applicability = BeautifulSoup(app_match.group(1), "lxml").get_text(strip=True)

    # ── Body content ─────────────────────────────────────────────────────
    content_div = (
        soup.find("div", {"id": "content"})
        or soup.find("div", class_="col-xs-12")
        or soup.find("div", class_="content")
        or soup.find("body")
    )
    body_text = content_div.get_text("\n", strip=True) if content_div else soup.get_text("\n", strip=True)

    # ── PDF links ────────────────────────────────────────────────────────
    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            pdf_url = urljoin(url, href)
            pdf_name = a.get_text(strip=True) or href.split("/")[-1]
            pdf_links.append({"url": pdf_url, "name": pdf_name, "filename": href.split("/")[-1]})

    # Deduplicate PDF links by URL
    seen_pdfs = set()
    unique_pdfs = []
    for pdf in pdf_links:
        if pdf["url"] not in seen_pdfs:
            seen_pdfs.add(pdf["url"])
            unique_pdfs.append(pdf)
    pdf_links = unique_pdfs

    # ── Supersedes / related letters ─────────────────────────────────────
    supersedes = []
    related = []
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True)
        href = a["href"]
        if re.search(r"SR\s*\d{2}-\d+", link_text, re.IGNORECASE) and href != url:
            ref_num = re.search(r"SR\s*\d{2}-\d+", link_text, re.IGNORECASE).group(0)
            # Check context for "supersedes" language
            context = ""
            parent = a.parent
            if parent:
                context = parent.get_text(" ", strip=True).lower()
            if any(w in context for w in ["supersed", "replac", "rescind"]):
                supersedes.append(ref_num)
            else:
                related.append(ref_num)

    # ── Section headings ─────────────────────────────────────────────────
    headings = []
    for h in soup.find_all(re.compile(r"h[1-6]")):
        headings.append(h.get_text(strip=True))
    for strong in soup.find_all(["strong", "b"]):
        text = strong.get_text(strip=True)
        if 5 < len(text) < 200 and text.endswith(":"):
            headings.append(text)

    # ── Cross-references ─────────────────────────────────────────────────
    cross_refs = _extract_cross_references(body_text)

    # ── Appendices ───────────────────────────────────────────────────────
    has_appendices = bool(re.search(
        r"(?:Attachment|Appendix|Enclosure)\s*[A-Z0-9]", body_text, re.IGNORECASE
    ))

    return {
        "sr_number": sr_info["sr_number"],
        "url": url,
        "raw_html": raw_html,
        "title": title,
        "date_iso": date_iso,
        "applicability": applicability,
        "topic_code": sr_info.get("topic_code", ""),
        "joint_letters": sr_info.get("joint_letters", []),
        "year": sr_info.get("year"),
        "body_text": body_text,
        "headings": headings,
        "has_appendices": has_appendices,
        "cross_references": cross_refs,
        "pdf_links": pdf_links,
        "supersedes": supersedes,
        "related_letters": related,
        "word_count": len(body_text.split()),
    }


def _parse_date_from_page(html_text: str, sr_info: dict) -> str:
    """Extract the publication date from an SR letter page."""
    # Extract just the content area to avoid matching dates in JS/nav
    soup = BeautifulSoup(html_text, "lxml")
    content_div = (
        soup.find("div", {"id": "content"})
        or soup.find("div", class_="col-xs-12")
        or soup.find("body")
    )
    search_text = content_div.get_text(" ", strip=True) if content_div else html_text

    date_patterns = [
        (r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})",
         ["%B %d, %Y", "%B %d %Y"]),
        (r"(\d{1,2}/\d{1,2}/\d{4})", ["%m/%d/%Y"]),
        (r"(\d{4}-\d{2}-\d{2})", ["%Y-%m-%d"]),
    ]
    for pattern, fmts in date_patterns:
        match = re.search(pattern, search_text)
        if match:
            raw_date = match.group(1)
            for fmt in fmts:
                try:
                    dt = datetime.strptime(raw_date, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue

    # Fallback: derive from SR number
    sr_num = sr_info["sr_number"]
    year_match = re.search(r"SR\s*(\d{2})-", sr_num, re.IGNORECASE)
    if year_match:
        year = int(year_match.group(1))
        full_year = 2000 + year if year < 90 else 1900 + year
        return f"{full_year}-01-01"

    return SCRAPE_DATE


def _extract_cross_references(text: str) -> list[str]:
    """Extract regulatory cross-references from body text."""
    refs = set()
    for m in re.finditer(r"\d+\s*CFR\s*(?:Part\s*)?\d+", text):
        refs.add(m.group(0).strip())
    for m in re.finditer(r"SR\s*\d{2}-\d+", text, re.IGNORECASE):
        refs.add(m.group(0).strip())
    for m in re.finditer(r"Regulation\s+[A-Z]{1,2}\b", text):
        refs.add(m.group(0).strip())
    return sorted(refs)


# ─── Step 4: Download PDF attachments ───────────────────────────────────────

def build_pdf_filename(sr_number: str, pdf_info: dict, index: int) -> str:
    """
    Build a clean, descriptive PDF filename.

    Examples:
      SR 11-7 main letter  → 'SR 11-7 - Guidance on Model Risk Management.pdf'
      SR 11-7 attachment 1  → 'SR 11-7 - Attachment 1 - Model Risk Management Guidance.pdf'
    """
    from urllib.parse import unquote

    original = unquote(pdf_info["filename"])
    description = pdf_info.get("name", "").strip()

    # Clean up generic descriptions
    generic_names = {"pdf", "pdf version", "letter", "attachment", "attachment.pdf",
                     "", "click here", "full text"}
    desc_lower = description.lower().rstrip(".")
    # Strip "(PDF)" suffix
    description = re.sub(r"\s*\(PDF\)\s*$", "", description, flags=re.IGNORECASE).strip()

    if desc_lower in generic_names or not description:
        # Try to derive a name from the original filename
        name_part = re.sub(r"\.(pdf|htm)$", "", original, flags=re.IGNORECASE)
        name_part = re.sub(r"[%_]", " ", name_part).strip()
        if name_part and name_part.lower() not in generic_names:
            description = name_part
        else:
            description = "Document"

    # Clean up the description for use as a filename
    description = re.sub(r"[<>:\"/\\|?*]", "", description)
    description = re.sub(r"\s+", " ", description).strip()

    # Truncate if too long
    if len(description) > 120:
        description = description[:120].rsplit(" ", 1)[0]

    # Determine if this is the main letter PDF or an attachment
    orig_lower = original.lower()
    # Extract year and sequence from SR number: "SR 11-7" -> ("11", "7")
    sr_match = re.match(r"SR\s*(\d{2})-(\d+)", sr_number, re.IGNORECASE)
    is_main = False
    if sr_match:
        yr, seq = sr_match.group(1), sr_match.group(2)
        # Match both padded and unpadded: sr1107.pdf, sr117.pdf, SR2506.pdf
        padded = f"sr{yr}{seq.zfill(2)}"
        unpadded = f"sr{yr}{seq}"
        is_main = orig_lower in (f"{padded}.pdf", f"{unpadded}.pdf")

    if is_main:
        # For the main letter PDF, use "Letter" as description if it's just the filename
        if description.lower().startswith("sr") and re.match(r"^sr\d+$", description.lower()):
            description = "Letter"
        return f"{sr_number} - {description}.pdf"
    else:
        return f"{sr_number} - Attachment {index} - {description}.pdf"


def download_pdf(pdf_url: str, pdf_path: Path) -> bool:
    """Download a single PDF file."""
    try:
        resp = SESSION.get(
            pdf_url, timeout=120, stream=True,
            headers={"Accept": "application/pdf, */*"},
        )
        resp.raise_for_status()
        with open(pdf_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info("  Downloaded PDF: %s (%d bytes)", pdf_path.name, pdf_path.stat().st_size)
        return True
    except Exception as e:
        log.error("  Failed to download PDF %s: %s", pdf_url, e)
        return False


# ─── Step 5: Build outputs ──────────────────────────────────────────────────

def build_sr_filename(sr_number: str, title: str, topic_code: str) -> str:
    """
    Build the canonical filename stem.
    E.g. 'SR_25-6_SUP_Status_of_Certain_Investment_Funds'
    """
    # Normalize SR number: "SR 25-6" -> "SR_25-6"
    num_part = sr_number.replace(" ", "_")

    # Add topic code if present
    topic_part = f"_{topic_code}" if topic_code else ""

    # Clean and truncate title
    clean_title = re.sub(r"^SR\s*\d{2}-\d+[^:]*:\s*", "", title, flags=re.IGNORECASE)
    clean_title = re.sub(r"[^\w\s]", "", clean_title)
    clean_title = re.sub(r"\s+", "_", clean_title.strip())
    # Title case the first few words
    words = clean_title.split("_")[:10]
    clean_title = "_".join(w.capitalize() if w.islower() else w for w in words)

    if len(clean_title) > 80:
        clean_title = clean_title[:80]

    return f"{num_part}{topic_part}_{clean_title}"


def build_metadata(parsed: dict, filename_stem: str) -> dict:
    """Build full NOVA 3-layer aligned metadata JSON for an SR letter."""
    sr_num = parsed["sr_number"]

    # Parse year and sequence
    yr_match = re.match(r"SR\s*(\d{2})-(\d+)", sr_num, re.IGNORECASE)
    sr_year = None
    sr_seq = None
    if yr_match:
        yr = int(yr_match.group(1))
        sr_year = 2000 + yr if yr < 90 else 1900 + yr
        sr_seq = int(yr_match.group(2))

    doc_id = f"usfed.{sr_num.lower().replace(' ', '')}.{parsed['date_iso'].replace('-', '')}.letter"
    title = parsed["title"]
    title_normalized = re.sub(r"^SR\s*\d{2}-\d+[^:]*:\s*", "", title, flags=re.IGNORECASE)

    text_hash = hashlib.sha256(parsed["body_text"].encode("utf-8")).hexdigest()

    # Applicability flags (heuristic from content)
    app_text = (parsed.get("applicability", "") + " " + parsed["body_text"][:10000]).lower()
    applies_smb = "state member bank" in app_text or "member bank" in app_text
    applies_bhc = "bank holding compan" in app_text or "holding compan" in app_text
    applies_slhc = "savings and loan" in app_text or "thrift" in app_text
    applies_fbo = "foreign bank" in app_text
    applies_nonbank = "nonbank" in app_text or "systemically important" in app_text
    applies_large = "large" in app_text and ("institution" in app_text or "organization" in app_text)

    # Normative weight
    body_lower = parsed["body_text"].lower()
    if any(w in body_lower for w in ["must", "shall", "required to"]):
        normative_weight = "mandatory"
    elif any(w in body_lower for w in ["should", "expected to", "is expected"]):
        normative_weight = "advisory"
    else:
        normative_weight = "informational"

    # Content flags
    contains_definition = bool(re.search(r"(?:means|defined as|definition of|the term)", body_lower))
    contains_requirement = bool(re.search(r"\b(?:shall|must|required to|is required)\b", body_lower))
    contains_deadline = bool(re.search(
        r"\b(?:within \d+ days|no later than|by \w+ \d{1,2}|effective immediately)\b", body_lower
    ))
    contains_parameter = bool(re.search(
        r"\b(?:threshold|minimum|maximum|at least|not exceed|percent|basis points)\b", body_lower
    ))

    # PDF attachment info — full details with source URLs and local paths
    pdf_attachments_detail = []
    for p in parsed.get("pdf_links", []):
        clean_name = p.get("clean_filename", p["filename"])
        pdf_attachments_detail.append({
            "filename": clean_name,
            "original_filename": p["filename"],
            "source_url": p["url"],
            "local_path": f"SR_Letters/pdf/{clean_name}",
            "description": p["name"],
        })
    pdf_filenames = [p.get("clean_filename", p["filename"]) for p in parsed.get("pdf_links", [])]

    doc_family_id = f"usfed.{sr_num.lower().replace(' ', '')}"
    version_id = parsed["date_iso"]
    version_label = str(sr_year) if sr_year else parsed["date_iso"][:4]

    bm25 = (f"{sr_num} {title_normalized} {parsed.get('applicability', '')} "
            + " ".join(parsed["headings"][:10]))[:500]

    return {
        # Identity
        "doc_id": doc_id,
        "doc_family_id": doc_family_id,
        "title": title,
        "title_normalized": title_normalized,
        "short_title": sr_num,
        "slug": f"sr-letter-{sr_num.lower().replace(' ', '')}",

        # Classification
        "regulator": "Federal Reserve System",
        "regulator_acronym": "FRS",
        "jurisdiction": "United States",
        "document_class": "sr_letter",
        "publication_type": "SR Letter",
        "publication_type_normalized": "sr_letter",
        "nova_tier": 2,
        "authority_class": "primary_normative",
        "authority_level": "supervisory_guidance",
        "prudential_weight": 0.5,
        "status": "active",

        # Layer 1: Embedding fields
        "heading_path": ["Federal Reserve System", "Supervision and Regulation", "SR Letters", sr_num],
        "section_path": f"Federal Reserve System > Supervision and Regulation > SR Letters > {sr_num}",
        "structural_level": "document",
        "normative_weight": normative_weight,

        # Layer 2: Index/filter fields
        "current_version_flag": True,
        "is_primary_normative": False,
        "is_supporting_interpretive": True,
        "is_context_only": False,
        "paragraph_role": "scope_statement",
        "contains_definition": contains_definition,
        "contains_formula": False,
        "contains_requirement": contains_requirement,
        "contains_deadline": contains_deadline,
        "contains_parameter": contains_parameter,
        "contains_assignment": False,
        "is_appendix": False,
        "depth": 0,

        # Layer 3: Prompt injection fields
        "version_id": version_id,
        "version_label": version_label,
        "citation_anchor": f"#{sr_num.lower().replace(' ', '')}",

        # SR-specific
        "sr_letter_number": sr_num,
        "sr_year": sr_year,
        "sr_sequence": sr_seq,
        "topic_code": parsed.get("topic_code", ""),
        "joint_letters": parsed.get("joint_letters", []),
        "issuing_agency": "Board of Governors of the Federal Reserve System",
        "issuing_division": "Division of Supervision and Regulation",
        "document_date_raw": parsed["date_iso"],
        "document_date_iso": parsed["date_iso"],
        "effective_date_start": parsed["date_iso"],
        "effective_date_end": None,
        "applicability_raw": parsed.get("applicability", ""),

        # Relationships
        "supersedes": parsed.get("supersedes", []),
        "related_letters": parsed.get("related_letters", []),
        "superseded_by_doc_id": None,
        "supersedes_doc_id": parsed["supersedes"][0] if parsed.get("supersedes") else None,

        # Applicability
        "applies_to_state_member_banks": applies_smb,
        "applies_to_bank_holding_companies": applies_bhc,
        "applies_to_savings_loan_holding": applies_slhc,
        "applies_to_foreign_banking_orgs": applies_fbo,
        "applies_to_nonbank_financial": applies_nonbank,
        "applies_to_edge_agreement_corps": False,
        "applies_to_large_institutions": applies_large,

        # Content metrics
        "word_count_raw_body": parsed["word_count"],
        "section_headings": parsed["headings"],
        "toc_depth": len(parsed["headings"]),
        "has_appendices": parsed["has_appendices"],
        "has_tables": False,
        "table_count": 0,
        "has_footnotes": False,
        "footnote_count": 0,
        "inline_cross_references": parsed["cross_references"],

        # PDF attachments — filenames for quick lookup, detail for full linking
        "pdf_attachments": pdf_filenames,
        "pdf_attachments_detail": pdf_attachments_detail,
        "attachment_count": len(pdf_filenames),

        # Corpus inclusion
        "include_in_primary_corpus": True,
        "include_in_context_corpus": True,
        "include_in_support_corpus": True,

        # Source & linked files — connects the SR letter to all its artifacts
        "content_source": "Federal Reserve Website",
        "source_url": parsed["url"],          # e.g. .../srletters/sr1107.htm
        "source_html_url": parsed["url"],     # explicit HTML page link
        "canonical_url": parsed["url"],
        "scraped_on": SCRAPE_DATE,
        "enriched_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),

        # Connected files — all artifacts for this SR letter
        "connected_files": {
            "source_html_url": parsed["url"],
            "raw_html_local": f"SR_Letters/raw_html/{filename_stem}.html",
            "metadata_json": f"SR_Letters/json/{filename_stem}.json",
            "markdown_summary": f"SR_Letters/md/{filename_stem}.md",
            "pdf_attachments": pdf_attachments_detail,
        },

        # Layer 2: Search fields
        "bm25_text": bm25,
        "vector_text_prefix": f"Federal Reserve SR Letter {sr_num}: {title_normalized}"[:200],

        # Layer 4: Operational / audit trail
        "normalized_text_sha256": text_hash,
        "normalized_md_path": f"SR_Letters/md/{filename_stem}.md",
        "canonical_json_path": f"SR_Letters/json/{filename_stem}.json",
        "raw_html_path": f"SR_Letters/raw_html/{filename_stem}.html",
        "parser_version": PARSER_VERSIONS["sr"],
        "normalizer_version": ENRICHER_VERSIONS["sr"],
        "quality_score": 100,
        "quality_flags": [],
    }


def generate_markdown(parsed: dict, metadata: dict) -> str:
    """Generate a markdown summary for an SR letter."""
    lines = [
        f"# {metadata['title']}",
        "",
        f"**SR Number:** {parsed['sr_number']}",
        f"**Date:** {parsed['date_iso']}",
        f"**Issuer:** {metadata['issuing_agency']}",
        f"**Division:** {metadata['issuing_division']}",
    ]
    if parsed.get("topic_code"):
        lines.append(f"**Topic:** {parsed['topic_code']}")
    if parsed.get("applicability"):
        lines.append(f"**Applicability:** {parsed['applicability']}")
    if parsed.get("joint_letters"):
        lines.append(f"**Joint with:** {', '.join(parsed['joint_letters'])}")
    if parsed.get("supersedes"):
        lines.append(f"**Supersedes:** {', '.join(parsed['supersedes'])}")
    lines += [
        f"**Source:** [{parsed['url']}]({parsed['url']})",
        "",
        "---",
        "",
    ]
    # PDF attachments
    if parsed.get("pdf_links"):
        lines.append("## Attachments")
        lines.append("")
        for pdf in parsed["pdf_links"]:
            lines.append(f"- [{pdf['name']}]({pdf['url']})")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(parsed["body_text"])
    return "\n".join(lines) + "\n"


# ─── Main pipeline ──────────────────────────────────────────────────────────

def scrape_sr_letter(sr_info: dict) -> dict | None:
    """Scrape a single SR letter: fetch page, download PDFs, generate outputs."""
    try:
        parsed = fetch_sr_letter_page(sr_info)
    except requests.HTTPError as e:
        log.error("HTTP error fetching %s: %s", sr_info["sr_number"], e)
        return None
    except Exception as e:
        log.error("Error fetching %s: %s", sr_info["sr_number"], e)
        return None

    if not parsed:
        return None

    filename_stem = build_sr_filename(
        parsed["sr_number"], parsed["title"], parsed.get("topic_code", "")
    )

    # Pre-compute clean PDF filenames BEFORE building metadata
    attachment_idx = 0
    for pdf_info in parsed.get("pdf_links", []):
        sr_match = re.match(r"SR\s*(\d{2})-(\d+)", parsed["sr_number"], re.IGNORECASE)
        is_main = False
        if sr_match:
            yr, seq = sr_match.group(1), sr_match.group(2)
            padded = f"sr{yr}{seq.zfill(2)}"
            unpadded = f"sr{yr}{seq}"
            orig_lower = pdf_info["filename"].lower()
            is_main = orig_lower in (f"{padded}.pdf", f"{unpadded}.pdf")
        if not is_main:
            attachment_idx += 1
        pdf_info["clean_filename"] = build_pdf_filename(
            parsed["sr_number"], pdf_info, attachment_idx
        )

    metadata = build_metadata(parsed, filename_stem)
    md_content = generate_markdown(parsed, metadata)

    # Create output directories
    json_dir = SR_DIR / "json"
    md_dir = SR_DIR / "md"
    raw_html_dir = SR_DIR / "raw_html"
    pdf_dir = SR_DIR / "pdf"
    for d in [json_dir, md_dir, raw_html_dir, pdf_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Write raw HTML (original from website)
    (raw_html_dir / f"{filename_stem}.html").write_text(
        parsed["raw_html"], encoding="utf-8"
    )

    # Write JSON metadata
    (json_dir / f"{filename_stem}.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Write markdown
    (md_dir / f"{filename_stem}.md").write_text(md_content, encoding="utf-8")

    # Download PDF attachments with descriptive names
    pdfs_downloaded = 0
    for pdf_info in parsed.get("pdf_links", []):
        clean_name = pdf_info["clean_filename"]
        pdf_path = pdf_dir / clean_name
        if download_pdf(pdf_info["url"], pdf_path):
            pdfs_downloaded += 1
        time.sleep(0.5)  # Be polite with PDF downloads

    log.info(
        "%s: %d words, %d PDFs → %s",
        parsed["sr_number"], parsed["word_count"], pdfs_downloaded, filename_stem,
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Scrape Federal Reserve SR Letters")
    parser.add_argument("--years", nargs="*", type=int, default=None,
                        help="Scrape letters from specific years only")
    parser.add_argument("--letters", nargs="*", type=str, default=None,
                        help="Scrape specific SR letters by number (e.g. 'SR 25-6')")
    args = parser.parse_args()

    # Step 1: Get all year page URLs
    year_urls = fetch_year_urls()

    # Filter by year if requested
    if args.years:
        year_urls = [u for u in year_urls
                     if int(re.search(r"(\d{4})\.htm$", u).group(1)) in args.years]

    # Step 2: Crawl year pages to collect all SR letter links
    all_letters = []
    for year_url in year_urls:
        try:
            letters = fetch_letters_from_year_page(year_url)
            all_letters.extend(letters)
        except Exception as e:
            log.error("Error fetching year page %s: %s", year_url, e)
        time.sleep(SR_DELAY_SECONDS)

    log.info("Total SR letters found across all years: %d", len(all_letters))

    # Filter by specific letter numbers if requested
    if args.letters:
        # Normalize the filter input
        target_set = set()
        for l in args.letters:
            normalized = re.sub(r"[_ ]", " ", l).upper()
            if not normalized.startswith("SR"):
                normalized = f"SR {normalized}"
            target_set.add(normalized)
        all_letters = [l for l in all_letters if l["sr_number"].upper() in target_set]

    log.info("Scraping %d SR letters...", len(all_letters))

    # Step 3-5: Fetch each letter, download PDFs, generate outputs
    results = []
    failures = []
    for sr_info in all_letters:
        meta = scrape_sr_letter(sr_info)
        if meta:
            results.append(meta)
        else:
            failures.append(sr_info["sr_number"])
        time.sleep(SR_DELAY_SECONDS)

    log.info(
        "SR Letters scrape complete: %d/%d letters scraped, %d failures, %d total PDFs",
        len(results), len(all_letters), len(failures),
        sum(m.get("attachment_count", 0) for m in results),
    )
    if failures:
        log.warning("Failed SR letters: %s", failures)


if __name__ == "__main__":
    main()
