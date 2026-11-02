"""Tests for etl/migrate.py that need no database: file layout and baseline probes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import migrate  # noqa: E402


def test_versions_are_unique_and_contiguous():
    versions = [v for v, _ in migrate.versioned_files()]
    assert versions == sorted(versions)
    assert versions == [f"{i:02d}" for i in range(1, len(versions) + 1)]


def test_every_legacy_migration_has_a_probe():
    assert set(migrate.LEGACY_PROBES) == {f"{i:02d}" for i in range(1, 8)}


def test_migrations_do_not_manage_their_own_transaction():
    """The runner wraps each file in a transaction with its schema_migrations row; a COMMIT
    inside the file would commit early and break all-or-nothing."""
    for _, path in migrate.versioned_files():
        lines = [ln.strip().upper() for ln in path.read_text(encoding="utf-8").splitlines()]
        assert "BEGIN;" not in lines and "COMMIT;" not in lines, path.name


def test_checksum_ignores_line_endings(tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    b.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    assert migrate.checksum(a) == migrate.checksum(b)


def test_repeatable_scripts_exist():
    assert [p.name for p in migrate.repeatable_files()] == ["R_dashboard_reader.sql"]
