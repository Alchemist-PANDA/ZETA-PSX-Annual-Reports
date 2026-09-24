"""Pakistan company universe — curated seed list of PSX-listed companies.

Contains a representative set of Pakistani listed companies across sectors.
This seed list is used for initial resolution; the system learns and
persists source profiles for each company after first successful discovery.
"""

import csv
from pathlib import Path
from .company import PakistanCompany
from .identity import normalize_ticker
from .fiscal_year import build_expected_company_years, FiscalPeriod

_HISTORICAL_CSV_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "reference" / "psx_historical_universe.csv"


def load_historical_universe(csv_path: Path | None = None) -> list[PakistanCompany]:
    """Load Pakistan companies from authoritative historical reference CSV."""
    path = csv_path or _HISTORICAL_CSV_PATH
    if not path.is_file():
        return get_seed_universe()

    companies: list[PakistanCompany] = []
    try:
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = normalize_ticker(row.get("current_symbol", ""))
                former_names = [n.strip() for n in row.get("former_company_names", "").split("|") if n.strip()]
                former_syms = [s.strip() for s in row.get("former_symbols", "").split("|") if s.strip()]
                aliases = list(set([symbol] + former_syms + [row.get("current_company_name", "")] + former_names))

                comp = PakistanCompany(
                    company_name=row.get("current_company_name", ""),
                    psx_symbol=symbol,
                    official_website=row.get("official_website", ""),
                    isin=row.get("isin", ""),
                    lei=row.get("lei", ""),
                    secp_registration=row.get("secp_registration", ""),
                    listing_date=row.get("listing_date", ""),
                    delisting_date=row.get("delisting_date", ""),
                    fiscal_year_end=row.get("fiscal_year_end", "December 31"),
                    historical_names=former_names,
                    historical_symbols=former_syms,
                    aliases=aliases,
                    sector=row.get("sector", ""),
                    identity_status="RESOLVED",
                    source_confidence="HIGH",
                )
                companies.append(comp)
    except Exception:
        return get_seed_universe()

    return companies or get_seed_universe()


def resolve_companies(queries: list[str]) -> list[PakistanCompany]:
    """Resolve user-provided company names/tickers to PakistanCompany objects.

    Accepts ticker symbols (e.g. ``HBL``), full names (e.g. ``Habib Bank``),
    former names (e.g. ``Summit Bank``), or mixed lists.
    Returns matched companies from the historical universe.
    Unmatched queries are returned with identity_status='REVIEW'.
    """
    universe = load_historical_universe()
    results: list[PakistanCompany] = []
    for query in queries:
        matched = False
        for company in universe:
            if company.matches_query(query):
                results.append(company)
                matched = True
                break
        if not matched:
            # Create a stub company for unresolved queries
            symbol = normalize_ticker(query)
            if len(symbol) > 10:
                symbol = symbol[:10]
            results.append(PakistanCompany(
                company_name=query.strip(),
                psx_symbol=symbol,
                identity_status="REVIEW",
                source_confidence="LOW",
            ))
    return results


def get_seed_universe() -> list[PakistanCompany]:
    """Return the built-in seed universe of major PSX companies."""
    return [c for c in _SEED_COMPANIES]


# ── Seed universe ─────────────────────────────────────────────────────────
# Major PSX-listed companies across diverse sectors.
# ISINs and LEIs included only where independently verified.
# fiscal_year_end uses the most common period ending.

