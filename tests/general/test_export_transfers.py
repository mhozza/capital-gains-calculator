"""Tests for exporting spouse transfers in Schwab Journaled Shares format."""

from __future__ import annotations

import datetime
from decimal import Decimal
import io
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

from cgt_calc.model import ActionType, CurrencyCode
from cgt_calc.parsers.schwab import _load_journaled_shares_file
from cgt_calc.transfers_exporter import export_transfers
from tests.general.calc_test_data import (
    USD,
    transaction,
    transfer_to_spouse_transaction,
)
from tests.general.test_calc import create_calculator, get_report
from tests.utils import build_cmd, report_path, run_cli

if TYPE_CHECKING:
    import pytest

RAW_HEADER = "date,action,symbol,quantity,price,fees,currency\n"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run cgt-calc via subprocess without failing on non-zero exit."""
    return subprocess.run(
        build_cmd(*args), capture_output=True, encoding="utf-8", check=False
    )


def _run_bare(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run cgt-calc with explicit arguments without build_cmd defaults."""
    return subprocess.run(
        [sys.executable, "-m", "cgt_calc.main", *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=cwd,
    )


def test_export_empty_report() -> None:
    """An empty calculation log writes only the header and returns 0."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 8),
                ActionType.BUY,
                "FOO",
                100,
                10,
                0,
                -1000,
                CurrencyCode(USD),
            ),
        ],
    )
    stream = io.StringIO()
    count = export_transfers(report, stream)

    assert count == 0
    assert (
        stream.getvalue() == "Date,Type,Symbol,Quantity,BaseCostPerShare\r\n"
        or stream.getvalue() == "Date,Type,Symbol,Quantity,BaseCostPerShare\n"
    )


def test_export_single_transfer_usd() -> None:
    """A transfer of USD shares exports with original base cost per share."""
    buy_day = datetime.date(2024, 6, 8)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 1000, 10, 0, -10000, CurrencyCode(USD)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 400),
        ],
    )
    stream = io.StringIO()
    count = export_transfers(report, stream)

    assert count == 1
    lines = stream.getvalue().splitlines()
    assert lines[0] == "Date,Type,Symbol,Quantity,BaseCostPerShare"
    assert lines[1] == "06/10/2024,TRANSFER_TO_SPOUSE,FOO,400,10.00"


def test_export_multiple_transfers_sorted() -> None:
    """Multiple transfers are sorted by date, then symbol."""
    buy_day = datetime.date(2024, 6, 1)
    day1 = datetime.date(2024, 6, 10)
    day2 = datetime.date(2024, 6, 15)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "ZZZ", 100, 20, 0, -2000, CurrencyCode(USD)
            ),
            transaction(
                buy_day, ActionType.BUY, "AAA", 100, 30, 0, -3000, CurrencyCode(USD)
            ),
            transfer_to_spouse_transaction(day2, "AAA", 25),
            transfer_to_spouse_transaction(day1, "ZZZ", 50),
            transfer_to_spouse_transaction(day1, "AAA", 10),
        ],
    )
    stream = io.StringIO()
    count = export_transfers(report, stream)

    assert count == 3
    lines = stream.getvalue().splitlines()
    assert lines[0] == "Date,Type,Symbol,Quantity,BaseCostPerShare"
    assert lines[1] == "06/10/2024,TRANSFER_TO_SPOUSE,AAA,10,30.00"
    assert lines[2] == "06/10/2024,TRANSFER_TO_SPOUSE,ZZZ,50,20.00"
    assert lines[3] == "06/15/2024,TRANSFER_TO_SPOUSE,AAA,25,30.00"


def test_export_fractional_price() -> None:
    """Fractional prices beyond 2 decimal places retain precision."""
    buy_day = datetime.date(2024, 6, 8)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    # 400 shares bought for $10,050 -> $25.125 / share
    report = get_report(
        calculator,
        [
            transaction(
                buy_day,
                ActionType.BUY,
                "FOO",
                400,
                25.125,
                0,
                -10050,
                CurrencyCode(USD),
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 200),
        ],
    )
    stream = io.StringIO()
    count = export_transfers(report, stream)

    assert count == 1
    lines = stream.getvalue().splitlines()
    assert lines[1] == "06/10/2024,TRANSFER_TO_SPOUSE,FOO,200,25.125"


def test_export_non_schwab_gbp() -> None:
    """Non-Schwab transfers (e.g. GBP) export with original cost base per share."""
    buy_day = datetime.date(2024, 6, 8)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "VOD", 500, 75, 0, -37500, CurrencyCode("GBP")
            ),
            transfer_to_spouse_transaction(transfer_day, "VOD", 200),
        ],
    )
    stream = io.StringIO()
    count = export_transfers(report, stream)

    assert count == 1
    lines = stream.getvalue().splitlines()
    assert lines[0] == "Date,Type,Symbol,Quantity,BaseCostPerShare"
    assert lines[1] == "06/10/2024,TRANSFER_TO_SPOUSE,VOD,200,75.00"


def test_cli_exports_transfers_file_override(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """The CLI exports spouse transfers to the requested CSV path via --export-transfers-file."""
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER
        + "2024-06-08,BUY,FOO,1000,10.00,0.00,USD\n"
        + "2024-06-10,TRANSFER_TO_SPOUSE,FOO,400,0.00,0.00,USD\n",
        encoding="utf-8",
    )
    export_file = tmp_path / "custom_transfers.csv"

    cmd = build_cmd(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--no-balance-check",
        "--export-transfers-file",
        str(export_file),
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)

    assert "Saved 1 spouse transfer to" in result.stderr
    assert export_file.is_file()
    lines = export_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Date,Type,Symbol,Quantity,BaseCostPerShare"
    assert lines[1] == "06/10/2024,TRANSFER_TO_SPOUSE,FOO,400,10.00"


def test_cli_overwrites_existing_destination(tmp_path: Path) -> None:
    """The export destination is overwritten if it already exists."""
    destination = tmp_path / "transfers.csv"
    destination.write_text("old content\n", encoding="utf-8")
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER
        + "2024-06-08,BUY,FOO,100,10.00,0.00,USD\n"
        + "2024-06-10,TRANSFER_TO_SPOUSE,FOO,40,0.00,0.00,USD\n",
        encoding="utf-8",
    )

    result = _run(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--no-balance-check",
        "--export-transfers-file",
        str(destination),
        "--no-report",
    )

    assert result.returncode == 0
    assert "old content" not in destination.read_text(encoding="utf-8")
    assert "06/10/2024,TRANSFER_TO_SPOUSE,FOO,40,10.00" in destination.read_text(
        encoding="utf-8"
    )


def test_cli_creates_missing_parent_directory(tmp_path: Path) -> None:
    """The export destination parent directory is created if it does not exist."""
    destination = tmp_path / "nested" / "dir" / "transfers.csv"
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER
        + "2024-06-08,BUY,FOO,100,10.00,0.00,USD\n"
        + "2024-06-10,TRANSFER_TO_SPOUSE,FOO,40,0.00,0.00,USD\n",
        encoding="utf-8",
    )

    result = _run(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--no-balance-check",
        "--export-transfers-file",
        str(destination),
        "--no-report",
    )

    assert result.returncode == 0
    assert destination.is_file()


def test_cli_refuses_destination_is_directory(tmp_path: Path) -> None:
    """The export destination must be a file, not a directory."""
    destination = tmp_path / "dir_dest"
    destination.mkdir()
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER
        + "2024-06-08,BUY,FOO,100,10.00,0.00,USD\n"
        + "2024-06-10,TRANSFER_TO_SPOUSE,FOO,40,0.00,0.00,USD\n",
        encoding="utf-8",
    )

    result = _run(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--no-balance-check",
        "--export-transfers-file",
        str(destination),
        "--no-report",
    )

    assert result.returncode != 0
    assert "is a directory" in result.stderr


def test_cli_warns_when_other_transfers_exist(tmp_path: Path) -> None:
    """A warning is emitted when transfers other than spouse transfers exist."""
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER
        + "2024-06-08,BUY,FOO,100,10.00,0.00,USD\n"
        + "2024-06-10,GIFT,FOO,20,0.00,0.00,USD\n"
        + "2024-06-12,TRANSFER_TO_SPOUSE,FOO,30,0.00,0.00,USD\n",
        encoding="utf-8",
    )
    export_file = tmp_path / "transfers.csv"
    result = _run(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--no-balance-check",
        "--export-transfers-file",
        str(export_file),
        "--no-report",
    )
    assert result.returncode == 0
    assert (
        "Transfers other than spouse transfers were found; only spouse transfers are currently exported to"
        in result.stderr
    )
    assert export_file.is_file()
    lines = export_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[1] == "06/12/2024,TRANSFER_TO_SPOUSE,FOO,30,10.00"


def test_cli_automatic_export_with_spouse_transfers(tmp_path: Path) -> None:
    """When spouse transfers exist, they are automatically exported to out/transfers.csv."""
    rates_file = Path("tests/exchange_rates_data.csv").resolve()
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER
        + "2024-06-08,BUY,FOO,100,10.00,0.00,USD\n"
        + "2024-06-10,TRANSFER_TO_SPOUSE,FOO,40,0.00,0.00,USD\n",
        encoding="utf-8",
    )

    result = _run_bare(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--exchange-rates-file",
        str(rates_file),
        "--no-balance-check",
        "--no-report",
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert "Saved 1 spouse transfer to out/transfers.csv" in result.stderr
    default_transfers = tmp_path / "out" / "transfers.csv"
    assert default_transfers.is_file()
    lines = default_transfers.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Date,Type,Symbol,Quantity,BaseCostPerShare"
    assert lines[1] == "06/10/2024,TRANSFER_TO_SPOUSE,FOO,40,10.00"


def test_cli_no_automatic_export_without_spouse_transfers(tmp_path: Path) -> None:
    """When no spouse transfers exist, out/transfers.csv is not written."""
    rates_file = Path("tests/exchange_rates_data.csv").resolve()
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER + "2024-06-08,BUY,FOO,100,10.00,0.00,USD\n",
        encoding="utf-8",
    )

    result = _run_bare(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--exchange-rates-file",
        str(rates_file),
        "--no-balance-check",
        "--no-report",
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert "spouse transfer" not in result.stderr
    default_transfers = tmp_path / "out" / "transfers.csv"
    assert not default_transfers.exists()


def test_cli_refuses_collision_with_output(tmp_path: Path) -> None:
    """The export destination cannot collide with --output."""
    destination = tmp_path / "calculations.pdf"
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER + "2024-06-08,BUY,FOO,10,10,0,USD\n", encoding="utf-8"
    )

    result = _run(
        "--year",
        "2024",
        "--raw-file",
        str(history),
        "--no-balance-check",
        "--export-transfers-file",
        str(destination),
        "--output",
        str(destination),
    )

    assert result.returncode != 0
    assert "Choose a different filename" in result.stderr


def test_roundtrip_with_schwab_parser(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Exported CSV can be directly passed to --schwab-journaled-shares-file."""
    # Spouse A: buys 100 shares of FOO at $25, transfers 40 to spouse on 01/08/2024
    spouse_a_history = tmp_path / "spouse_a.csv"
    spouse_a_history.write_text(
        RAW_HEADER
        + "2024-01-02,BUY,FOO,100,25.00,0.00,USD\n"
        + "2024-01-08,TRANSFER_TO_SPOUSE,FOO,40,0.00,0.00,USD\n",
        encoding="utf-8",
    )
    journal_file = tmp_path / "journal_base_costs.csv"

    # Spouse A runs calculation and exports spouse transfers
    cmd_a = build_cmd(
        "--year",
        "2023",
        "--raw-file",
        str(spouse_a_history),
        "--no-balance-check",
        "--export-transfers-file",
        str(journal_file),
        "--output",
        report_path(request) + "spouse_a.pdf",
    )
    run_cli(cmd_a)

    # Verify exported file parses cleanly with _load_journaled_shares_file
    base_costs = _load_journaled_shares_file(journal_file)
    assert (datetime.date(2024, 1, 8), "FOO") in base_costs.entries
    assert base_costs.entries[datetime.date(2024, 1, 8), "FOO"] == [
        (Decimal(40), Decimal("25.00"))
    ]

    # Spouse B: Schwab account has a 'Journaled Shares' row on 01/08/2024, then sells on 06/21/2024
    schwab_header = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
    spouse_b_history = tmp_path / "spouse_b_schwab.csv"
    spouse_b_history.write_text(
        schwab_header
        + "06/21/2024,Sell,FOO,FOO INC,$50,40,$0,$2000\n"
        + "01/08/2024,Journaled Shares,FOO,JOURNALED SHARES,,40,,$0\n",
        encoding="utf-8",
    )

    # Spouse B runs calculation using Spouse A's exported file
    cmd_b = build_cmd(
        "--year",
        "2024",
        "--schwab-file",
        str(spouse_b_history),
        "--schwab-journaled-shares-file",
        str(journal_file),
        "--output",
        report_path(request) + "spouse_b.pdf",
    )
    result_b = run_cli(cmd_b)

    # Proceeds: 40 * $50 = $2000 => ~ £1,573.69
    # Cost: 40 * $25 = $1000 => ~ £790.45
    # Gain: ~ £783.24
    assert "Disposals:                  1" in result_b.stdout
    assert "£783.24" in result_b.stdout
