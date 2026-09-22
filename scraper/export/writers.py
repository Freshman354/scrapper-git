"""Export eligibility + writers. See scraper_module_contracts.md S11.

build_eligible_list applies the live suppression check on top of what
storage already filtered to verification_status='verified' — storage
never talks to the outreach system itself, this is where that happens.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from scraper.domain.types import EligibleContact, ExportFilters


def build_eligible_list(filters: ExportFilters, suppression_client, repository) -> List[EligibleContact]:
    candidates = repository.get_eligible_contacts(filters)
    return [c for c in candidates if not suppression_client.is_suppressed(c.email)]


def write_csv_export(contacts: List[EligibleContact], fmt: str, output_dir: str = "/tmp") -> str:
    if fmt != "csv":
        raise ValueError(f"Only csv is implemented for V1, got fmt={fmt!r}")

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    final_path = Path(output_dir) / "scraper_export.csv"
    temp_path = Path(output_dir) / "scraper_export.csv.tmp"  # file-first: write temp, then move into place

    with open(temp_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["business_id", "contact_id", "email", "verification_status"])
        for c in contacts:
            writer.writerow([c.business_id, c.contact_id, c.email, c.verification_status])

    temp_path.replace(final_path)
    return str(final_path)