_SEED_COMPANIES: list[PakistanCompany] = [
    # ── Banking ───────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Habib Bank Limited",
        psx_symbol="HBL",
        official_website="https://www.hbl.com",
        isin="PK0078801017",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["HBL"],
    ),
    PakistanCompany(
        company_name="United Bank Limited",
        psx_symbol="UBL",
        official_website="https://www.ubldirect.com",
        isin="PK0081901012",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["UBL"],
    ),
    PakistanCompany(
        company_name="MCB Bank Limited",
        psx_symbol="MCB",
        official_website="https://www.mcb.com.pk",
        isin="PK0055201018",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["MCB", "Muslim Commercial Bank"],
    ),
    PakistanCompany(
        company_name="National Bank of Pakistan",
        psx_symbol="NBP",
        official_website="https://www.nbp.com.pk",
        isin="PK0059501017",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["NBP"],
    ),
    PakistanCompany(
        company_name="Bank Alfalah Limited",
        psx_symbol="BAFL",
        official_website="https://www.bankalfalah.com",
        isin="PK0007601015",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["BAFL", "Bank Alfalah"],
    ),
    PakistanCompany(
        company_name="Meezan Bank Limited",
        psx_symbol="MEBL",
        official_website="https://www.meezanbank.com",
        isin="PK0054001013",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["MEBL", "Meezan Bank"],
    ),
    PakistanCompany(
        company_name="Allied Bank Limited",
        psx_symbol="ABL",
        official_website="https://www.abl.com",
        isin="PK0002401014",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["ABL", "Allied Bank"],
    ),
    PakistanCompany(
        company_name="Askari Bank Limited",
        psx_symbol="AKBL",
        official_website="https://www.askaribank.com.pk",
        isin="PK0006201019",
        fiscal_year_end="December 31",
        sector="Banking",
        aliases=["AKBL", "Askari Bank"],
    ),
    # ── Cement ────────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Lucky Cement Limited",
        psx_symbol="LUCK",
        official_website="https://www.lucky-cement.com",
        isin="PK0049301011",
        fiscal_year_end="June 30",
        sector="Cement",
        aliases=["LUCK", "Lucky Cement"],
    ),
    PakistanCompany(
        company_name="D.G. Khan Cement Company Limited",
        psx_symbol="DGKC",
        official_website="https://www.dgcement.com",
        isin="PK0023801015",
        fiscal_year_end="June 30",
        sector="Cement",
        aliases=["DGKC", "DG Khan Cement", "DG Cement"],
    ),
    PakistanCompany(
        company_name="Maple Leaf Cement Factory Limited",
        psx_symbol="MLCF",
        official_website="https://www.mapleleaf.com.pk",
        isin="PK0053901016",
        fiscal_year_end="June 30",
        sector="Cement",
        aliases=["MLCF", "Maple Leaf Cement"],
    ),
    PakistanCompany(
        company_name="Bestway Cement Limited",
        psx_symbol="BWCL",
        official_website="https://www.bestway.com.pk",
        isin="PK0010601012",
        fiscal_year_end="June 30",
        sector="Cement",
        aliases=["BWCL", "Bestway Cement"],
    ),
    # ── Technology ────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Systems Limited",
        psx_symbol="SYS",
        official_website="https://www.systemsltd.com",
        isin="PK0076201011",
        fiscal_year_end="December 31",
        sector="Technology",
        aliases=["SYS", "Systems Ltd"],
    ),
    PakistanCompany(
        company_name="NetSol Technologies Limited",
        psx_symbol="NETSOL",
        official_website="https://www.netsoltech.com",
        isin="PK0060001018",
        fiscal_year_end="June 30",
        sector="Technology",
        aliases=["NETSOL", "NetSol Technologies"],
    ),
    PakistanCompany(
        company_name="TRG Pakistan Limited",
        psx_symbol="TRG",
        official_website="https://www.trg-intl.com",
        isin="PK0077301018",
        fiscal_year_end="June 30",
        sector="Technology",
        aliases=["TRG", "TRG Pakistan"],
    ),
    # ── Oil & Gas ─────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Oil & Gas Development Company Limited",
        psx_symbol="OGDC",
        official_website="https://www.ogdcl.com",
        isin="PK0065401016",
        fiscal_year_end="June 30",
        sector="Oil & Gas",
        aliases=["OGDC", "OGDCL"],
    ),
    PakistanCompany(
        company_name="Pakistan Petroleum Limited",
        psx_symbol="PPL",
        official_website="https://www.ppl.com.pk",
        isin="PK0068301014",
        fiscal_year_end="June 30",
        sector="Oil & Gas",
        aliases=["PPL", "Pakistan Petroleum"],
    ),
    PakistanCompany(
        company_name="Mari Petroleum Company Limited",
        psx_symbol="MARI",
        official_website="https://www.marienergy.com.pk",
        isin="PK0053401017",
        fiscal_year_end="June 30",
        sector="Oil & Gas",
        aliases=["MARI", "Mari Petroleum", "Mari Gas"],
    ),
    PakistanCompany(
        company_name="Pakistan State Oil Company Limited",
        psx_symbol="PSO",
        official_website="https://www.psopk.com",
        isin="PK0069101012",
        fiscal_year_end="June 30",
        sector="Oil & Gas",
        aliases=["PSO", "Pakistan State Oil"],
    ),
    # ── Fertilizer ────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Engro Corporation Limited",
        psx_symbol="ENGRO",
        official_website="https://www.engro.com",
        isin="PK0027801010",
        fiscal_year_end="December 31",
        sector="Fertilizer / Conglomerate",
        aliases=["ENGRO", "Engro"],
    ),
    PakistanCompany(
        company_name="Fauji Fertilizer Company Limited",
        psx_symbol="FFC",
        official_website="https://www.ffc.com.pk",
        isin="PK0029401019",
        fiscal_year_end="December 31",
        sector="Fertilizer",
        aliases=["FFC", "Fauji Fertilizer"],
    ),
    PakistanCompany(
        company_name="Engro Fertilizers Limited",
        psx_symbol="EFERT",
        official_website="https://www.engrofertilizers.com",
        isin="PK0105301014",
        fiscal_year_end="December 31",
        sector="Fertilizer",
        aliases=["EFERT", "Engro Fertilizers"],
    ),
    # ── Pharma ────────────────────────────────────────────────────────
    PakistanCompany(
        company_name="The Searle Company Limited",
        psx_symbol="SEARL",
        official_website="https://www.searlecompany.com",
        isin="PK0073301017",
        fiscal_year_end="December 31",
        sector="Pharma",
        aliases=["SEARL", "Searle"],
    ),
    PakistanCompany(
        company_name="AGP Limited",
        psx_symbol="AGP",
        official_website="https://www.agp.com.pk",
        isin="PK0121201011",
        fiscal_year_end="December 31",
        sector="Pharma",
        aliases=["AGP"],
    ),
    # ── Power / Utilities ─────────────────────────────────────────────
    PakistanCompany(
        company_name="Hub Power Company Limited",
        psx_symbol="HUBC",
        official_website="https://www.hubpower.com",
        isin="PK0037001017",
        fiscal_year_end="June 30",
        sector="Power",
        aliases=["HUBC", "Hub Power", "Hubco"],
    ),
    PakistanCompany(
        company_name="K-Electric Limited",
        psx_symbol="KEL",
        official_website="https://www.ke.com.pk",
        isin="PK0044501019",
        fiscal_year_end="June 30",
        sector="Power",
        aliases=["KEL", "K-Electric", "KESC"],
        historical_names=["Karachi Electric Supply Company"],
    ),
    # ── Textile ───────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Interloop Limited",
        psx_symbol="ILP",
        official_website="https://www.interloop.com.pk",
        isin="PK0131501015",
        fiscal_year_end="June 30",
        sector="Textile",
        aliases=["ILP", "Interloop"],
    ),
    PakistanCompany(
        company_name="Nishat Mills Limited",
        psx_symbol="NML",
        official_website="https://www.nishatmills.com",
        isin="PK0061701010",
        fiscal_year_end="June 30",
        sector="Textile",
        aliases=["NML", "Nishat Mills"],
    ),
    # ── Insurance ─────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Adamjee Insurance Company Limited",
        psx_symbol="AICL",
        official_website="https://www.adamjeeinsurance.com",
        isin="PK0001201019",
        fiscal_year_end="December 31",
        sector="Insurance",
        aliases=["AICL", "Adamjee Insurance"],
    ),
    # ── Consumer ──────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Colgate-Palmolive (Pakistan) Limited",
        psx_symbol="COLG",
        official_website="https://www.colgatepalmolive.com.pk",
        isin="PK0020201016",
        fiscal_year_end="June 30",
        sector="Consumer",
        aliases=["COLG", "Colgate Palmolive Pakistan"],
    ),
    PakistanCompany(
        company_name="Nestle Pakistan Limited",
        psx_symbol="NESTLE",
        official_website="https://www.nestle.pk",
        isin="PK0060601015",
        fiscal_year_end="December 31",
        sector="Consumer",
        aliases=["NESTLE", "Nestle Pakistan"],
    ),
    PakistanCompany(
        company_name="Unilever Pakistan Foods Limited",
        psx_symbol="UPFL",
        official_website="https://www.unilever.pk",
        isin="PK0080501012",
        fiscal_year_end="December 31",
        sector="Consumer",
        aliases=["UPFL", "Unilever Pakistan"],
    ),
    # ── Automobile ────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Indus Motor Company Limited",
        psx_symbol="INDU",
        official_website="https://www.toyota-indus.com",
        isin="PK0039101014",
        fiscal_year_end="June 30",
        sector="Automobile",
        aliases=["INDU", "Indus Motor", "Toyota Indus"],
    ),
    PakistanCompany(
        company_name="Pak Suzuki Motor Company Limited",
        psx_symbol="PSMC",
        official_website="https://www.paksuzuki.com.pk",
        isin="PK0067501015",
        fiscal_year_end="December 31",
        sector="Automobile",
        aliases=["PSMC", "Pak Suzuki"],
    ),
    # ── Steel ─────────────────────────────────────────────────────────
    PakistanCompany(
        company_name="International Steels Limited",
        psx_symbol="ISL",
        official_website="https://www.isl.com.pk",
        isin="PK0121101013",
        fiscal_year_end="June 30",
        sector="Steel",
        aliases=["ISL", "International Steels"],
    ),
    # ── Chemicals ─────────────────────────────────────────────────────
    PakistanCompany(
        company_name="Lotte Chemical Pakistan Limited",
        psx_symbol="LOTCHEM",
        official_website="https://www.lfrcp.com.pk",
        isin="PK0037501016",
        fiscal_year_end="December 31",
        sector="Chemicals",
        aliases=["LOTCHEM", "Lotte Chemical"],
        historical_names=["Pakistan PTA Limited"],
    ),
]
