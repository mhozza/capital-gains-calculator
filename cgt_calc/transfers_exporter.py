"""Export spouse transfers as CSV in Schwab Journaled Shares format."""

from __future__ import annotations

import csv
from decimal import Decimal
import logging
from typing import TYPE_CHECKING, Final

from .model import ActionType
from .util import exact_str, round_decimal, strip_zeros

if TYPE_CHECKING:
    import datetime
    from typing import TextIO

    from .model import CalculationEntry, CapitalGainsReport

__all__ = [
    "JOURNALED_SHARES_HEADER",
    "export_transfers",
    "has_spouse_transfers",
    "has_transfers",
]

LOGGER = logging.getLogger(__name__)

JOURNALED_SHARES_HEADER: Final = (
    "Date",
    "Type",
    "Symbol",
    "Quantity",
    "BaseCostPerShare",
)
TRANSFER_TO_SPOUSE_PREFIX: Final = "transfer-to-spouse$"


def has_transfers(report: CapitalGainsReport) -> bool:
    """Return True if the report contains any transfers to spouse."""
    return any(
        key.startswith(TRANSFER_TO_SPOUSE_PREFIX)
        for symbol_dict in report.calculation_log.values()
        for key in symbol_dict
    )


has_spouse_transfers = has_transfers


def export_transfers(report: CapitalGainsReport, stream: TextIO) -> int:
    """Write spouse transfers to stream as CSV matching Schwab journaled shares format.

    Returns the count of transfer rows written.
    """
    writer = csv.writer(stream)
    writer.writerow(JOURNALED_SHARES_HEADER)

    transfer_prefix = TRANSFER_TO_SPOUSE_PREFIX
    transfers: list[tuple[datetime.date, str, list[CalculationEntry]]] = sorted(
        (
            (date_index, key[len(transfer_prefix) :], entry_list)
            for date_index, symbol_dict in report.calculation_log.items()
            for key, entry_list in symbol_dict.items()
            if key.startswith(transfer_prefix)
        ),
        key=lambda transfer: (transfer[0], transfer[1]),
    )

    for date_index, symbol, entry_list in transfers:
        quantity = sum((e.quantity for e in entry_list), Decimal(0))
        orig_costs = [
            e.original_allowable_cost
            for e in entry_list
            if e.original_allowable_cost is not None
        ]
        if orig_costs and len(orig_costs) == len(entry_list):
            total_cost = sum(orig_costs, Decimal(0))
        else:
            total_cost = sum((e.allowable_cost for e in entry_list), Decimal(0))

        cost_per_share = total_cost / quantity
        if cost_per_share == round_decimal(cost_per_share, 2):
            cost_str = f"{cost_per_share:.2f}"
        else:
            cost_str = exact_str(cost_per_share)

        date_str = date_index.strftime("%m/%d/%Y")
        qty_str = strip_zeros(quantity)
        writer.writerow(
            [
                date_str,
                ActionType.TRANSFER_TO_SPOUSE.name,
                symbol,
                qty_str,
                cost_str,
            ]
        )

    return len(transfers)
