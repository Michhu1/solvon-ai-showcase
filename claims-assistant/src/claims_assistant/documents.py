"""Loads the policy documents and splits them into clause-sized chunks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("CLAIMS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
POLICY_DIR = DATA_DIR / "policies"

_HEADING = re.compile(r"^### (?P<clause>[A-Z]{3}-\d+\.\d+) (?P<title>.+)$", re.MULTILINE)
_PRODUCT_BY_PREFIX = {"MOT": "motor", "HOM": "home", "TRV": "travel"}


@dataclass(frozen=True)
class Chunk:
    clause: str  # e.g. "MOT-2.1" - also the citation id
    product: str
    document: str
    section: str
    text: str


def split_document(markdown: str) -> list[Chunk]:
    """One chunk per clause: clauses are short and self-contained, so they make natural retrieval units."""
    document = markdown.splitlines()[0].lstrip("# ").strip()
    headings = list(_HEADING.finditer(markdown))
    chunks = []
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
        body = " ".join(markdown[match.end() : end].split())
        clause = match["clause"]
        chunks.append(
            Chunk(
                clause=clause,
                product=_PRODUCT_BY_PREFIX[clause[:3]],
                document=document,
                section=match["title"].strip(),
                text=body,
            )
        )
    return chunks


def load_chunks(directory: Path = POLICY_DIR) -> list[Chunk]:
    return [chunk for path in sorted(directory.glob("*.md")) for chunk in split_document(path.read_text("utf-8"))]
