"""
NOVA Metadata Enrichment — Post-processing pass that ensures every JSON metadata
file has all required NOVA fields populated, consistent, and enriched.

This runs after the individual scrapers and:
  1. Validates all required fields are present
  2. Fills in computed fields (hashes, word counts, cross-refs)
  3. Normalizes and deduplicates values
  4. Recomputes BM25 / vector text fields
  5. Generates a corpus-level audit report

Usage:
    python -m scrapers.enrich_metadata                     # Enrich all sources
    python -m scrapers.enrich_metadata --source ecfr       # Just eCFR
    python -m scrapers.enrich_metadata --validate-only     # Dry run, report issues
"""

import argparse
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from scrapers.config import (
    ECFR_DIR,
    FR_DIR,
    SR_DIR,
    ENRICHER_VERSIONS,
    PART_TO_REG_LETTER,
    nova_tier_for_part,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ─── Required fields per source type ─────────────────────────────────────────

# ── NOVA 3-Layer required fields ──────────────────────────────────────────────
# These align with the NOVA 3-Layer Metadata Architecture:
#   Layer 1 (Embedding): doc_id, short_title, document_class, heading_path,
#       section_path, regulator, structural_level, normative_weight
#   Layer 2 (Index/Filter): status, jurisdiction, nova_tier, authority_class,
#       effective_date_start, current_version_flag, version_id, normative_weight,
#       structural_level, paragraph_role, contains_* flags, bm25_text
#   Layer 3 (Prompt Injection): title, citation_anchor, version_id, version_label,
#       current_version_flag, effective_date_start, status, authority_class, nova_tier
#   Layer 4 (Operational): canonical_json_path, normalized_md_path, parser_version,
#       normalizer_version, quality_score

COMMON_REQUIRED = [
    # Identity
    "doc_id", "doc_family_id", "title", "short_title", "slug", "title_normalized",
    # Classification
    "regulator", "regulator_acronym", "jurisdiction",
    "document_class", "nova_tier", "authority_class", "authority_level", "status",
    # Layer 1: Embedding
    "heading_path", "section_path", "structural_level", "normative_weight",
    # Layer 2: Index/Filter
    "current_version_flag", "is_primary_normative",
    "paragraph_role",
    "contains_definition", "contains_requirement",
    # Layer 3: Prompt Injection
    "version_id", "version_label", "citation_anchor",
    # Temporal
    "scraped_on", "enriched_timestamp",
    # Source
    "content_source", "source_url", "canonical_url",
    "issuing_agency",
    # Corpus
    "include_in_primary_corpus", "include_in_context_corpus",
    # Content metrics
    "word_count_raw_body",
    # Search
    "bm25_text", "vector_text_prefix",
    # Layer 4: Operational
    "normalized_text_sha256", "normalized_md_path", "canonical_json_path",
    "parser_version", "normalizer_version",
    "quality_score", "quality_flags",
]

ECFR_REQUIRED = COMMON_REQUIRED + [
    "title_number", "chapter", "part_number", "part_name",
    "cfr_citation", "section_count", "section_headings",
    "ecfr_current_as_of", "effective_date_start",
    "applies_to_state_member_banks", "applies_to_bank_holding_companies",
    "contains_formula", "contains_deadline", "contains_parameter",
]

FR_REQUIRED = COMMON_REQUIRED + [
    "document_number", "citation", "publication_date",
    "publication_type_normalized",
]

SR_REQUIRED = COMMON_REQUIRED + [
    "sr_letter_number", "document_date_iso",
    "applies_to_state_member_banks", "applies_to_bank_holding_companies",
]


# ─── Enrichment functions ────────────────────────────────────────────────────

def enrich_ecfr_metadata(json_path: Path, validate_only: bool = False) -> list[str]:
    """Enrich/validate a single eCFR metadata JSON file."""
    issues = []
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    part_num = meta.get("part_number")
    reg_letter = meta.get("regulation_letter")

    # ── Fill missing NOVA 3-layer fields ──────────────────────────────────
    if not validate_only:
        # Layer 1: Embedding fields
        if "heading_path" not in meta:
            hp = ["Federal Reserve System", "12 CFR Chapter II"]
            if reg_letter:
                hp.append(f"Regulation {reg_letter}")
            hp.append(f"Part {part_num}")
            meta["heading_path"] = hp

        if "structural_level" not in meta:
            meta["structural_level"] = "part"

        if "normative_weight" not in meta:
            ac = meta.get("authority_class", "")
            if ac == "primary_normative":
                meta["normative_weight"] = "mandatory"
            elif ac == "procedural_administrative":
                meta["normative_weight"] = "advisory"
            else:
                meta["normative_weight"] = "informational"

        # Layer 2: Index/filter fields
        if "paragraph_role" not in meta:
            meta["paragraph_role"] = "scope_statement"

        if "doc_family_id" not in meta:
            reg_slug = f"reg{reg_letter.lower()}" if reg_letter else f"part{part_num}"
            meta["doc_family_id"] = f"usfed.{reg_slug}.{part_num}"

        # Layer 3: Prompt injection
        if "version_id" not in meta:
            meta["version_id"] = meta.get("ecfr_current_as_of", meta.get("effective_date_start", ""))
        if "version_label" not in meta:
            vid = meta.get("version_id", "")
            meta["version_label"] = vid[:4] if vid else ""

        # Content flags (scan MD if available)
        md_path = json_path.parent.parent / "md" / json_path.name.replace(".json", ".md")
        scan_text = ""
        if md_path.exists():
            scan_text = md_path.read_text(encoding="utf-8")[:15000].lower()

        for flag, pattern in [
            ("contains_definition", r"(?:means|defined as|definition of|the term)"),
            ("contains_requirement", r"\b(?:shall|must|required to|is required)\b"),
            ("contains_formula", r"(?:formula|=\s*\(|calculated as|ratio\s*=)"),
            ("contains_deadline", r"\b(?:within \d+ days|no later than|by \w+ \d{1,2})"),
            ("contains_parameter", r"\b(?:threshold|minimum|maximum|at least|not exceed|percent|basis points)\b"),
        ]:
            if flag not in meta:
                meta[flag] = bool(re.search(pattern, scan_text, re.IGNORECASE)) if scan_text else False

    # ── Check required fields ─────────────────────────────────────────────
    for field in ECFR_REQUIRED:
        if field not in meta or meta[field] is None:
            issues.append(f"MISSING: {field}")

    # Verify MD file exists and recompute hash + word count
    md_path = json_path.parent.parent / "md" / json_path.name.replace(".json", ".md")
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
        computed_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()
        computed_words = len(md_content.split())

        if meta.get("normalized_text_sha256") != computed_hash:
            issues.append(f"HASH_MISMATCH: stored={meta.get('normalized_text_sha256', 'none')[:16]}...")
            if not validate_only:
                meta["normalized_text_sha256"] = computed_hash

        if meta.get("word_count_raw_body", 0) != computed_words:
            issues.append(f"WORDCOUNT_MISMATCH: stored={meta.get('word_count_raw_body')}, actual={computed_words}")
            if not validate_only:
                meta["word_count_raw_body"] = computed_words
    else:
        issues.append(f"MD_MISSING: {md_path.name}")

    # Verify HTML file exists
    html_path = json_path.parent.parent / "html" / json_path.name.replace(".json", ".html")
    if not html_path.exists():
        issues.append(f"HTML_MISSING: {html_path.name}")

    # Validate NOVA tier
    if part_num:
        expected_tier = nova_tier_for_part(part_num)
        if meta.get("nova_tier") != expected_tier:
            issues.append(f"TIER_MISMATCH: stored={meta.get('nova_tier')}, expected={expected_tier}")
            if not validate_only:
                meta["nova_tier"] = expected_tier

    # Ensure enriched_timestamp is current
    if not validate_only:
        meta["enriched_timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["normalizer_version"] = ENRICHER_VERSIONS["ecfr"]

    # Recompute BM25 text
    if not validate_only:
        headings = meta.get("section_headings", [])[:15]
        short = meta.get("short_title", "")
        title = meta.get("title", "")
        toc = " ".join(headings)
        meta["bm25_text"] = f"{short} {title} {meta.get('cfr_citation', '')} {toc}"[:500]
        meta["vector_text_prefix"] = (
            f"US Federal Reserve {meta.get('cfr_citation', '')} {short}: {title}"
        )[:200]

    # Write back
    if not validate_only:
        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return issues


def enrich_fr_metadata(json_path: Path, validate_only: bool = False) -> list[str]:
    """Enrich/validate a single Federal Register metadata JSON file."""
    issues = []
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    doc_num = meta.get("document_number", meta.get("short_title", ""))
    doc_type = meta.get("document_class", "")
    pub_type = meta.get("publication_type_normalized", doc_type)
    pub_date = meta.get("publication_date", "")

    # ── Fill missing NOVA 3-layer fields ──────────────────────────────────
    if not validate_only:
        # Layer 1: Embedding fields
        if "heading_path" not in meta:
            meta["heading_path"] = ["Federal Reserve System", "Federal Register", pub_type, doc_num]

        if "structural_level" not in meta:
            meta["structural_level"] = "document"

        if "normative_weight" not in meta:
            if doc_type in ("Rule", "Final Rule"):
                meta["normative_weight"] = "mandatory"
            else:
                meta["normative_weight"] = "informational"

        # Layer 2: Index/filter fields
        if "paragraph_role" not in meta:
            meta["paragraph_role"] = "scope_statement" if doc_type in ("Rule", "Final Rule") else "rationale"

        if "doc_family_id" not in meta:
            meta["doc_family_id"] = f"usfed.fr.{doc_num}"

        if "citation_anchor" not in meta:
            meta["citation_anchor"] = f"#fr-{doc_num}"

        # Content flags from abstract/action
        scan_text = (meta.get("abstract", "") or "") + " " + (meta.get("action", "") or "")
        scan_lower = scan_text.lower()
        for flag, pattern in [
            ("contains_definition", r"(?:defines|definition of|means)"),
            ("contains_requirement", r"\b(?:shall|must|required)\b"),
        ]:
            if flag not in meta:
                meta[flag] = bool(re.search(pattern, scan_lower))

        # Layer 3: Prompt injection
        if "version_id" not in meta:
            meta["version_id"] = pub_date
        if "version_label" not in meta:
            meta["version_label"] = pub_date[:4] if pub_date else None
        if "title_normalized" not in meta:
            meta["title_normalized"] = meta.get("title", "")

    # ── Check required fields ─────────────────────────────────────────────
    for field in FR_REQUIRED:
        if field not in meta or meta[field] is None:
            issues.append(f"MISSING: {field}")

    # Verify MD and HTML exist
    md_path = json_path.parent.parent / "md" / json_path.name.replace(".json", ".md")
    html_path = json_path.parent.parent / "html" / json_path.name.replace(".json", ".html")
    if not md_path.exists():
        issues.append(f"MD_MISSING: {md_path.name}")
    if not html_path.exists():
        issues.append(f"HTML_MISSING: {html_path.name}")

    # Recompute hash if MD exists
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
        computed_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()
        if meta.get("normalized_text_sha256") != computed_hash:
            issues.append("HASH_MISMATCH")
            if not validate_only:
                meta["normalized_text_sha256"] = computed_hash
                meta["word_count_raw_body"] = len(md_content.split())

    # Ensure consistent publication_type_normalized
    if doc_type == "Rule" and meta.get("publication_type_normalized") != "Final Rule":
        if not validate_only:
            meta["publication_type_normalized"] = "Final Rule"
        issues.append("TYPE_NORMALIZED_MISMATCH")

    if not validate_only:
        meta["enriched_timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["normalizer_version"] = ENRICHER_VERSIONS["fr"]

    if not validate_only:
        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return issues


def enrich_sr_metadata(json_path: Path, validate_only: bool = False) -> list[str]:
    """Enrich/validate a single SR Letter metadata JSON file."""
    issues = []
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    sr_num_raw = meta.get("sr_letter_number", meta.get("short_title", ""))
    sr_num = json_path.stem  # e.g. "sr25-6"
    date_iso = meta.get("document_date_iso", meta.get("effective_date_start", ""))

    # ── Fill missing NOVA 3-layer fields ──────────────────────────────────
    if not validate_only:
        # Layer 1: Embedding fields
        if "heading_path" not in meta:
            meta["heading_path"] = [
                "Federal Reserve System", "Supervision and Regulation",
                "SR Letters", sr_num_raw or sr_num.upper()
            ]

        if "structural_level" not in meta:
            meta["structural_level"] = "document"

        if "normative_weight" not in meta:
            # Scan MD content to determine weight
            md_path = json_path.parent.parent / "md" / f"{sr_num}.md"
            body = ""
            if md_path.exists():
                body = md_path.read_text(encoding="utf-8")[:5000].lower()
            if any(w in body for w in ["must", "shall", "required to"]):
                meta["normative_weight"] = "mandatory"
            elif any(w in body for w in ["should", "expected to", "is expected"]):
                meta["normative_weight"] = "advisory"
            else:
                meta["normative_weight"] = "informational"

        # Layer 2: Index/filter fields
        if "paragraph_role" not in meta:
            meta["paragraph_role"] = "scope_statement"

        if "doc_family_id" not in meta:
            meta["doc_family_id"] = f"usfed.{sr_num}"

        # Content flags
        md_path = json_path.parent.parent / "md" / f"{sr_num}.md"
        scan_text = ""
        if md_path.exists():
            scan_text = md_path.read_text(encoding="utf-8")[:10000].lower()
        for flag, pattern in [
            ("contains_definition", r"(?:means|defined as|definition of|the term)"),
            ("contains_requirement", r"\b(?:shall|must|required to|is required)\b"),
        ]:
            if flag not in meta:
                meta[flag] = bool(re.search(pattern, scan_text)) if scan_text else False

        # Layer 3: Prompt injection
        if "version_id" not in meta:
            meta["version_id"] = date_iso
        if "version_label" not in meta:
            yr = meta.get("sr_year")
            meta["version_label"] = str(yr) if yr else (date_iso[:4] if date_iso else "")
        if "title_normalized" not in meta:
            title = meta.get("title", "")
            meta["title_normalized"] = re.sub(r"^SR\s*\d{2}-\d+[^:]*:\s*", "", title)

    # ── Check required fields ─────────────────────────────────────────────
    for field in SR_REQUIRED:
        if field not in meta or meta[field] is None:
            issues.append(f"MISSING: {field}")

    # Verify MD and HTML exist
    md_path = json_path.parent.parent / "md" / json_path.name.replace(".json", ".md")
    html_path = json_path.parent.parent / "html" / json_path.name.replace(".json", ".html")
    if not md_path.exists():
        issues.append(f"MD_MISSING: {md_path.name}")
    if not html_path.exists():
        issues.append(f"HTML_MISSING: {html_path.name}")

    # Recompute hash
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
        computed_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()
        if meta.get("normalized_text_sha256") != computed_hash:
            issues.append("HASH_MISMATCH")
            if not validate_only:
                meta["normalized_text_sha256"] = computed_hash
                meta["word_count_raw_body"] = len(md_content.split())

    # Validate SR number format
    sr_letter_num = meta.get("sr_letter_number", "")
    if not re.match(r"SR\s*\d{2}-\d+", sr_letter_num):
        issues.append(f"SR_NUMBER_FORMAT: {sr_letter_num}")

    if not validate_only:
        meta["enriched_timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["normalizer_version"] = ENRICHER_VERSIONS["sr"]

    if not validate_only:
        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return issues


# ─── Corpus-level audit ──────────────────────────────────────────────────────

def audit_corpus(ecfr_results: dict, fr_results: dict, sr_results: dict) -> str:
    """Generate a text audit report of the full corpus."""
    lines = [
        "=" * 70,
        "NOVA US Federal Regulations Corpus — Enrichment Audit Report",
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "=" * 70,
        "",
    ]

    for name, results in [("eCFR", ecfr_results), ("Federal Register", fr_results), ("SR Letters", sr_results)]:
        total = results["total"]
        clean = results["clean"]
        with_issues = results["with_issues"]
        lines.append(f"── {name} ──")
        lines.append(f"  Total files:  {total}")
        lines.append(f"  Clean:        {clean}")
        lines.append(f"  With issues:  {with_issues}")
        if results["issue_summary"]:
            lines.append("  Issue breakdown:")
            for issue_type, count in sorted(results["issue_summary"].items(), key=lambda x: -x[1]):
                lines.append(f"    {issue_type}: {count}")
        lines.append("")

    total_all = ecfr_results["total"] + fr_results["total"] + sr_results["total"]
    clean_all = ecfr_results["clean"] + fr_results["clean"] + sr_results["clean"]
    lines.append(f"TOTAL: {total_all} files, {clean_all} clean ({100*clean_all/max(total_all,1):.1f}%)")

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_enrichment(source_dir: Path, enrich_fn, source_name: str, validate_only: bool) -> dict:
    """Run enrichment across all JSON files in a source directory."""
    json_dir = source_dir / "json"
    if not json_dir.exists():
        log.warning("%s json dir not found: %s", source_name, json_dir)
        return {"total": 0, "clean": 0, "with_issues": 0, "issue_summary": {}}

    json_files = sorted(json_dir.glob("*.json"))
    total = len(json_files)
    clean = 0
    with_issues = 0
    issue_summary = {}

    log.info("Enriching %d %s files%s...", total, source_name,
             " (validate only)" if validate_only else "")

    for jf in json_files:
        try:
            issues = enrich_fn(jf, validate_only)
            if issues:
                with_issues += 1
                for issue in issues:
                    issue_type = issue.split(":")[0]
                    issue_summary[issue_type] = issue_summary.get(issue_type, 0) + 1
                if len(issues) <= 3:
                    log.debug("%s: %s", jf.name, "; ".join(issues))
            else:
                clean += 1
        except Exception as e:
            with_issues += 1
            issue_summary["ERROR"] = issue_summary.get("ERROR", 0) + 1
            log.error("Error enriching %s: %s", jf.name, e)

    log.info("%s: %d/%d clean, %d with issues", source_name, clean, total, with_issues)
    return {"total": total, "clean": clean, "with_issues": with_issues, "issue_summary": issue_summary}


def main():
    parser = argparse.ArgumentParser(description="Enrich and validate NOVA metadata")
    parser.add_argument("--source", choices=["ecfr", "fr", "sr", "all"], default="all",
                        help="Which source to enrich")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate, don't modify files")
    args = parser.parse_args()

    results = {}

    if args.source in ("ecfr", "all"):
        results["ecfr"] = run_enrichment(ECFR_DIR, enrich_ecfr_metadata, "eCFR", args.validate_only)
    else:
        results["ecfr"] = {"total": 0, "clean": 0, "with_issues": 0, "issue_summary": {}}

    if args.source in ("fr", "all"):
        results["fr"] = run_enrichment(FR_DIR, enrich_fr_metadata, "Federal Register", args.validate_only)
    else:
        results["fr"] = {"total": 0, "clean": 0, "with_issues": 0, "issue_summary": {}}

    if args.source in ("sr", "all"):
        results["sr"] = run_enrichment(SR_DIR, enrich_sr_metadata, "SR Letters", args.validate_only)
    else:
        results["sr"] = {"total": 0, "clean": 0, "with_issues": 0, "issue_summary": {}}

    report = audit_corpus(results["ecfr"], results["fr"], results["sr"])
    # Handle Windows console encoding
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n" + report)


if __name__ == "__main__":
    main()
