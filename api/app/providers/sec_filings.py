"""SEC filing history, the EDGAR daily index and insider transactions (Form 4). Pure parsers.

- Submissions (`data.sec.gov/submissions/CIK##########.json`) list a company's filings as
  parallel arrays: the latest ~1,000 under `filings.recent`, older ones in extra pages named in
  `filings.files` that have the same columns.
- The daily master index (`Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx`) lists
  every filing accepted that day, pipe-delimited. A Form 4 appears once per filer, i.e. under
  the issuer's CIK and under each reporting owner's.
- Form 4 is an XML `ownershipDocument` inside the full submission text file.
- SEC's quarterly insider data sets are zipped TSV tables keyed by accession number.
"""

import io
import re
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from typing import Any
from xml.etree import ElementTree

import polars as pl

from app.core.calendar import MARKET_TZ
from app.providers.base import EarningsRelease, FilingRecord, IndexEntry, InsiderTransaction

EARNINGS_ITEM = "2.02"  # 8-K item 2.02: results of operations and financial condition
EARNINGS_FORMS = frozenset({"8-K"})
OPEN_MARKET_CODES = frozenset({"P", "S"})  # open-market purchase / sale
FORM4_TYPES = frozenset({"4", "4/A"})
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def pad_cik(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def _date_or_none(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_acceptance(value: object) -> datetime | None:
    """EDGAR acceptance time. The submissions API writes it with a trailing "Z", but the clock is
    US/Eastern (it matches the ACCEPTANCE-DATETIME header of the filing itself), so the zone
    marker is ignored and Eastern attached."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None
    return parsed.replace(tzinfo=MARKET_TZ)


def parse_filing_columns(columns: Mapping[str, Sequence[Any]]) -> list[FilingRecord]:
    """One page of filings in the submissions API's columnar layout."""
    accessions = columns.get("accessionNumber") or []
    n = len(accessions)

    def col(name: str) -> Sequence[Any]:
        values = columns.get(name)
        return values if values is not None and len(values) == n else [None] * n

    forms, filed, accepted = col("form"), col("filingDate"), col("acceptanceDateTime")
    reports, items, docs = col("reportDate"), col("items"), col("primaryDocument")
    out: list[FilingRecord] = []
    for i in range(n):
        filed_on = _date_or_none(filed[i])
        if filed_on is None or not forms[i]:
            continue
        out.append(
            FilingRecord(
                accession=str(accessions[i]),
                form=str(forms[i]),
                filed=filed_on,
                accepted_at=parse_acceptance(accepted[i]),
                report_date=_date_or_none(reports[i]),
                items=tuple(x.strip() for x in str(items[i] or "").split(",") if x.strip()),
                primary_document=docs[i] or None,
            )
        )
    return out


def extra_pages(submissions: Mapping[str, Any], since: date | None = None) -> list[str]:
    """File names of the older filing pages (e.g. CIK0000320193-submissions-001.json) that
    reach back to `since` or later."""
    names = []
    for f in submissions.get("filings", {}).get("files") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        last = _date_or_none(f.get("filingTo"))
        if since is None or last is None or last >= since:
            names.append(str(f["name"]))
    return names


def parse_filing_history(
    submissions: Mapping[str, Any], pages: Iterable[Mapping[str, Sequence[Any]]] = ()
) -> list[FilingRecord]:
    """Every filing from the recent block and the extra pages, oldest first, deduplicated."""
    records = parse_filing_columns(submissions.get("filings", {}).get("recent") or {})
    for page in pages:
        records.extend(parse_filing_columns(page))
    unique = {r.accession: r for r in records}
    return sorted(unique.values(), key=lambda r: (r.filed, r.accession))


def is_earnings_release(filing: FilingRecord) -> bool:
    return filing.form in EARNINGS_FORMS and EARNINGS_ITEM in filing.items


def release_timing(accepted_at: datetime | None) -> str:
    """When a results release reached the market, relative to the regular session."""
    if accepted_at is None:
        return "unknown"
    clock = accepted_at.astimezone(MARKET_TZ).time()
    if clock < MARKET_OPEN:
        return "before_open"
    if clock >= MARKET_CLOSE:
        return "after_close"
    return "during_session"


def earnings_releases(filings: Iterable[FilingRecord]) -> list[EarningsRelease]:
    """One release per day from the 8-K item 2.02 filings, oldest first."""
    by_day: dict[date, EarningsRelease] = {}
    for f in sorted(filings, key=lambda f: (f.filed, f.accession)):
        if is_earnings_release(f):
            release = EarningsRelease(f.filed, release_timing(f.accepted_at), f.accession)
            by_day.setdefault(f.filed, release)
    return list(by_day.values())


# --- Daily index ----------------------------------------------------------------------------


def parse_daily_index(text: str) -> list[IndexEntry]:
    """Rows of a master index: CIK|Company Name|Form Type|Date Filed|File Name."""
    out: list[IndexEntry] = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue
        cik, company, form, filed, path = (p.strip() for p in parts)
        filed_on = _parse_index_date(filed)
        if filed_on is None:
            continue
        out.append(IndexEntry(pad_cik(cik), company, form, filed_on, path))
    return out


def _parse_index_date(value: str) -> date | None:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def accession_from_path(path: str) -> str:
    """edgar/data/320193/0000320193-24-000081.txt → 0000320193-24-000081"""
    return path.rsplit("/", 1)[-1].removesuffix(".txt")


# --- Form 4 ---------------------------------------------------------------------------------

_OWNERSHIP_DOC = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.DOTALL)


def _text(node: ElementTree.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true"}


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def describe_role(
    *, director: bool, officer: bool, ten_percent: bool, title: str | None, other: str | None
) -> str:
    if officer and title:
        return title
    parts = [
        name
        for flag, name in ((officer, "Officer"), (director, "Director"), (ten_percent, "10% owner"))
        if flag
    ]
    return ", ".join(parts) or other or "Other"


def parse_form4(text: str, *, accession: str, filed: date) -> list[InsiderTransaction]:
    """Open-market purchases and sales of non-derivative securities in one Form 4. The insider
    is the first reporting owner (joint filers report the same trades)."""
    match = _OWNERSHIP_DOC.search(text)
    if match is None:
        return []
    try:
        root = ElementTree.fromstring(match.group(0))
    except ElementTree.ParseError:
        return []
    if (_text(root, "documentType") or "") not in FORM4_TYPES:
        return []
    issuer_cik = _text(root, "issuer/issuerCik")
    owner = root.find("reportingOwner")
    owner_cik = _text(owner, "reportingOwnerId/rptOwnerCik")
    if issuer_cik is None or owner is None or owner_cik is None:
        return []
    relation = owner.find("reportingOwnerRelationship")
    director = _flag(_text(relation, "isDirector"))
    officer = _flag(_text(relation, "isOfficer"))
    ten_percent = _flag(_text(relation, "isTenPercentOwner"))
    role = describe_role(
        director=director,
        officer=officer,
        ten_percent=ten_percent,
        title=_text(relation, "officerTitle"),
        other=_text(relation, "otherText"),
    )
    out: list[InsiderTransaction] = []
    for seq, tx in enumerate(root.iterfind("nonDerivativeTable/nonDerivativeTransaction"), 1):
        code = _text(tx, "transactionCoding/transactionCode")
        day = _date_or_none(_text(tx, "transactionDate/value"))
        shares = _float(_text(tx, "transactionAmounts/transactionShares/value"))
        if code not in OPEN_MARKET_CODES or day is None or not shares:
            continue
        out.append(
            InsiderTransaction(
                accession=accession,
                seq=seq,
                issuer_cik=pad_cik(issuer_cik),
                filed=filed,
                transaction_date=day,
                insider_cik=pad_cik(owner_cik),
                insider_name=_text(owner, "reportingOwnerId/rptOwnerName") or "",
                role=role,
                is_director=director,
                is_officer=officer,
                is_ten_percent_owner=ten_percent,
                code=code,
                shares=shares,
                price=_float(_text(tx, "transactionAmounts/transactionPricePerShare/value")),
            )
        )
    return out


# --- Quarterly insider data sets ------------------------------------------------------------


def _read_table(archive: zipfile.ZipFile, name: str) -> pl.DataFrame:
    member = next((m for m in archive.namelist() if m.rsplit("/", 1)[-1].upper() == name), None)
    if member is None:
        raise ValueError(f"{name} is missing from the insider data set")
    return pl.read_csv(
        archive.read(member),
        separator="\t",
        quote_char=None,
        infer_schema=False,
        truncate_ragged_lines=True,
    )


def _dataset_date(column: str) -> pl.Expr:
    """Dates are written like 02-JAN-2024 (older sets) or 2024-01-02."""
    text = pl.col(column).str.strip_chars().str.to_titlecase()
    return pl.coalesce(
        text.str.to_date("%d-%b-%Y", strict=False), text.str.to_date("%Y-%m-%d", strict=False)
    )


def parse_insider_dataset(data: bytes) -> list[InsiderTransaction]:
    """Open-market purchases and sales from one quarter's Form 3/4/5 data set."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        submissions = _read_table(archive, "SUBMISSION.TSV")
        owners = _read_table(archive, "REPORTINGOWNER.TSV")
        trades = _read_table(archive, "NONDERIV_TRANS.TSV")

    submissions = submissions.filter(pl.col("DOCUMENT_TYPE").is_in(list(FORM4_TYPES))).select(
        "ACCESSION_NUMBER", _dataset_date("FILING_DATE").alias("filed"), "ISSUERCIK"
    )
    relationship = pl.col("RPTOWNER_RELATIONSHIP").fill_null("").str.to_lowercase()
    owners = (
        owners.group_by("ACCESSION_NUMBER", maintain_order=True)
        .first()
        .select(
            "ACCESSION_NUMBER",
            "RPTOWNERCIK",
            "RPTOWNERNAME",
            pl.col("RPTOWNER_TITLE").alias("title"),
            relationship.str.contains("director").alias("is_director"),
            relationship.str.contains("officer").alias("is_officer"),
            relationship.str.contains("tenpercent|10%|ten percent").alias("is_ten_percent"),
        )
    )
    # Number the transactions in filing order (the row key), as the Form 4 reader does.
    trades = (
        trades.with_columns(pl.col("NONDERIV_TRANS_SK").cast(pl.Int64, strict=False).alias("sk"))
        .with_columns(
            pl.col("sk").rank("ordinal").over("ACCESSION_NUMBER").cast(pl.Int64).alias("seq")
        )
        .filter(pl.col("TRANS_CODE").is_in(list(OPEN_MARKET_CODES)))
        .with_columns(
            _dataset_date("TRANS_DATE").alias("transaction_date"),
            pl.col("TRANS_SHARES").cast(pl.Float64, strict=False).alias("shares"),
            pl.col("TRANS_PRICEPERSHARE").cast(pl.Float64, strict=False).alias("price"),
        )
    )
    joined = (
        trades.join(submissions, on="ACCESSION_NUMBER", how="inner")
        .join(owners, on="ACCESSION_NUMBER", how="inner")
        .filter(
            pl.col("filed").is_not_null()
            & pl.col("transaction_date").is_not_null()
            & (pl.col("shares") > 0)
            & pl.col("ISSUERCIK").str.strip_chars().str.contains(r"^\d+$")
            & pl.col("RPTOWNERCIK").str.strip_chars().str.contains(r"^\d+$")
        )
    )
    out: list[InsiderTransaction] = []
    for row in joined.iter_rows(named=True):
        out.append(
            InsiderTransaction(
                accession=row["ACCESSION_NUMBER"],
                seq=row["seq"],
                issuer_cik=pad_cik(row["ISSUERCIK"].strip()),
                filed=row["filed"],
                transaction_date=row["transaction_date"],
                insider_cik=pad_cik(row["RPTOWNERCIK"].strip()),
                insider_name=row["RPTOWNERNAME"] or "",
                role=describe_role(
                    director=row["is_director"],
                    officer=row["is_officer"],
                    ten_percent=row["is_ten_percent"],
                    title=row["title"],
                    other=None,
                ),
                is_director=row["is_director"],
                is_officer=row["is_officer"],
                is_ten_percent_owner=row["is_ten_percent"],
                code=row["TRANS_CODE"],
                shares=row["shares"],
                price=row["price"],
            )
        )
    return out
