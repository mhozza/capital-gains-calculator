"""Capital Gain Calculator command line interface."""

from __future__ import annotations

import decimal
import logging
import sys
from typing import TYPE_CHECKING

from colorama import Fore

from . import render_latex
from .args_parser import (
    create_parser,
    reject_duplicate_stdin,
    reject_schwab_file_and_dir,
    resolve_reporting_period,
)
from .const import DEFAULT_TRANSFERS_FILE
from .currency_converter import CurrencyConverter
from .current_price_fetcher import CurrentPriceFetcher
from .dates import get_tax_year_end, get_tax_year_start
from .exceptions import CgtError, SpouseTransferExportError, TransactionDumpError
from .isin_converter import IsinConverter
from .logging import setup_logging, style_text
from .model import ActionType
from .parsers.broker_registry import BrokerRegistry
from .render_text import render_text
from .share_prices import SharePrices
from .spin_off_handler import SpinOffHandler
from .transaction_dumper import dump_transactions
from .transfers_exporter import export_transfers, has_transfers

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterator, Sequence
    import datetime
    from pathlib import Path

    from .model import BrokerTransaction, CapitalGainsReport

LOGGER = logging.getLogger(__name__)


def _common_paths_written(args: argparse.Namespace) -> Iterator[tuple[Path, str]]:
    """Yield report and cache paths written during the run."""
    if not getattr(args, "no_report", False):
        output = getattr(args, "output", None)
        if output is not None:
            directory = output.parent
            jobname = output.stem
            yield output, "the report from --output"
            yield directory / f"{jobname}.tex", "the LaTeX source from --output"
            if not getattr(args, "no_pdflatex", False):
                # pdflatex names its output after the report's stem, so an
                # --output carrying any other suffix, or none, still lands here.
                yield directory / f"{jobname}.pdf", "the PDF report from --output"
                for suffix in (".latex.log", ".log", ".aux"):
                    yield (
                        directory / f"{jobname}{suffix}",
                        "a pdflatex working file from --output",
                    )
    for cache, option in (
        (getattr(args, "exchange_rates_file", None), "--exchange-rates-file"),
        (getattr(args, "isin_translation_file", None), "--isin-translation-file"),
        (getattr(args, "spin_offs_file", None), "--spin-offs-file"),
    ):
        if cache is not None:
            yield cache, f"the cache from {option}"


def _paths_written_after_dump(args: argparse.Namespace) -> Iterator[tuple[Path, str]]:
    """Yield each file the rest of this run may write, and what it is.

    The dump is saved before anything else, so a destination naming one of
    these would be silently replaced by the time the run finished. The report
    names follow render_latex.render_pdf; pdflatex leaves its own log and aux
    file next to the report and removes them again afterwards.
    """
    yield from _common_paths_written(args)
    export_path = getattr(args, "export_transfers_file", None)
    if export_path is not None:
        yield (
            export_path,
            "the transfers export from --export-transfers-file",
        )
    else:
        yield (
            DEFAULT_TRANSFERS_FILE,
            "the transfers export",
        )


def _paths_written_other_than_transfers_export(
    args: argparse.Namespace,
) -> Iterator[tuple[Path, str]]:
    """Yield each other file this run may write, and what it is."""
    yield from _common_paths_written(args)
    dump_path = getattr(args, "dump_transactions", None)
    if dump_path is not None:
        yield (
            dump_path,
            "the transaction dump from --dump-transactions",
        )


def _save_transaction_dump(
    args: argparse.Namespace, transactions: Sequence[BrokerTransaction]
) -> None:
    """Write the parsed transactions to the new CSV file the user asked for."""
    path: Path = args.dump_transactions
    destination = path.resolve()
    for other, description in _paths_written_after_dump(args):
        if other.resolve() == destination:
            raise TransactionDumpError(
                f"--dump-transactions path '{path}' is also {description}, "
                "which this run writes after saving the dump. Choose a "
                "different filename."
            )
    if not path.parent.is_dir():
        raise TransactionDumpError(
            f"--dump-transactions directory '{path.parent}' does not exist. "
            "Create it, or choose a path in a directory that does."
        )
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            dump_transactions(transactions, stream)
    except FileExistsError as err:
        raise TransactionDumpError(
            f"--dump-transactions file '{path}' already exists. Choose a new "
            "filename, or move the existing file out of the way."
        ) from err
    except OSError as err:
        raise TransactionDumpError(
            f"Could not write parsed transactions to '{path}': {err}. A partly "
            "written file may be left behind; remove it or choose another "
            "filename before retrying."
        ) from err
    count = len(transactions)
    noun = "transaction" if count == 1 else "transactions"
    LOGGER.info(
        style_text(
            f"Saved {count} parsed {noun} to {path}",
            colour=Fore.CYAN,
            emoji="💾",
            stream=sys.stderr,
        )
    )


def _has_other_transfers(
    report: CapitalGainsReport,
    broker_transactions: Sequence[BrokerTransaction],
) -> bool:
    """Return True if transfers other than spouse transfers exist in the reporting period."""
    for symbol_dict in report.calculation_log.values():
        for key in symbol_dict:
            if key.startswith(("gift$", "gift-unconnected$")):
                return True

    def is_in_period(date: datetime.date) -> bool:
        if report.period_start is not None and date < report.period_start:
            return False
        if report.period_end is not None and date > report.period_end:
            return False
        if report.period_start is None and report.period_end is None:
            return (
                get_tax_year_start(report.tax_year)
                <= date
                <= get_tax_year_end(report.tax_year)
            )
        return True

    for trx in broker_transactions:
        if not is_in_period(trx.date):
            continue
        if trx.action in {
            ActionType.GIFT,
            ActionType.GIFT_UNCONNECTED,
            ActionType.UNCLASSIFIED_GIFT,
        }:
            return True

    return False


