"""Unit tests for the pure transform functions in upload_pm_abstract_ftp.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from upload_pm_abstract_ftp import (  # noqa: E402
    _next_start_index,
    add_pubmed_url,
    add_recency_flags,
    add_top_journal_flag,
    clean_abstract_and_date,
    dedupe_by_pmid,
    filter_date_range,
    reorder_columns,
)


def _row(pmid, abstract="some abstract", publication_date="2022-01-01", journal_title="Nature"):
    return {
        "pmid": pmid,
        "title": "t",
        "journal_title": journal_title,
        "publication_date": publication_date,
        "abstract": abstract,
        "author_list": "",
        "author_list_full": "",
        "coi_statement": None,
        "coi_flag": 0,
        "pubmed_url": "",
    }


def test_clean_abstract_and_date_drops_missing_abstract():
    df = pd.DataFrame([_row("1", abstract=""), _row("2")])
    cleaned = clean_abstract_and_date(df)
    assert list(cleaned["pmid"]) == ["2"]


def test_clean_abstract_and_date_drops_unparsable_date():
    df = pd.DataFrame([_row("1", publication_date=None), _row("2")])
    cleaned = clean_abstract_and_date(df)
    assert list(cleaned["pmid"]) == ["2"]


def test_filter_date_range_is_inclusive():
    df = pd.DataFrame(
        [_row("1", publication_date="2020-01-01"), _row("2", publication_date="2020-06-01"), _row("3", publication_date="2021-01-01")]
    )
    df["publication_date"] = pd.to_datetime(df["publication_date"])
    filtered = filter_date_range(df, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-31"))
    assert set(filtered["pmid"]) == {"1", "2"}


def test_add_recency_flags():
    df = pd.DataFrame([_row("1", publication_date="2025-06-01"), _row("2", publication_date="2019-01-01")])
    df["publication_date"] = pd.to_datetime(df["publication_date"])
    flagged = add_recency_flags(df, reference_date=pd.Timestamp("2026-01-01"))
    row1 = flagged.set_index("pmid").loc["1"]
    row2 = flagged.set_index("pmid").loc["2"]
    assert bool(row1["is_last_year"]) is True
    assert bool(row1["is_last_5_years"]) is True
    assert bool(row2["is_last_year"]) is False
    assert bool(row2["is_last_5_years"]) is False


def test_add_top_journal_flag_without_list_defaults_false():
    df = pd.DataFrame([_row("1", journal_title="Nature")])
    flagged = add_top_journal_flag(df, top_journals=None)
    assert flagged["is_top_journal"].tolist() == [False]


def test_add_top_journal_flag_case_insensitive_match():
    df = pd.DataFrame([_row("1", journal_title="Nature"), _row("2", journal_title="Obscure Journal")])
    flagged = add_top_journal_flag(df, top_journals={"nature"})
    assert flagged.set_index("pmid").loc["1", "is_top_journal"] == True  # noqa: E712
    assert flagged.set_index("pmid").loc["2", "is_top_journal"] == False  # noqa: E712


def test_add_pubmed_url():
    df = pd.DataFrame([_row("12345")])
    result = add_pubmed_url(df)
    assert result.loc[0, "pubmed_url"] == "https://pubmed.ncbi.nlm.nih.gov/12345"


def test_dedupe_by_pmid_keeps_last():
    df = pd.DataFrame([_row("1", abstract="old"), _row("1", abstract="new")])
    deduped = dedupe_by_pmid(df)
    assert len(deduped) == 1
    assert deduped.iloc[0]["abstract"] == "new"


def test_reorder_columns_raises_on_missing_column():
    df = pd.DataFrame([_row("1")])  # missing is_last_year/is_last_5_years/is_top_journal
    with pytest.raises(ValueError):
        reorder_columns(df)


@pytest.mark.parametrize(
    "existing,stem,expected",
    [
        ([], "pubmed_data", 1),
        (["pubmed_data_00001.parquet"], "pubmed_data", 2),
        (["pubmed_data_00001.parquet", "pubmed_data_00002.parquet"], "pubmed_data", 3),
        (["pubmed_data_00001.parquet", "pubmed_data_00007.parquet"], "pubmed_data", 8),  # gaps use the true max
        (["other_stem_00099.parquet"], "pubmed_data", 1),  # different stem is ignored
    ],
)
def test_next_start_index(existing, stem, expected):
    assert _next_start_index(existing, stem) == expected
