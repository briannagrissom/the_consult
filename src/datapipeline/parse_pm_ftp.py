"""Parse PubMed baseline XML files into pickled pandas DataFrames.

The script scans an input directory (defaulting to
``outputs/pubmed_baseline_ftp_extract``) for files that match the PubMed
baseline naming convention (``pubmedNNnXXXX.xml``), filters them by their index
number, extracts a small set of metadata for each article, and serialises the
resulting table to ``outputs/`` as ``<stem>.pkl``. Existing pickle files are
skipped unless ``--force`` is supplied.

Example
-------
Convert every XML file whose index number exceeds 400::

    uv run python parse_pm_ftp.py --min-index 400

Process a specific directory while overwriting any prior pickle files::

    uv run python parse_pm_ftp.py --xml-dir data/baseline --force
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

import pandas as pd
from lxml import etree


LOGGER = logging.getLogger("parse_pm_ftp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_XML_DIR = Path("outputs/pubmed_baseline_ftp_extract")
DEFAULT_OUTPUT_DIR = Path("outputs/pubmed_baseline_ftp_parsed")
DEFAULT_MAX_WORKERS = max(1, (os.cpu_count() or 2) // 2)
FILENAME_RE = re.compile(r"n(?P<index>\d+)")


def extract_index_from_name(name: str) -> int:
    match = FILENAME_RE.search(name)
    if not match:
        raise ValueError(f"Cannot determine index from filename: {name}")
    return int(match.group("index"))


def iter_pubmed_articles(xml_path: Path) -> Iterator[etree._Element]:
    context = etree.iterparse(str(xml_path), events=("end",), tag="PubmedArticle")
    for _event, elem in context:
        yield elem
        elem.clear()
        parent = elem.getparent()
        if parent is not None:
            while elem.getprevious() is not None:
                del parent[0]
    del context


def parse_article(article_xml: etree._Element) -> dict:
    medline = article_xml.find("MedlineCitation")
    if medline is None:
        return {}

    article = medline.find("Article")
    pmid = medline.findtext("PMID")
    title = article.findtext("ArticleTitle") if article is not None else None
    journal = article.find("Journal") if article is not None else None
    journal_title = journal.findtext("Title") if journal is not None else None

    pubmed_url = "https://pubmed.ncbi.nlm.nih.gov/" + pmid

    coi_statement = article_xml.findtext(".//CoiStatement") if article is not None else None

    NO_COI_KEYWORDS = [
        "no competing interest",
        "no conflict of interest",
        "no conflicts of interest",
        "none declared",
        "declare no competing",
        "declare that they have no",
        "declare no conflict",
        "nothing to disclose",
        "no financial relationship",
        "absence of any commercial or financial relationship",
        "none to declare",
        "no relevant conflict",
        "no known competing",
    ]

    coi_flag = 0
    if coi_statement is not None and coi_statement.strip():
        coi_flag = 1
        coi_lower = coi_statement.lower()
        for keyword in NO_COI_KEYWORDS:
            if keyword in coi_lower:
                coi_flag = 0
                break

    pub_date = journal.find("JournalIssue/PubDate") if journal is not None else None
    if pub_date is not None:
        year = pub_date.findtext("Year")
        month = pub_date.findtext("Month")
        day = pub_date.findtext("Day")
        parts = [part for part in (year, month, day) if part]
        publication_date = "-".join(parts) if parts else None
    else:
        publication_date = None

    abstract_elem = article.find("Abstract") if article is not None else None
    if abstract_elem is not None:
        fragments = [fragment.strip() for fragment in abstract_elem.xpath(".//AbstractText/text()")]
        abstract = " ".join(fragment for fragment in fragments if fragment)
    else:
        abstract = None

    MAX_AUTHORS = 6

    formatted_authors = []
    author_list_str = ""
    author_list_full_str = ""

    author_list_xml = article.find(".//AuthorList")
    author_list = author_list_xml.findall("./Author") if author_list_xml is not None else []

    is_author_complete = author_list_xml.get("CompleteYN") == "Y" if author_list_xml is not None else False

    for author in author_list:
        last_name = author.findtext("LastName") or ""
        first_name = author.findtext("ForeName") or ""
        initials = author.findtext("Initials") or ""
        affil = author.findtext(".//AffiliationInfo/Affiliation") or ""

        if last_name and initials:
            formatted_authors.append(f"{last_name} {initials}")
        author_list_full_str += f"{first_name} {last_name} ({affil}); "

    num_authors = len(formatted_authors)
    if num_authors > 0:
        if num_authors > MAX_AUTHORS:
            author_list_str = ", ".join(formatted_authors[:MAX_AUTHORS]) + " et al."
        elif not is_author_complete:
            author_list_str = ", ".join(formatted_authors) + " et al."
        else:
            author_list_str = ", ".join(formatted_authors)

    return {
        "pmid": pmid,
        "title": title,
        "journal_title": journal_title,
        "publication_date": publication_date,
        "abstract": abstract if abstract else None,
        "author_list": author_list_str,
        "author_list_full": author_list_full_str.rstrip("; "),
        "coi_statement": coi_statement,
        "coi_flag": coi_flag,
        "pubmed_url": pubmed_url,
    }


def parse_file(xml_path: Path) -> pd.DataFrame:
    records: List[dict] = []
    for article in iter_pubmed_articles(xml_path):
        record = parse_article(article)
        if record and record.get("pmid"):
            records.append(record)
    if not records:
        LOGGER.warning("No articles parsed from %s", xml_path)
        return pd.DataFrame(columns=["pmid", "title", "journal_title", "publication_date", "abstract"])
    return pd.DataFrame(records)


def _parse_and_pickle(xml_path: Path, output_path: Path) -> Tuple[str, int]:
    df = parse_file(xml_path)
    if df.empty:
        return (xml_path.name, 0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(output_path)
    return (xml_path.name, int(len(df)))


def process_files(
    xml_dir: Path,
    output_dir: Path,
    min_index: int,
    force: bool,
    limit: int | None,
    max_workers: int,
) -> None:
    xml_files = sorted(xml_dir.glob("*.xml"))
    if not xml_files:
        LOGGER.error("No XML files found in %s", xml_dir)
        return

    jobs: List[Tuple[Path, Path]] = []
    for xml_file in xml_files:
        try:
            file_index = extract_index_from_name(xml_file.stem)
        except ValueError as exc:
            LOGGER.warning(str(exc))
            continue

        if file_index <= min_index:
            continue

        output_path = output_dir / f"{xml_file.stem}.pkl"
        if output_path.exists() and not force:
            LOGGER.info("Skipping %s; pickle already exists", output_path)
            continue

        jobs.append((xml_file, output_path))

        if limit is not None and len(jobs) >= limit:
            LOGGER.info("Reached processing limit of %s files", limit)
            break

    if not jobs:
        LOGGER.info("No files matched the criteria (min index %s)", min_index)
        return

    LOGGER.info("Processing %s files with %s workers", len(jobs), max_workers)

    processed = 0
    output_dir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_parse_and_pickle, xml_path, output_path): (
                xml_path,
                output_path,
            )
            for xml_path, output_path in jobs
        }
        for future in as_completed(futures):
            xml_path, output_path = futures[future]
            try:
                file_name, row_count = future.result()
            except Exception as exc:
                LOGGER.error("Failed to process %s: %s", xml_path, exc)
                continue

            if row_count == 0:
                LOGGER.warning("No records parsed from %s", file_name)
                continue

            LOGGER.info("Wrote %s rows to %s", row_count, output_path)
            processed += 1

    if processed == 0:
        LOGGER.info("Finished processing but no rows were written (min index %s)", min_index)


def parse_arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xml-dir",
        type=Path,
        default=DEFAULT_XML_DIR,
        help="Directory containing PubMed XML files (default: outputs/pubmed_baseline_ftp_extract)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where pickled outputs will be stored (default: outputs)",
    )
    parser.add_argument(
        "--min-index",
        type=int,
        default=400,
        help="Process files with numeric index greater than this value (default: 400)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing pickle files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of files to process",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Number of parallel workers to use (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:  # pragma: no cover - CLI wrapper
    args = parse_arguments(argv)
    xml_dir = args.xml_dir
    output_dir = args.output_dir
    if not xml_dir.exists():
        LOGGER.error("XML directory does not exist: %s", xml_dir)
        return
    process_files(xml_dir, output_dir, args.min_index, args.force, args.limit, args.max_workers)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
