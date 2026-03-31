"""
SR Letters Scraper — Fetches Supervision and Regulation (SR) Letters from the
Federal Reserve Board website.

Unlike eCFR and Federal Register, SR Letters have no public API. This scraper:
  1. Fetches the SR Letters index page (by year)
  2. Extracts links to individual SR letter pages
  3. Fetches and parses each letter's HTML content
  4. Generates NOVA JSON, MD, and HTML outputs

Produces:
  SR_Letters/json/  — NOVA metadata per SR letter
  SR_Letters/md/    — Readable markdown content
  SR_Letters/html/  — Formatted HTML with metadata tags

Usage:
    python -m scrapers.scrape_sr_letters                       # All available years
    python -m scrapers.scrape_sr_letters --years 2024 2025     # Specific years
    python -m scrapers.scrape_sr_letters --letters sr25-6      # Specific letters
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.config import (
    SR_DIR,
    SR_DELAY_SECONDS,
    SR_LETTER_BASE,
    SR_LISTING_URL,
    PARSER_VERSIONS,
    ENRICHER_VERSIONS,
    SCRAPE_DATE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NOVA-SR-Scraper/1.0 (regulatory research)",
    "Accept": "text/html",
})


# ─── Index page parsing ──────────────────────────────────────────────────────

def fetch_sr_index() -> list[dict]:
    """
    Fetch the SR Letters index page and extract all SR letter links.
    Returns list of dicts: {sr_number, url, title, date_text}
    """
    log.info("Fetching SR Letters index from %s", SR_LISTING_URL)
    resp = SESSION.get(SR_LISTING_URL, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    letters = []

    # The index page groups letters by year in tables or div sections
    # Each row has: SR number link, date, title/subject
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)

        # Match SR letter links: e.g. SR2506.htm, sr25-6.htm, SR 25-6
        if re.search(r"SR\d{2,4}", href, re.IGNORECASE):
            sr_number = _extract_sr_number(href, text)
            if not sr_number:
                continue

            full_url = urljoin(SR_LISTING_URL, href)

            # Try to get date and title from surrounding table row
            parent_row = link.find_parent("tr")
            date_text = ""
            title_text = ""
            if parent_row:
                cells = parent_row.find_all("td")
                if len(cells) >= 2:
                    date_text = cells[0].get_text(strip=True) if cells[0] != link.parent else ""
                    # Title is usually the last cell or the link text
                    title_text = cells[-1].get_text(strip=True) if len(cells) >= 3 else text

            letters.append({
                "sr_number": sr_number,
                "url": full_url,
                "title": title_text or text,
                "date_text": date_text,
            })

    # Deduplicate by sr_number
    seen = set()
    unique = []
    for letter in letters:
        if letter["sr_number"] not in seen:
            seen.add(letter["sr_number"])
            unique.append(letter)

    log.info("Found %d unique SR letters in index", len(unique))
    return unique


def _extract_sr_number(href: str, text: str) -> str | None:
    """
    Extract normalized SR letter number from a link.
    E.g. 'SR2506.htm' → 'sr25-6', 'SR 25-6' → 'sr25-6'
    """
    # Try extracting from href filename
    filename = href.split("/")[-1].split(".")[0]

    # Pattern: SR2506 or SR25-6 or sr25-6-ca25-1
    match = re.match(r"[Ss][Rr]\s*(\d{2})[-]?(\d+)(.*)", filename)
    if match:
        year = match.group(1)
        seq = match.group(2)
        suffix = match.group(3)

        # Normalize suffix: handle -ca suffixes, (sup), etc.
        suffix_clean = suffix.lower().strip()
        # Remove .htm etc.
        suffix_clean = re.sub(r"\.(htm|html|pdf)$", "", suffix_clean)

        sr_num = f"sr{year}-{seq}"
        if suffix_clean:
            sr_num += suffix_clean
        return sr_num

    # Try from text
    text_match = re.match(r"SR\s*(\d{2})-(\d+)(.*)", text, re.IGNORECASE)
    if text_match:
        return f"sr{text_match.group(1)}-{text_match.group(2)}{text_match.group(3).lower().strip()}"

    return None


def fetch_sr_years_index() -> list[dict]:
    """
    Alternative index approach: fetch year-specific listing pages.
    The Fed website often has: /supervisionreg/srletters/srlettersbyYYYY.htm
    """
    all_letters = []

    # First get the main listing which links to year pages
    resp = SESSION.get(SR_LISTING_URL, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    year_links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if re.search(r"srletters\d{4}|srbyyear|sr\d{4}", href, re.IGNORECASE):
            year_links.append(urljoin(SR_LISTING_URL, href))

    # Also construct year URLs for known range
    for year in range(1990, datetime.now().year + 1):
        url = f"{SR_LETTER_BASE}/srletters{year}.htm"
        if url not in year_links:
            year_links.append(url)

    for year_url in year_links:
        try:
            resp = SESSION.get(year_url, timeout=30)
            if resp.status_code == 200:
                year_soup = BeautifulSoup(resp.text, "lxml")
                for link in year_soup.find_all("a", href=True):
                    href = link["href"]
                    if re.search(r"SR\d{2}", href, re.IGNORECASE) and href != year_url:
                        sr_num = _extract_sr_number(href, link.get_text(strip=True))
                        if sr_num:
                            all_letters.append({
                                "sr_number": sr_num,
                                "url": urljoin(year_url, href),
                                "title": link.get_text(strip=True),
                                "date_text": "",
                            })
            time.sleep(SR_DELAY_SECONDS)
        except Exception as e:
            log.warning("Could not fetch year page %s: %s", year_url, e)

    # Deduplicate
    seen = set()
    unique = []
    for letter in all_letters:
        if letter["sr_number"] not in seen:
            seen.add(letter["sr_number"])
            unique.append(letter)

    return unique


# ─── Individual SR letter parsing ─────────────────────────────────────────────

def fetch_and_parse_sr_letter(sr_info: dict) -> dict:
    """
    Fetch an individual SR letter page and extract its content.
    Returns parsed dict with: title, date, body_text, body_html, applicability, etc.
    """
    url = sr_info["url"]
    log.info("Fetching SR letter %s from %s", sr_info["sr_number"], url)

    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    raw_html = resp.text
    soup = BeautifulSoup(raw_html, "lxml")

    # Extract title — usually in the page <title> or an h1/h3
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    if not title:
        h1 = soup.find("h1") or soup.find("h3")
        title = h1.get_text(strip=True) if h1 else sr_info["title"]

    # Clean up title: remove "FRB: " prefix
    title = re.sub(r"^FRB:\s*", "", title)
    if not title.upper().startswith("SR"):
        title = f"{sr_info['sr_number'].upper()}: {title}"

    # Extract date
    date_iso = _parse_sr_date(raw_html, sr_info)

    # Extract applicability
    applicability = ""
    app_match = re.search(
        r"Applicabil(?:ity|e\s+to)[:\s]+(.+?)(?=<br|<p|\n\n|SUBJECT|Dear)",
        raw_html, re.IGNORECASE | re.DOTALL
    )
    if app_match:
        applicability = BeautifulSoup(app_match.group(1), "lxml").get_text(strip=True)

    # Extract body text (main content area)
    # Try the main content div
    content_div = (
        soup.find("div", {"id": "content"})
        or soup.find("div", class_="col-xs-12")
        or soup.find("div", class_="content")
        or soup.find("body")
    )
    body_text = content_div.get_text("\n", strip=True) if content_div else soup.get_text("\n", strip=True)
    body_html = str(content_div) if content_div else raw_html

    # Extract section headings from the body
    headings = []
    for h in soup.find_all(re.compile(r"h[1-6]")):
        headings.append(h.get_text(strip=True))
    # Also look for bold/strong text that serves as section headers
    for strong in soup.find_all(["strong", "b"]):
        text = strong.get_text(strip=True)
        if len(text) > 5 and len(text) < 200 and text.endswith(":"):
            headings.append(text)

    # Detect appendices
    has_appendices = bool(re.search(r"(?:Attachment|Appendix|Enclosure)\s*[A-Z0-9]", body_text, re.IGNORECASE))

    # Inline cross-references
    cross_refs = _extract_cross_references(body_text)

    return {
        "sr_number": sr_info["sr_number"],
        "title": title,
        "date_iso": date_iso,
        "applicability": applicability,
        "body_text": body_text,
        "body_html": body_html,
        "headings": headings,
        "has_appendices": has_appendices,
        "cross_references": cross_refs,
        "source_url": url,
        "word_count": len(body_text.split()),
    }


def _parse_sr_date(html_text: str, sr_info: dict) -> str:
    """Extract the date from an SR letter page."""
    # Try common patterns in the HTML
    date_patterns = [
        r"(\w+\s+\d{1,2},?\s+\d{4})",  # "December 19, 2025"
        r"(\d{1,2}/\d{1,2}/\d{4})",      # "12/19/2025"
        r"(\d{4}-\d{2}-\d{2})",           # "2025-12-19"
    ]
    for pattern in date_patterns:
        match = re.search(pattern, html_text[:3000])  # Only look in first 3k chars
        if match:
            raw_date = match.group(1)
            for fmt in ["%B %d, %Y", "%B %d %Y", "%m/%d/%Y", "%Y-%m-%d"]:
                try:
                    dt = datetime.strptime(raw_date, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue

    # Fallback: derive from SR number
    sr_num = sr_info["sr_number"]
    year_match = re.search(r"sr(\d{2})-", sr_num)
    if year_match:
        year = int(year_match.group(1))
        full_year = 2000 + year if year < 90 else 1900 + year
        return f"{full_year}-01-01"

    return SCRAPE_DATE


def _extract_cross_references(text: str) -> list[str]:
    """Extract regulatory cross-references from body text."""
    refs = set()
    # CFR references
    for m in re.finditer(r"\d+\s*CFR\s*(?:Part\s*)?\d+", text):
        refs.add(m.group(0).strip())
    # SR letter references
    for m in re.finditer(r"SR\s*\d{2}-\d+", text, re.IGNORECASE):
        refs.add(m.group(0).strip())
    # Regulation letter references
    for m in re.finditer(r"Regulation\s+[A-Z]{1,2}\b", text):
        refs.add(m.group(0).strip())
    return sorted(refs)


# ─── Output generation ───────────────────────────────────────────────────────

def build_sr_metadata(parsed: dict) -> dict:
    """Build full NOVA metadata for an SR letter."""
    sr_num = parsed["sr_number"]
    sr_upper = sr_num.upper().replace("SR", "SR ")

    # Parse year and sequence
    yr_match = re.match(r"sr(\d{2})-(\d+)", sr_num)
    sr_year = None
    sr_seq = None
    if yr_match:
        yr = int(yr_match.group(1))
        sr_year = 2000 + yr if yr < 90 else 1900 + yr
        sr_seq = int(yr_match.group(2))

    doc_id = f"usfed.{sr_num}.{parsed['date_iso'].replace('-', '')}.letter"
    title = parsed["title"] if ":" in parsed["title"] else f"{sr_upper}: {parsed['title']}"
    title_normalized = re.sub(r"^SR\s*\d{2}-\d+[^:]*:\s*", "", title)

    text_hash = hashlib.sha256(parsed["body_text"].encode("utf-8")).hexdigest()

    # Applicability flags
    app_text = (parsed.get("applicability", "") + " " + parsed["body_text"][:2000]).lower()
    applies_smb = "state member bank" in app_text or "member bank" in app_text
    applies_bhc = "bank holding compan" in app_text or "holding compan" in app_text
    applies_slhc = "savings and loan" in app_text or "thrift" in app_text
    applies_fbo = "foreign bank" in app_text
    applies_nonbank = "nonbank" in app_text or "systemically important" in app_text
    applies_large = "large" in app_text and ("institution" in app_text or "organization" in app_text)

    # ── NOVA Layer 1: Embedding structural fields ───────────────────────
    heading_path = ["Federal Reserve System", "Supervision and Regulation", "SR Letters", sr_upper]
    structural_level = "document"

    # normative_weight: SR letters are supervisory guidance (advisory)
    body_lower = parsed["body_text"][:5000].lower()
    if any(w in body_lower for w in ["must", "shall", "required to"]):
        normative_weight = "mandatory"
    elif any(w in body_lower for w in ["should", "expected to", "is expected"]):
        normative_weight = "advisory"
    else:
        normative_weight = "informational"

    paragraph_role = "scope_statement"

    # ── NOVA Layer 2: Content flags ───────────────────────────────────────
    contains_definition = bool(re.search(
        r"(?:means|defined as|definition of|the term)", body_lower
    ))
    contains_requirement = bool(re.search(
        r"\b(?:shall|must|required to|is required)\b", body_lower
    ))
    contains_deadline = bool(re.search(
        r"\b(?:within \d+ days|no later than|by \w+ \d{1,2}|effective immediately)\b", body_lower
    ))
    contains_parameter = bool(re.search(
        r"\b(?:threshold|minimum|maximum|at least|not exceed|percent|basis points)\b", body_lower
    ))

    # ── NOVA Layer 3: Version fields ──────────────────────────────────────
    version_id = parsed["date_iso"]
    version_label = str(sr_year) if sr_year else parsed["date_iso"][:4]
    doc_family_id = f"usfed.{sr_num}"

    return {
        # ── Identity ──────────────────────────────────────────────────────
        "doc_id": doc_id,
        "doc_family_id": doc_family_id,
        "title": title,
        "title_normalized": title_normalized,
        "short_title": sr_upper,
        "slug": f"sr-letter-{sr_num}",

        # ── Classification ────────────────────────────────────────────────
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

        # ── Layer 1: Embedding fields ─────────────────────────────────────
        "heading_path": heading_path,
        "section_path": f"Federal Reserve System > Supervision and Regulation > SR Letters > {sr_upper}",
        "structural_level": structural_level,
        "normative_weight": normative_weight,

        # ── Layer 2: Index/filter fields ──────────────────────────────────
        "current_version_flag": True,
        "is_primary_normative": False,
        "is_supporting_interpretive": True,
        "is_context_only": False,
        "paragraph_role": paragraph_role,
        "contains_definition": contains_definition,
        "contains_formula": False,
        "contains_requirement": contains_requirement,
        "contains_deadline": contains_deadline,
        "contains_parameter": contains_parameter,
        "contains_assignment": False,
        "is_appendix": False,
        "depth": 0,

        # ── Layer 3: Prompt injection fields ──────────────────────────────
        "version_id": version_id,
        "version_label": version_label,
        "citation_anchor": f"#sr{sr_num}",

        # ── SR-specific ───────────────────────────────────────────────────
        "sr_letter_number": sr_upper,
        "sr_year": sr_year,
        "sr_sequence": sr_seq,
        "issuing_agency": "Board of Governors of the Federal Reserve System",
        "issuing_division": "Division of Supervision and Regulation",
        "document_date_raw": parsed["date_iso"],
        "document_date_iso": parsed["date_iso"],
        "effective_date_start": parsed["date_iso"],
        "effective_date_end": None,
        "applicability_raw": parsed.get("applicability", ""),

        # ── Applicability ─────────────────────────────────────────────────
        "applies_to_state_member_banks": applies_smb,
        "applies_to_bank_holding_companies": applies_bhc,
        "applies_to_savings_loan_holding": applies_slhc,
        "applies_to_foreign_banking_orgs": applies_fbo,
        "applies_to_nonbank_financial": applies_nonbank,
        "applies_to_edge_agreement_corps": False,
        "applies_to_large_institutions": applies_large,

        # ── Content metrics ───────────────────────────────────────────────
        "word_count_raw_body": parsed["word_count"],
        "section_headings": parsed["headings"],
        "toc_depth": len(parsed["headings"]),
        "has_appendices": parsed["has_appendices"],
        "has_tables": False,
        "table_count": 0,
        "has_footnotes": False,
        "footnote_count": 0,
        "inline_cross_references": parsed["cross_references"],

        # ── Corpus inclusion ──────────────────────────────────────────────
        "include_in_primary_corpus": True,
        "include_in_context_corpus": True,
        "include_in_support_corpus": True,

        # ── Versioning ────────────────────────────────────────────────────
        "superseded_by_doc_id": None,
        "supersedes_doc_id": None,

        # ── Source ────────────────────────────────────────────────────────
        "content_source": "Federal Reserve Website",
        "source_url": parsed["source_url"],
        "canonical_url": parsed["source_url"],
        "scraped_on": SCRAPE_DATE,
        "enriched_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),

        # ── Layer 2: Search fields ────────────────────────────────────────
        "bm25_text": (f"{sr_upper} {title_normalized} {parsed.get('applicability', '')} "
                      + " ".join(parsed["headings"][:10]))[:500],
        "vector_text_prefix": f"Federal Reserve SR Letter {sr_upper}: {title_normalized}"[:200],

        # ── Layer 4: Operational / audit trail ────────────────────────────
        "normalized_text_sha256": text_hash,
        "normalized_md_path": f"SR_Letters/md/{sr_num}.md",
        "canonical_json_path": f"SR_Letters/json/{sr_num}.json",
        "parser_version": PARSER_VERSIONS["sr"],
        "normalizer_version": ENRICHER_VERSIONS["sr"],
        "quality_score": 100,
        "quality_flags": [],
    }


def generate_sr_markdown(parsed: dict, metadata: dict) -> str:
    """Generate markdown content for an SR letter."""
    lines = [
        f"# {metadata['title']}",
        "",
        f"**Issuer:** {metadata['issuing_agency']}",
        f"**Division:** {metadata['issuing_division']}",
        f"**Date:** {parsed['date_iso']}",
    ]
    if parsed.get("applicability"):
        lines.append(f"**Applicability:** {parsed['applicability']}")
    lines += [
        f"**Source:** [{parsed['source_url']}]({parsed['source_url']})",
        "",
        "---",
        "",
        parsed["body_text"],
    ]
    return "\n".join(lines) + "\n"


def generate_sr_html(parsed: dict, metadata: dict) -> str:
    """Generate styled HTML for an SR letter."""
    app_line = ""
    if parsed.get("applicability"):
        app_line = f"        <dt>Applicability</dt><dd>{html_escape(parsed['applicability'])}</dd>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_escape(metadata['title'])}</title>
    <meta name="doc-id" content="{html_escape(metadata['doc_id'])}">
    <meta name="regulator" content="Federal Reserve System">
    <meta name="document-class" content="sr_letter">
    <meta name="nova-tier" content="{metadata['nova_tier']}">
    <meta name="authority-class" content="{html_escape(metadata['authority_class'])}">
    <meta name="date-issued" content="{html_escape(parsed['date_iso'])}">
    <meta name="source-url" content="{html_escape(parsed['source_url'])}">
    <style>
        body {{ font-family: 'Georgia', serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; color: #333; }}
        .header {{ border-bottom: 2px solid #003366; padding-bottom: 1rem; margin-bottom: 2rem; }}
        .header h1 {{ color: #003366; font-size: 1.5rem; }}
        .meta {{ background: #f5f5f5; padding: 1rem; border-radius: 4px; margin-bottom: 2rem; }}
        .meta dt {{ font-weight: bold; color: #003366; }}
        .meta dd {{ margin-left: 0; margin-bottom: 0.5rem; }}
        .content {{ white-space: pre-wrap; }}
        .footer {{ border-top: 1px solid #ccc; padding-top: 1rem; margin-top: 2rem; font-size: 0.9rem; color: #666; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{html_escape(metadata['title'])}</h1>
    </div>
    <dl class="meta">
        <dt>Issuer</dt><dd>{html_escape(metadata['issuing_agency'])}</dd>
        <dt>Division</dt><dd>{html_escape(metadata['issuing_division'])}</dd>
        <dt>Date</dt><dd>{html_escape(parsed['date_iso'])}</dd>
{app_line}
        <dt>NOVA Tier</dt><dd>{metadata['nova_tier']}</dd>
        <dt>Source</dt><dd><a href="{html_escape(parsed['source_url'])}">{html_escape(parsed['source_url'])}</a></dd>
    </dl>
    <div class="content">
{parsed['body_html']}
    </div>
    <div class="footer">
        <p>Scraped: {SCRAPE_DATE} | Parser: {PARSER_VERSIONS['sr']}</p>
    </div>
</body>
</html>"""


