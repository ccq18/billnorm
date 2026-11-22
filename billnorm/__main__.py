"""Normalize local statement CSV files."""
import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory


FIELDS = (
    "date", "time", "amount", "currency", "direction", "source", "counterparty",
    "raw_memo", "category_guess", "source_file", "source_row",
)
AUDIT_FIELDS = (
    "source_file", "source_row", "status", "reason", "ledger_row",
    "matched_file", "matched_source_row",
)
ALIASES = {
    "alipay": dict(date="交易时间", amount="金额", direction="收/支",
                   counterparty="交易对方", raw_memo="商品说明"),
    "wechat": dict(date="交易时间", amount="金额(元)", direction="收/支",
                   counterparty="交易对方", raw_memo="商品"),
}
# Documented web "交易记录" export (public importers). Same source=alipay.
ALIPAY_WEB_ALIASES = dict(
    date="交易创建时间", amount="金额（元）", direction="收/支",
    counterparty="交易对方", raw_memo="商品名称",
)


def builtin_schemas(generic=None):
    schemas = [
        ("alipay", ALIASES["alipay"]),
        ("wechat", ALIASES["wechat"]),
        ("alipay", ALIPAY_WEB_ALIASES),
    ]
    if generic is not None:
        schemas.append(("generic", generic))
    return schemas
REQUIRED = {"date", "amount", "counterparty", "raw_memo"}
ALLOWED = REQUIRED | {"direction", "currency"}
AMOUNT = re.compile(r"[+-]?(?:[0-9]{1,18}|[0-9]{1,3}(?:,[0-9]{3}){1,5})(?:\.[0-9]{1,2})?")
CATEGORIES = {
    "refund": ("退款", "退货", "refund"),
    "transfer": ("转账", "transfer"),
    "transport": ("地铁", "公交", "打车", "metro", "bus fare", "taxi"),
    "food": ("早餐", "午餐", "晚餐", "咖啡", "coffee", "lunch", "dinner"),
}


@dataclass(frozen=True)
class Transaction:
    date: str
    time: str
    amount: str
    currency: str
    direction: str
    source: str
    counterparty: str
    raw_memo: str
    category_guess: str
    source_file: str
    source_row: int

    def soft_key(self):
        return self.date, self.amount, self.counterparty, self.raw_memo.strip()

    def exact_key(self):
        return (*self.soft_key(), self.time, self.currency, self.direction, self.source)


def load_mapping(path):
    if path is None:
        return None
    mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(mapping, dict)
        or not REQUIRED <= mapping.keys() <= ALLOWED
        or any(not isinstance(value, str) or not value.strip() for value in mapping.values())
        or len(set(mapping.values())) != len(mapping)
    ):
        raise ValueError("invalid generic mapping: see README")
    return mapping


def read_records(data):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return [], [(None, "decode_error")]
    if "\x00" in decoded:
        return [], [(None, "binary_or_unsupported_encoding")]
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    records = []
    start = 1
    try:
        for cells in reader:
            records.append((start, cells))
            start = reader.line_num + 1
    except csv.Error:
        last = sum(1 for _ in io.StringIO(decoded, newline=""))
        errors = [(start, "csv_parse_error")]
        errors.extend((row, "unparsed_after_csv_error") for row in range(start + 1, last + 1))
        return records, errors
    return records, []


def find_header(records, schemas):
    for position, (_, cells) in enumerate(records):
        names = [cell.strip() for cell in cells]
        for source, mapping in schemas:
            if all(names.count(name) == 1 for name in mapping.values()):
                return position, source, {field: names.index(name) for field, name in mapping.items()}
    return None


