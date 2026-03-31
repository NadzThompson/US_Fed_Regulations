"""
Master runner — Executes the full NOVA scraping + enrichment pipeline.

Usage:
    python -m scrapers.run_all                         # Full pipeline
    python -m scrapers.run_all --skip-ecfr             # Skip eCFR
    python -m scrapers.run_all --skip-fr               # Skip Federal Register
    python -m scrapers.run_all --skip-sr               # Skip SR Letters
    python -m scrapers.run_all --enrich-only           # Just re-enrich existing files
    python -m scrapers.run_all --ecfr-date 2026-03-09  # Specific eCFR date
"""

import argparse
import logging
import sys
from datetime import date

from scrapers.scrape_ecfr import scrape_part, ECFR_PARTS
from scrapers.scrape_federal_register import fetch_documents, process_document
from scrapers.scrape_sr_letters import fetch_sr_index, fetch_sr_years_index, scrape_sr_letter
from scrapers.enrich_metadata import run_enrichment, enrich_ecfr_metadata, enrich_fr_metadata, enrich_sr_metadata, audit_corpus
from scrapers.config import ECFR_DIR, FR_DIR, SR_DIR, ECFR_DELAY_SECONDS, FR_DELAY_SECONDS, SR_DELAY_SECONDS, FR_CONDITIONS

import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def run_ecfr(as_of_date: str):
    """Scrape all eCFR parts."""
    log.info("═══ eCFR Scrape ═══")
    results = []
    for part in ECFR_PARTS:
        try:
            meta = scrape_part(part, as_of_date)
            results.append(meta)
        except Exception as e:
            log.error("Part %d failed: %s", part, e)
        time.sleep(ECFR_DELAY_SECONDS)

    log.info("eCFR: %d/%d parts scraped", len(results), len(ECFR_PARTS))
    return results


def run_federal_register(after_date: str | None = None):
    """Scrape all Federal Register document types."""
    log.info("═══ Federal Register Scrape ═══")
    total = 0
    for doc_type in ["final_rules", "proposed_rules", "notices"]:
        after = after_date
        if doc_type == "notices" and not after:
            after = "2023-01-01"

        raw_docs = fetch_documents(doc_type, after_date=after)
        log.info("Processing %d %s...", len(raw_docs), doc_type)
        for doc in raw_docs:
            try:
                process_document(doc)
                total += 1
            except Exception as e:
                log.error("FR doc %s failed: %s", doc.get("document_number", "?"), e)

    log.info("Federal Register: %d total documents", total)
    return total


def run_sr_letters():
    """Scrape all SR Letters."""
    log.info("═══ SR Letters Scrape ═══")
    all_letters = fetch_sr_index()
    if not all_letters:
        all_letters = fetch_sr_years_index()

    results = []
    for sr_info in all_letters:
        meta = scrape_sr_letter(sr_info)
        if meta:
            results.append(meta)
        time.sleep(SR_DELAY_SECONDS)

    log.info("SR Letters: %d/%d scraped", len(results), len(all_letters))
    return results


def run_enrichment_all(validate_only: bool = False):
    """Run enrichment across all sources."""
    log.info("═══ Metadata Enrichment ═══")
    ecfr_r = run_enrichment(ECFR_DIR, enrich_ecfr_metadata, "eCFR", validate_only)
    fr_r = run_enrichment(FR_DIR, enrich_fr_metadata, "Federal Register", validate_only)
    sr_r = run_enrichment(SR_DIR, enrich_sr_metadata, "SR Letters", validate_only)
    report = audit_corpus(ecfr_r, fr_r, sr_r)
    print("\n" + report)


def main():
    parser = argparse.ArgumentParser(description="Run the full NOVA scraping pipeline")
    parser.add_argument("--skip-ecfr", action="store_true")
    parser.add_argument("--skip-fr", action="store_true")
    parser.add_argument("--skip-sr", action="store_true")
    parser.add_argument("--enrich-only", action="store_true",
                        help="Skip scraping, just re-enrich existing metadata")
    parser.add_argument("--ecfr-date", type=str, default=None,
                        help="eCFR 'as of' date (default: today)")
    parser.add_argument("--fr-after", type=str, default=None,
                        help="Only fetch FR docs published after this date")
    parser.add_argument("--validate-only", action="store_true",
                        help="Enrichment: validate only, don't modify files")
    args = parser.parse_args()

    ecfr_date = args.ecfr_date or date.today().isoformat()

    if not args.enrich_only:
        if not args.skip_ecfr:
            run_ecfr(ecfr_date)
        if not args.skip_fr:
            run_federal_register(args.fr_after)
        if not args.skip_sr:
            run_sr_letters()

    # Always run enrichment at the end
    run_enrichment_all(args.validate_only)

    log.info("Pipeline complete.")


if __name__ == "__main__":
    main()
