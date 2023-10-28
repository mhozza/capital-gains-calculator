"""Custom CSV parser."""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

COL_DATE = "Vest Date"
COL_PLAN = "Plan"
COL_TYPE = "Type"
COL_PRICE = "Price"
COL_QUANTITY = "Quantity"
COL_CASH_PROCEEDS = "Net Cash Proceeds"
COL_SHARE_PROCEEDS = "Net Share Proceeds"


COLUMNS: Final[list[str]] = [
    COL_DATE,
    COL_PLAN,
    COL_TYPE,
    COL_PRICE,
    COL_QUANTITY,
    COL_CASH_PROCEEDS,
    COL_SHARE_PROCEEDS,
]


TYPE_SALE = "Sale"
TYPE_RELEASE = "Release"
TYPE_GIFT = "Gift"

# These can be potentially wired through as a flag
KNOWN_SYMBOL_DICT: Final[dict[str, str]] = {
    "GSU Class C": "GOOG",
    "Cash": "USD",
}


def _hacky_parse_decimal(decimal: str) -> Decimal:
    return Decimal(decimal.replace(",", "").replace("$", ""))


def _parse_header(
    header: list[str],
    file: str,
) -> dict[int, str]:
    """Check if header is valid."""

    matched_columns: dict[str, int | None] = {key: None for key in COLUMNS}

    for i, col in enumerate(header):
        matched_columns[col] = i

    missing_cols = [key for key, value in matched_columns.items() if value is None]
    if len(missing_cols) > 0:
        msg = f"Missing mandatory columns: {missing_cols}."
        raise ParsingError(msg, file)
    return {
        i: name
        for name, i in matched_columns.items()
        if name in COLUMNS and i is not None
    }


def _associate_row(row: list[str], header: dict[int, str]) -> dict[str, str]:
    return {name: row[i] for i, name in header.items()}


def _parse_row(row: dict[str, str], file: str) -> BrokerTransaction:
    transaction_type = row[COL_TYPE]

    price = None
    action = None
    amount = None
    quantity = None
    fees = Decimal(0)
    if transaction_type == TYPE_RELEASE:
        price = _hacky_parse_decimal(row[COL_PRICE])
        action = ActionType.STOCK_ACTIVITY
        quantity = _hacky_parse_decimal(row[COL_SHARE_PROCEEDS])
        amount = quantity * price
    elif transaction_type == TYPE_SALE:
        price = _hacky_parse_decimal(row[COL_PRICE])
        action = ActionType.SELL
        quantity = _hacky_parse_decimal(row[COL_QUANTITY])
        amount = _hacky_parse_decimal(row[COL_CASH_PROCEEDS])
        fees = quantity * price - amount
    elif transaction_type == TYPE_GIFT:
        action = ActionType.GIFT
        quantity = _hacky_parse_decimal(row[COL_QUANTITY])
        price = None
    else:
        msg = f"Unknown transaction_type: {transaction_type}."
        raise ParsingError(msg, file)

    symbol = KNOWN_SYMBOL_DICT[row[COL_PLAN]]
    symbol = TICKER_RENAMES.get(symbol, symbol)

    return BrokerTransaction(
        date=datetime.datetime.strptime(row[COL_DATE], "%d-%b-%Y").date(),
        action=action,
        symbol=symbol,
        description=row[COL_PLAN],
        quantity=quantity,
        price=price,
        fees=fees,
        amount=amount,
        currency="USD",
        broker="",
    )


def read_custom_csv_transactions(file: str) -> list[BrokerTransaction]:
    """Read transactions from a CSV file."""
    with Path(file).open(encoding="utf-8") as csv_file:
        lines = list(csv.reader(csv_file))
        header = lines[0]
        lines = lines[1:]

        header_cols = _parse_header(header, file)
        return [_parse_row(_associate_row(row, header_cols), file) for row in lines]