def normalize(cells, indices, source, filename, row):
    def value(field, default=""):
        return cells[indices[field]] if field in indices else default

    timestamp = value("date").strip()
    clock_time = ""
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", timestamp):
            day = date.fromisoformat(timestamp)
        elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}", timestamp):
            local_timestamp = datetime.fromisoformat(timestamp)
            day = local_timestamp.date()
            clock_time = local_timestamp.time().isoformat(timespec="seconds")
        else:
            raise ValueError
    except ValueError:
        raise ValueError("bad_date") from None

    raw_amount = value("amount").strip()
    if raw_amount.startswith(("¥", "￥")):
        raw_amount = raw_amount[1:].strip()
    if not AMOUNT.fullmatch(raw_amount):
        raise ValueError("bad_amount")
    amount = Decimal(raw_amount.replace(",", ""))
    direction = value("direction").strip().casefold()
    if "direction" in indices:
        if direction in ("收入", "income", "in"):
            if amount < 0:
                raise ValueError("amount_direction_conflict")
        elif direction in ("支出", "expense", "out"):
            if raw_amount.startswith("+") and amount > 0:
                raise ValueError("amount_direction_conflict")
            amount = -abs(amount)
        else:
            raise ValueError("unsupported_direction")

    currency = value("currency", "CNY").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("bad_currency")
    if amount == 0:
        amount = abs(amount)
    direction = "income" if amount > 0 else "expense" if amount < 0 else "zero"
    counterparty = value("counterparty").strip()
    memo = value("raw_memo")
    description = f"{counterparty} {memo}".casefold()
    category = next(
        (name for name, words in CATEGORIES.items() if any(word in description for word in words)),
        "other",
    )
    return Transaction(day.isoformat(), clock_time, f"{amount:.2f}", currency, direction,
                       source, counterparty, memo, category, filename, row)


def process_file(path, schemas, audit, hashes):
    filename = path.name
    try:
        with path.open("rb") as handle:
            data = handle.read()
    except OSError:
        audit(filename, None, "rejected", "unreadable_file")
        hashes[filename] = "unavailable"
        return False, []
    hashes[filename] = hashlib.sha256(data).hexdigest()
    if path.suffix.lower() != ".csv":
        audit(filename, None, "rejected", "unsupported_format")
        return False, []

    records, errors = read_records(data)
    header = find_header(records, schemas)
    if header is None:
        for row, _ in records:
            audit(filename, row, "rejected", "header_mismatch")
        if not records and not errors:
            audit(filename, None, "rejected", "header_mismatch")
        for row, reason in errors:
            audit(filename, row, "rejected", reason)
        return False, []

    position, source, indices = header
    header_row, header_cells = records[position]
    width = len(header_cells)
    for row, _ in records[:position]:
        audit(filename, row, "skipped", "preamble")
    audit(filename, header_row, "skipped", "header")
    transactions = []
    for row, cells in records[position + 1:]:
        if not any(cell.strip() for cell in cells):
            audit(filename, row, "skipped", "blank_row")
        elif len(cells) != width:
            audit(filename, row, "rejected", "column_count_mismatch")
        else:
            try:
                transactions.append(normalize(cells, indices, source, filename, row))
            except ValueError as exc:
                audit(filename, row, "rejected", str(exc))
    for row, reason in errors:
        audit(filename, row, "rejected", reason)
    return True, transactions


