"""
Unzip all content archives after cloning the repository.

Usage:
    python scrapers/unzip_content.py
"""

import os
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ARCHIVES = [
    # (zip_file, extract_to_directory)
    ("ecfr/ecfr_pdfs.zip", "ecfr/pdf"),
    ("SR_Letters/sr_letters_pdfs_part01.zip", "SR_Letters/pdf"),
    ("SR_Letters/sr_letters_pdfs_part02.zip", "SR_Letters/pdf"),
    ("federal_register/federal_register_raw_html.zip", "federal_register/raw_html"),
    ("federal_register/federal_register_html.zip", "federal_register/html"),
    ("federal_register/federal_register_md.zip", "federal_register/md"),
]

# Federal Register PDFs are split into multiple parts
for i in range(1, 20):
    part = f"federal_register/federal_register_pdfs_part{str(i).zfill(2)}.zip"
    if (REPO_ROOT / part).exists():
        ARCHIVES.append((part, "federal_register/pdf"))


def main():
    for zip_file, extract_dir in ARCHIVES:
        zip_path = REPO_ROOT / zip_file
        out_path = REPO_ROOT / extract_dir

        if not zip_path.exists():
            continue

        out_path.mkdir(parents=True, exist_ok=True)
        print(f"Extracting {zip_file} -> {extract_dir}/")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(out_path)
            print(f"  {len(zf.namelist())} files extracted")

    print("\nDone. All content archives extracted.")


if __name__ == "__main__":
    main()
