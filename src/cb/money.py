"""Exact USD money handling. NFR-1 (integer cents, never float), REQ-2 (amount validation)."""
import re
from decimal import Decimal

class MoneyError(Exception):
    pass

def parse_amount(text):
    if not isinstance(text, str):
        raise MoneyError("Input must be a string")

    text = text.strip()
    if not text:
        raise MoneyError("Input cannot be empty")

    if not re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", text):
        raise MoneyError("Invalid amount format")

    decimal_amount = Decimal(text)
    if decimal_amount <= 0:
        raise MoneyError("Amount must be greater than zero")
    if decimal_amount > Decimal("100000.00"):
        raise MoneyError("Amount cannot exceed 100000.00")

    return int(decimal_amount * 100)

def format_cents(cents):
    if not isinstance(cents, int):
        raise MoneyError("Input must be an integer")
    if cents < 0:
        raise MoneyError("Amount cannot be negative")
    dollars, remainder = divmod(cents, 100)
    return f"{dollars}.{remainder:02d}"