# ─── Main pipeline ────────────────────────────────────────────────────────────

def scrape_sr_letter(sr_info: dict) -> dict | None:
    """Scrape a single SR letter: fetch, parse, generate outputs."""
    try:
        parsed = fetch_and_parse_sr_letter(sr_info)
    except requests.HTTPError as e:
        log.error("HTTP error fetching %s: %s", sr_info["sr_number"], e)
        return None
    except Exception as e:
        log.error("Error fetching %s: %s", sr_info["sr_number"], e)
        return None

    metadata = build_sr_metadata(parsed)
    md_content = generate_sr_markdown(parsed, metadata)
    html_content = generate_sr_html(parsed, metadata)

    # Write outputs
    json_dir = SR_DIR / "json"
    md_dir = SR_DIR / "md"
    html_dir = SR_DIR / "html"
    for d in [json_dir, md_dir, html_dir]:
        d.mkdir(parents=True, exist_ok=True)

    sr_num = sr_info["sr_number"]
    (json_dir / f"{sr_num}.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (md_dir / f"{sr_num}.md").write_text(md_content, encoding="utf-8")
    (html_dir / f"{sr_num}.html").write_text(html_content, encoding="utf-8")

    log.info("SR %s: %d words → %s", sr_num, parsed["word_count"], sr_num)
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Scrape Federal Reserve SR Letters")
    parser.add_argument("--years", nargs="*", type=int, default=None,
                        help="Scrape letters from specific years only")
    parser.add_argument("--letters", nargs="*", type=str, default=None,
                        help="Scrape specific SR letters by number (e.g. sr25-6)")
    args = parser.parse_args()

    # Get index of all SR letters
    all_letters = fetch_sr_index()
    if not all_letters:
        log.info("Primary index empty, trying year-based approach...")
        all_letters = fetch_sr_years_index()

    # Filter by year if requested
    if args.years:
        filtered = []
        for letter in all_letters:
            yr_match = re.match(r"sr(\d{2})-", letter["sr_number"])
            if yr_match:
                yr = int(yr_match.group(1))
                full_yr = 2000 + yr if yr < 90 else 1900 + yr
                if full_yr in args.years:
                    filtered.append(letter)
        all_letters = filtered

    # Filter by specific letter numbers if requested
    if args.letters:
        target_set = set(l.lower() for l in args.letters)
        all_letters = [l for l in all_letters if l["sr_number"] in target_set]

    log.info("Scraping %d SR letters...", len(all_letters))

    results = []
    for sr_info in all_letters:
        meta = scrape_sr_letter(sr_info)
        if meta:
            results.append(meta)
        time.sleep(SR_DELAY_SECONDS)

    log.info(
        "SR Letters scrape complete: %d/%d letters scraped",
        len(results), len(all_letters),
    )


if __name__ == "__main__":
    main()
