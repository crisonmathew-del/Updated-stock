"""Industry groups and sectors from SEC SIC codes (spec §6.6), the keyless classification.

A 4-digit SIC code with at least `min_members` stocks becomes its own group, named with SEC's
title for the code (e.g. "Pharmaceutical Preparations"). Smaller codes roll up into their
2-digit major group. Sectors follow the 11 GICS-style sectors used by the SPDR sector ETFs: each
major group has a sector, with overrides for 4-digit ranges that belong elsewhere (software sits
in "business services" but is Information Technology; REITs sit in "holding offices" but are
Real Estate). Known limitation: big codes such as 7372 (prepackaged software) stay one group.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

ENERGY = "Energy"
MATERIALS = "Materials"
INDUSTRIALS = "Industrials"
DISCRETIONARY = "Consumer Discretionary"
STAPLES = "Consumer Staples"
HEALTH = "Health Care"
FINANCIALS = "Financials"
TECH = "Information Technology"
COMMUNICATION = "Communication Services"
UTILITIES = "Utilities"
REAL_ESTATE = "Real Estate"
UNCLASSIFIED = "Unclassified"

SECTOR_ETFS: dict[str, str] = {
    "XLB": MATERIALS,
    "XLC": COMMUNICATION,
    "XLE": ENERGY,
    "XLF": FINANCIALS,
    "XLI": INDUSTRIALS,
    "XLK": TECH,
    "XLP": STAPLES,
    "XLRE": REAL_ESTATE,
    "XLU": UTILITIES,
    "XLV": HEALTH,
    "XLY": DISCRETIONARY,
}

# SIC major groups (2-digit): title and sector.
MAJOR_GROUPS: dict[str, tuple[str, str]] = {
    "01": ("Agricultural Production - Crops", STAPLES),
    "02": ("Agricultural Production - Livestock", STAPLES),
    "07": ("Agricultural Services", STAPLES),
    "08": ("Forestry", MATERIALS),
    "09": ("Fishing, Hunting & Trapping", STAPLES),
    "10": ("Metal Mining", MATERIALS),
    "12": ("Coal Mining", ENERGY),
    "13": ("Oil & Gas Extraction", ENERGY),
    "14": ("Nonmetallic Minerals Mining", MATERIALS),
    "15": ("Building Construction", INDUSTRIALS),
    "16": ("Heavy Construction", INDUSTRIALS),
    "17": ("Construction - Special Trade Contractors", INDUSTRIALS),
    "20": ("Food & Kindred Products", STAPLES),
    "21": ("Tobacco Products", STAPLES),
    "22": ("Textile Mill Products", DISCRETIONARY),
    "23": ("Apparel & Finished Fabric Products", DISCRETIONARY),
    "24": ("Lumber & Wood Products", MATERIALS),
    "25": ("Furniture & Fixtures", DISCRETIONARY),
    "26": ("Paper & Allied Products", MATERIALS),
    "27": ("Printing & Publishing", COMMUNICATION),
    "28": ("Chemicals & Allied Products", MATERIALS),
    "29": ("Petroleum Refining", ENERGY),
    "30": ("Rubber & Plastics Products", MATERIALS),
    "31": ("Leather Products", DISCRETIONARY),
    "32": ("Stone, Clay, Glass & Concrete", MATERIALS),
    "33": ("Primary Metal Industries", MATERIALS),
    "34": ("Fabricated Metal Products", INDUSTRIALS),
    "35": ("Industrial Machinery & Computer Equipment", INDUSTRIALS),
    "36": ("Electronic & Electrical Equipment", TECH),
    "37": ("Transportation Equipment", INDUSTRIALS),
    "38": ("Instruments & Medical Goods", TECH),
    "39": ("Miscellaneous Manufacturing", DISCRETIONARY),
    "40": ("Railroad Transportation", INDUSTRIALS),
    "41": ("Local & Suburban Transit", INDUSTRIALS),
    "42": ("Trucking & Warehousing", INDUSTRIALS),
    "43": ("Postal Service", INDUSTRIALS),
    "44": ("Water Transportation", INDUSTRIALS),
    "45": ("Air Transportation", INDUSTRIALS),
    "46": ("Pipelines (except Natural Gas)", ENERGY),
    "47": ("Transportation Services", INDUSTRIALS),
    "48": ("Communications", COMMUNICATION),
    "49": ("Electric, Gas & Sanitary Services", UTILITIES),
    "50": ("Wholesale - Durable Goods", INDUSTRIALS),
    "51": ("Wholesale - Nondurable Goods", STAPLES),
    "52": ("Building Materials & Garden Retail", DISCRETIONARY),
    "53": ("General Merchandise Stores", DISCRETIONARY),
    "54": ("Food Stores", STAPLES),
    "55": ("Auto Dealers & Gas Stations", DISCRETIONARY),
    "56": ("Apparel & Accessory Stores", DISCRETIONARY),
    "57": ("Home Furniture & Equipment Stores", DISCRETIONARY),
    "58": ("Restaurants", DISCRETIONARY),
    "59": ("Miscellaneous Retail", DISCRETIONARY),
    "60": ("Depository Institutions", FINANCIALS),
    "61": ("Nondepository Credit Institutions", FINANCIALS),
    "62": ("Security & Commodity Brokers", FINANCIALS),
    "63": ("Insurance Carriers", FINANCIALS),
    "64": ("Insurance Agents & Brokers", FINANCIALS),
    "65": ("Real Estate", REAL_ESTATE),
    "67": ("Holding & Investment Offices", FINANCIALS),
    "70": ("Hotels & Lodging", DISCRETIONARY),
    "72": ("Personal Services", DISCRETIONARY),
    "73": ("Business Services", INDUSTRIALS),
    "75": ("Auto Repair, Services & Parking", DISCRETIONARY),
    "76": ("Miscellaneous Repair Services", INDUSTRIALS),
    "78": ("Motion Pictures", COMMUNICATION),
    "79": ("Amusement & Recreation Services", DISCRETIONARY),
    "80": ("Health Services", HEALTH),
    "81": ("Legal Services", INDUSTRIALS),
    "82": ("Educational Services", DISCRETIONARY),
    "83": ("Social Services", HEALTH),
    "84": ("Museums & Gardens", DISCRETIONARY),
    "86": ("Membership Organizations", INDUSTRIALS),
    "87": ("Engineering, Research & Management Services", INDUSTRIALS),
    "88": ("Private Households", DISCRETIONARY),
    "89": ("Services, Not Elsewhere Classified", INDUSTRIALS),
    "99": ("Nonclassifiable Establishments", UNCLASSIFIED),
}

# 4-digit ranges (inclusive) whose sector differs from their major group's.
SECTOR_OVERRIDES: tuple[tuple[int, int, str], ...] = (
    (1520, 1531, DISCRETIONARY),  # homebuilders
    (2833, 2836, HEALTH),  # drugs, biologicals, diagnostics
    (2840, 2844, STAPLES),  # soap, detergents, cosmetics
    (3570, 3579, TECH),  # computers and office equipment
    (3600, 3629, INDUSTRIALS),  # electrical distribution and industrial apparatus
    (3630, 3652, DISCRETIONARY),  # household appliances, audio/video equipment
    (3690, 3699, INDUSTRIALS),  # miscellaneous electrical machinery
    (3711, 3711, DISCRETIONARY),  # motor vehicles
    (3714, 3714, DISCRETIONARY),  # motor vehicle parts
    (3716, 3716, DISCRETIONARY),  # motor homes
    (3751, 3751, DISCRETIONARY),  # motorcycles and bicycles
    (3841, 3851, HEALTH),  # medical, surgical, dental and ophthalmic goods
    (4922, 4923, ENERGY),  # natural gas transmission (pipelines)
    (4950, 4959, INDUSTRIALS),  # waste management
    (5045, 5045, TECH),  # computer wholesale
    (5047, 5047, HEALTH),  # medical equipment wholesale
    (5122, 5122, HEALTH),  # drug wholesale
    (5171, 5172, ENERGY),  # petroleum wholesale
    (5331, 5331, STAPLES),  # variety stores / warehouse clubs
    (5912, 5912, STAPLES),  # drug stores
    (6798, 6798, REAL_ESTATE),  # REITs
    (7310, 7319, COMMUNICATION),  # advertising
    (7370, 7379, TECH),  # software and IT services
    (8731, 8731, HEALTH),  # commercial biological research
)


def sector_for(sic: str | None) -> str:
    if not sic or not sic.isdigit():
        return UNCLASSIFIED
    code = int(sic)
    for low, high, sector in SECTOR_OVERRIDES:
        if low <= code <= high:
            return sector
    return MAJOR_GROUPS.get(sic[:2], ("", UNCLASSIFIED))[1]


@dataclass(frozen=True)
class GroupDef:
    code: str
    name: str
    sector: str
    sic_level: int


@dataclass(frozen=True)
class TickerSic:
    ticker_id: int
    sic_code: str | None
    sic_description: str | None


def assign_groups(
    tickers: Iterable[TickerSic], min_members: int
) -> tuple[dict[str, GroupDef], dict[int, str]]:
    """Returns ({group code: definition}, {ticker_id: group code}). Tickers without a usable
    SIC code get no group."""
    usable = [t for t in tickers if t.sic_code and t.sic_code.isdigit() and len(t.sic_code) == 4]
    counts = Counter(t.sic_code for t in usable)
    titles: dict[str, Counter[str]] = {}
    for t in usable:
        if t.sic_description:
            titles.setdefault(str(t.sic_code), Counter())[t.sic_description] += 1

    split_majors = {str(sic)[:2] for sic, n in counts.items() if n >= min_members}
    groups: dict[str, GroupDef] = {}
    membership: dict[int, str] = {}
    for t in usable:
        sic = str(t.sic_code)
        if counts[sic] >= min_members:
            code = f"SIC{sic}"
            if code not in groups:
                title = titles[sic].most_common(1)[0][0] if sic in titles else f"SIC {sic}"
                groups[code] = GroupDef(code, title, sector_for(sic), 4)
        else:
            major = sic[:2]
            code = f"SIC{major}"
            if code not in groups:
                name, sector = MAJOR_GROUPS.get(major, (f"SIC {major}", UNCLASSIFIED))
                if major in split_majors:
                    name = f"{name} (other)"
                groups[code] = GroupDef(code, name, sector, 2)
        membership[t.ticker_id] = code
    return groups, membership