def _save_transfers_export(
    args: argparse.Namespace,
    report: CapitalGainsReport,
    broker_transactions: Sequence[BrokerTransaction],
) -> None:
    """Write spouse transfers to the CSV file (either default or user-specified)."""
    explicit_export = getattr(args, "export_transfers_file", None) is not None
    path: Path = (
        args.export_transfers_file if explicit_export else DEFAULT_TRANSFERS_FILE
    )

    if _has_other_transfers(report, broker_transactions):
        LOGGER.warning(
            "Transfers other than spouse transfers were found; "
            "only spouse transfers are currently exported to %s",
            path,
        )

    if not has_transfers(report) and not explicit_export:
        return

    destination = path.resolve()
    for other, description in _paths_written_other_than_transfers_export(args):
        if other.resolve() == destination:
            opt_name = (
                "--export-transfers-file" if explicit_export else "transfers export"
            )
            raise SpouseTransferExportError(
                f"{opt_name} path '{path}' is also {description}, "
                "which this run writes. Choose a different filename."
            )
    if path.is_dir():
        raise SpouseTransferExportError(
            f"Transfers export path '{path}' is a directory, not a file."
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as stream:
            count = export_transfers(report, stream)
    except OSError as err:
        raise SpouseTransferExportError(
            f"Could not write spouse transfers to '{path}': {err}. A partly "
            "written file may be left behind; remove it or choose another "
            "filename before retrying."
        ) from err
    noun = "transfer" if count == 1 else "transfers"
    LOGGER.info(
        style_text(
            f"Saved {count} spouse {noun} to {path}",
            colour=Fore.CYAN,
            emoji="💾",
            stream=sys.stderr,
        )
    )


def calculate_cgt(args: argparse.Namespace) -> None:
    """Perform all the computations."""

    # Imported here because cgt_calc.main re-exports the entry points below,
    # so a module-level import either way round would be circular.
    from .main import CapitalGainsCalculator  # noqa: PLC0415

    isin_translation_file = args.isin_translation_file

    # Read data from input files
    isin_converter = IsinConverter(isin_translation_file)
    broker_transactions = BrokerRegistry.load_all_transactions(args, isin_converter)

    if args.dump_transactions is not None:
        _save_transaction_dump(args, broker_transactions)

    currency_converter = CurrencyConverter.create(args.exchange_rates_file)
    share_prices = SharePrices(args.prices_file)
    price_fetcher = CurrentPriceFetcher(
        currency_converter, historical_prices_data=share_prices.closing_prices()
    )
    spin_off_handler = SpinOffHandler(args.spin_offs_file)

    calculator = CapitalGainsCalculator(
        args.year,
        currency_converter,
        isin_converter,
        price_fetcher,
        spin_off_handler,
        share_prices,
        args.interest_fund_tickers,
        cgt_exempt_tickers=args.cgt_exempt_tickers,
        balance_check=args.balance_check,
        autoconvert_currency=args.autoconvert_currency,
        calc_unrealized_gains=args.calc_unrealized_gains,
        gifts_to_spouse=args.gifts_to_spouse,
        period_start=args.period_from,
        period_end=args.period_to,
    )
    # First pass converts broker transactions to HMRC transactions,
    # collapsing all transactions with the same type within the same day.
    # It also converts prices to GBP, validates the data, plans share
    # reorganisations, and records dividends, taxes on dividends and interest
    # for the second pass to process.
    # Second pass calculates capital gain tax for the given tax year.
    report = calculator.calculate(broker_transactions)
    # The report string is newline-terminated already; avoid a trailing
    # blank line so piped output stays stable under newline normalisation.
    print(render_text(report, args.income), end="")

    _save_transfers_export(args, report, broker_transactions)

    # Generate PDF report.
    if not args.no_report:
        render_latex.render_pdf(
            report,
            output_path=args.output,
            skip_pdflatex=args.no_pdflatex,
        )
    done_msg = (
        "Done! Calculations complete (PDF generation skipped)."
        if args.no_pdflatex or args.no_report
        else "Done! Report generated successfully."
    )
    LOGGER.info(style_text(done_msg, colour=Fore.GREEN, emoji="🎉", stream=sys.stderr))


def main() -> int:
    """Run main function."""

    # Enable colourised logging.
    setup_logging()

    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True

    parser = create_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    reject_duplicate_stdin(parser, args)
    reject_schwab_file_and_dir(parser, args)
    resolve_reporting_period(parser, args)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        calculate_cgt(args)
    except CgtError as err:
        if args.verbose:
            LOGGER.exception("Exception:")
        else:
            # Print error without traceback
            LOGGER.error("%s", err)
        return 1
    except Exception:
        # Last-resort catch for unexpected exceptions
        LOGGER.critical("Unexpected error!")
        LOGGER.exception("Details:")
        return 1

    return 0


def init() -> None:
    """Entry point."""
    sys.exit(main())


if __name__ == "__main__":
    init()