def write_outputs(output, ledger, audits, summary):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output) as temporary:
        staging = Path(temporary)
        for filename, fields, rows in (
            ("ledger.csv", FIELDS, (asdict(item) for item in ledger)),
            ("audit.csv", AUDIT_FIELDS, audits),
        ):
            with (staging / filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\r\n")
                writer.writeheader()
                writer.writerows(rows)
        (staging / "summary.txt").write_text(summary, encoding="utf-8")
        for filename in ("ledger.csv", "audit.csv", "summary.txt"):
            (staging / filename).replace(output / filename)


def run(input_dir, output, generic, mapping_file, dedupe="keep"):
    schemas = builtin_schemas(generic)
    ledger, audits = [], []
    hashes, soft_matches, exact_matches, imported_files = {}, {}, {}, {}
    parsed = 0

    def audit(filename, row, status, reason, ledger_row=None, match=None):
        audits.append(dict(
            source_file=filename, source_row=row, status=status, reason=reason,
            ledger_row=ledger_row, matched_file=match.source_file if match else None,
            matched_source_row=match.source_row if match else None,
        ))

    for path in sorted(input_dir.iterdir(), key=lambda entry: entry.name):
        if path == mapping_file or (not path.is_symlink() and path.is_dir()):
            continue
        if path.is_symlink() or not path.is_file():
            hashes[path.name] = "unavailable"
            audit(path.name, None, "rejected", "not_regular_file")
            continue
        recognized, transactions = process_file(path, schemas, audit, hashes)
        parsed += recognized
        digest = hashes[path.name]
        previous_import = imported_files.get(digest)
        imported_rows = {}
        for item in transactions:
            if dedupe == "drop-exact":
                if previous_import is not None:
                    audit(item.source_file, item.source_row, "duplicate",
                          "identical_file_reimport", *previous_import[item.source_row])
                    continue
                exact_key = item.exact_key()
                exact_match = exact_matches.get(exact_key)
                if exact_match is not None:
                    audit(item.source_file, item.source_row, "duplicate",
                          "exact_same_source_record", *exact_match)
                    imported_rows[item.source_row] = exact_match
                    continue

            ledger.append(item)
            ledger_row = len(ledger)
            match = (ledger_row, item)
            soft_key = item.soft_key()
            soft_match = soft_matches.get(soft_key)
            if soft_match is None:
                soft_matches[soft_key] = match
                audit(item.source_file, item.source_row, "mapped", "normalized", ledger_row)
            else:
                audit(item.source_file, item.source_row, "mapped", "suspect_duplicate",
                      ledger_row, soft_match[1])
            if dedupe == "drop-exact":
                exact_matches[exact_key] = match
                imported_rows[item.source_row] = match
        if dedupe == "drop-exact" and recognized and previous_import is None:
            imported_files[digest] = imported_rows

    audits.sort(key=lambda row: (row["source_file"], row["source_row"] or 0))
    counts = Counter(row["status"] for row in audits)
    source_totals, category_totals = defaultdict(Decimal), defaultdict(Decimal)
    for item in ledger:
        source_totals[item.source, item.currency] += Decimal(item.amount)
        category_totals[item.category_guess, item.currency] += Decimal(item.amount)
    lines = [f"input_files={len(hashes)}", f"parsed_files={parsed}", f"ledger_rows={len(ledger)}"]
    lines.extend(f"{status}={counts[status]}" for status in ("mapped", "duplicate", "skipped", "rejected"))
    for title, totals in (("source_totals", source_totals), ("category_guess_totals", category_totals)):
        lines.append(f"\n[{title}]")
        lines.extend(f"{name} {currency} {amount:.2f}" for (name, currency), amount in sorted(totals.items()))
    lines.append("\n[sha256]")
    lines.extend(f"{digest}  {json.dumps(name, ensure_ascii=False)}" for name, digest in hashes.items())
    write_outputs(output, ledger, audits, "\n".join(lines) + "\n")
    return 0 if parsed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="directory containing statements")
    parser.add_argument("--output", type=Path, required=True, help="directory for the three reports")
    parser.add_argument("--generic-map", type=Path, help="JSON column mapping for generic CSV")
    parser.add_argument("--dedupe", choices=("keep", "drop-exact"), default="keep",
                        help="keep all rows or drop exact duplicates (default: keep)")
    args = parser.parse_args()
    try:
        input_dir, output = args.input.resolve(), args.output.resolve()
        if not input_dir.is_dir():
            parser.error("--input must be a directory")
        if input_dir == output:
            parser.error("--input and --output must be different directories")
        mapping_file = args.generic_map.resolve() if args.generic_map else None
        if mapping_file in (output / name for name in ("ledger.csv", "audit.csv", "summary.txt")):
            parser.error("--generic-map must be separate from the output reports")
        generic = load_mapping(mapping_file)
        return run(input_dir, output, generic, mapping_file, args.dedupe)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"billnorm: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
