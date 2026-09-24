"""Pakistan Stock Exchange (PSX) annual-report harvesting extension.

Extends the ZETA core engine for PAK/XKAR companies.  Reuses the existing
download engine, PDF validation, SHA-256 hashing, atomic writes, SQLite
state ledger, and benchmark methodology while adding Pakistan-specific
identity resolution, source discovery, fiscal-year logic, and
annual-report classification.
"""

__all__ = [
    "PakistanCompany",
    "PakistanReport",
    "PakistanConfig",
]
