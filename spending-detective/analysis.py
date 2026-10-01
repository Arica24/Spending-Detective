"""Deterministic CSV validation and review hints; no financial advice."""
import csv
import io
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

CATEGORIES = ('Food', 'Transport', 'Shopping', 'Bills', 'Entertainment', 'Health', 'Other')

def merchant_key(value):
    return re.sub(r'\s+', ' ', value.strip().casefold())

def guess_category(merchant):
    groups = {'Food': ('cafe', 'grocer', 'market', 'restaurant'),
              'Transport': ('rail', 'bus', 'transport', 'taxi'),
              'Bills': ('energy', 'water', 'internet', 'mobile'),
              'Entertainment': ('stream', 'cinema', 'music', 'gym'),
              'Health': ('pharmacy', 'dental'), 'Shopping': ('shop', 'clothing', 'books')}
    for category, words in groups.items():
        if any(word in merchant_key(merchant) for word in words):
            return category
    return 'Other'

def parse_csv(raw):
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)):
            raise ValueError('Use unique CSV column names.')
        if not {'date', 'merchant', 'amount'}.issubset(headers):
            raise ValueError('CSV needs date, merchant and amount columns. Download the sample for the format.')
        if set(headers) - {'date', 'merchant', 'amount', 'category'}:
            raise ValueError('Only date, merchant, amount and optional category columns are supported.')
        rows = []
        for line, row in enumerate(reader, 2):
            if len(rows) >= 5000:
                raise ValueError('Maximum 5,000 transactions per upload.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Row {line}: wrong number of columns.')
            day = row['date'].strip()
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ValueError(f'Row {line}: use dates like 2026-09-01.')
            try:
                date.fromisoformat(day)
            except ValueError:
                raise ValueError(f'Row {line}: invalid date.') from None
            merchant = row['merchant'].strip()
            if not merchant or len(merchant) > 120 or any(ord(c) < 32 for c in merchant):
                raise ValueError(f'Row {line}: merchant must contain 1–120 printable characters.')
            try:
                amount = Decimal(row['amount'].strip())
                if not amount.is_finite() or amount.as_tuple().exponent < -2 or abs(amount) > 1000000:
                    raise InvalidOperation
                cents = int(amount * 100)
            except (InvalidOperation, ValueError):
                raise ValueError(f'Row {line}: amount must be a number with at most two decimal places, up to 1,000,000.') from None
            category = row.get('category', '').strip() or guess_category(merchant)
            if category not in CATEGORIES:
                raise ValueError(f'Row {line}: unsupported category. Choose one listed in the sample instructions.')
            rows.append({'date': day, 'merchant': merchant, 'cents': cents, 'category': category})
        if not rows:
            raise ValueError('The CSV has no transactions.')
        return rows
    except (UnicodeError, csv.Error):
        raise ValueError('Upload a valid UTF-8 CSV file.') from None

def analyse(rows):
    duplicates = defaultdict(list)
    merchants = defaultdict(list)
    for row in rows:
        key = merchant_key(row['merchant'])
        duplicates[(row['date'], key, row['cents'])].append(row['id'])
        if row['cents'] > 0:
            merchants[key].append(row)
    duplicate_ids = {i for group in duplicates.values() if len(group) > 1 for i in group}
    recurring = []
    for key, group in merchants.items():
        # Collapse same-day same-amount copies so duplicates do not create a pattern.
        unique = {(r['date'], r['cents']): r for r in group}
        group = sorted(unique.values(), key=lambda r: r['date'])
        if len(group) < 3:
            continue
        last = group[-3:]
        intervals = [(date.fromisoformat(last[i+1]['date']) - date.fromisoformat(last[i]['date'])).days for i in (0, 1)]
        amounts = [r['cents'] for r in last]
        if all(25 <= gap <= 35 for gap in intervals) and max(amounts) <= min(amounts) * Decimal('1.2'):
            recurring.append({'merchant': last[-1]['merchant'], 'monthly_cents': amounts[-1],
                              'annual_cents': amounts[-1] * 12,
                              'evidence': 'Three payments, 25–35 days apart, with similar amounts.'})
    for row in rows:
        row['duplicate'] = row['id'] in duplicate_ids
    return rows, recurring

def safe_csv_cell(value):
    # Stop untrusted merchant text being interpreted as spreadsheet formulas.
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
