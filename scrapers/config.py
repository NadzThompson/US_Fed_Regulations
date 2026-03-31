"""
Shared configuration for all NOVA US Federal Regulations scrapers.
"""

import os
from pathlib import Path
from datetime import datetime

# ─── Base paths ───────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
ECFR_DIR = REPO_ROOT / "ecfr"
FR_DIR = REPO_ROOT / "federal_register"
SR_DIR = REPO_ROOT / "SR_Letters"

# ─── API endpoints ───────────────────────────────────────────────────────────
ECFR_API_BASE = "https://www.ecfr.gov/api/versioner/v1"
ECFR_FULL_XML_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{title}.xml"
ECFR_STRUCTURE_URL = "https://www.ecfr.gov/api/versioner/v1/structure/{date}/title-{title}.json"
ECFR_PART_URL = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/{date}/title-{title}"

FR_API_BASE = "https://www.federalregister.gov/api/v1"
FR_DOCUMENTS_URL = f"{FR_API_BASE}/documents.json"

SR_LISTING_URL = "https://www.federalreserve.gov/supervisionreg/srletters/srlettersbyyear.htm"
SR_LETTER_BASE = "https://www.federalreserve.gov/supervisionreg/srletters"

# ─── Scope: 12 CFR Chapter II (Federal Reserve System) ───────────────────────
CFR_TITLE = 12
CFR_CHAPTER = "II"

# All active parts under 12 CFR Chapter II
# Lettered regulations (A=201 through ZZ=253) plus administrative (250, 261-272, 281)
# Reserved parts excluded: 200, 203, 216, 230, 236
ECFR_PARTS = [
    201, 202, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215,
    217, 218, 219, 220, 221, 222, 223, 224, 225, 226, 228, 229, 231, 232,
    233, 234, 235, 237, 238, 239, 240, 241, 242, 243, 244, 246, 248, 249,
    250, 251, 252, 253,
    261, 262, 263, 264, 265, 266, 267, 268, 269, 270, 271, 272, 281,
]

# Mapping: part number → regulation letter (for lettered regs only)
PART_TO_REG_LETTER = {
    201: "A", 202: "B", 204: "D", 205: "E", 206: "F", 207: "G", 208: "H",
    209: "I", 210: "J", 211: "K", 212: "L", 213: "M", 214: "N", 215: "O",
    217: "Q", 218: "R", 219: "S", 220: "T", 221: "U", 222: "V", 223: "W",
    224: "X", 225: "Y", 226: "Z", 228: "BB", 229: "CC", 231: "EE", 232: "FF",
    233: "GG", 234: "HH", 235: "II", 237: "KK", 238: "LL", 239: "MM",
    240: "NN", 241: "OO", 242: "PP", 243: "QQ", 244: "RR", 246: "TT",
    248: "VV", 249: "WW", 251: "XX", 252: "YY", 253: "ZZ",
}

# ─── NOVA tier assignments ────────────────────────────────────────────────────
TIER_1_PARTS = {217, 225, 249, 252, 248, 238, 208}
TIER_3_PARTS = {207, 209, 212, 213, 214, 219, 224, 231, 232, 241, 242,
                261, 262, 263, 264, 265, 266, 267, 268, 269, 270, 271, 272, 281}
TIER_4_PARTS = {250}

def nova_tier_for_part(part_number: int) -> int:
    if part_number in TIER_1_PARTS:
        return 1
    if part_number in TIER_4_PARTS:
        return 4
    if part_number in TIER_3_PARTS:
        return 3
    return 2

# ─── Federal Register query parameters ───────────────────────────────────────
FR_AGENCY_IDS = [466]  # Federal Reserve System agency ID
FR_CONDITIONS = {
    "final_rules": {
        "type[]": "RULE",
        "agencies[]": "federal-reserve-system",
    },
    "proposed_rules": {
        "type[]": "PRORULE",
        "agencies[]": "federal-reserve-system",
    },
    "notices": {
        "type[]": "NOTICE",
        "agencies[]": "federal-reserve-system",
    },
}

# ─── Rate limiting ────────────────────────────────────────────────────────────
ECFR_DELAY_SECONDS = 1.0
FR_DELAY_SECONDS = 0.5
SR_DELAY_SECONDS = 1.5

# ─── Scraper metadata ────────────────────────────────────────────────────────
SCRAPE_DATE = datetime.utcnow().strftime("%Y-%m-%d")
PARSER_VERSIONS = {
    "ecfr": "nova-ecfr-scraper-v1",
    "fr": "nova-fr-api-v1",
    "sr": "nova-sr-scraper-v1",
}
ENRICHER_VERSIONS = {
    "ecfr": "nova-ecfr-enricher-v2",
    "fr": "nova-fr-enricher-v2",
    "sr": "nova-sr-enricher-v1",
}
