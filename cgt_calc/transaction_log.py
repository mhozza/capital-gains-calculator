"""Functions to work with HMRC transaction log."""

from dataclasses import astuple
import datetime
from decimal import Decimal

from .model import (
    AquisitionHmrcTransactionLog,
    DisposalHmrcTransactionLog,
    DisposalType,
    HmrcTransactionData,
)


def has_aquisition_key(
    transactions: AquisitionHmrcTransactionLog,
    date_index: datetime.date,
    symbol: str,
) -> bool:
    """Check if transaction log has entry for date_index and symbol."""
    return date_index in transactions and symbol in transactions[date_index]


def has_disposal_key(
    transactions: DisposalHmrcTransactionLog,
    date_index: datetime.date,
    symbol: str,
    disposal_type: DisposalType,
) -> bool:
    """Check if transaction log has entry for date_index and symbol."""
    return (
        date_index in transactions
        and symbol in transactions[date_index]
        and disposal_type in transactions[date_index][symbol]
    )


def add_to_aquisition_list(
    current_list: AquisitionHmrcTransactionLog,
    date_index: datetime.date,
    symbol: str,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
) -> None:
    """Add entry to given transaction log."""
    if date_index not in current_list:
        current_list[date_index] = {}
    if symbol not in current_list[date_index]:
        current_list[date_index][symbol] = HmrcTransactionData(
            quantity=Decimal(0), amount=Decimal(0), fees=Decimal(0)
        )
    current_quantity, current_amount, current_fees = astuple(
        current_list[date_index][symbol]
    )
    current_list[date_index][symbol] = HmrcTransactionData(
        quantity=current_quantity + quantity,
        amount=current_amount + amount,
        fees=current_fees + fees,
    )


def add_to_disposal_list(
    current_list: DisposalHmrcTransactionLog,
    date_index: datetime.date,
    symbol: str,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
    disposal_type: DisposalType,
) -> None:
    """Add entry to given transaction log."""
    if date_index not in current_list:
        current_list[date_index] = {}
    if symbol not in current_list[date_index]:
        current_list[date_index][symbol] = {}
    if disposal_type not in current_list[date_index][symbol]:
        current_list[date_index][symbol][disposal_type] = HmrcTransactionData(
            quantity=Decimal(0), amount=Decimal(0), fees=Decimal(0)
        )

    current_quantity, current_amount, current_fees = astuple(
        current_list[date_index][symbol][disposal_type]
    )
    current_list[date_index][symbol][disposal_type] = HmrcTransactionData(
        quantity=current_quantity + quantity,
        amount=current_amount + amount,
        fees=current_fees + fees,
    )
