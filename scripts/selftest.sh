#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
scratch=$(mktemp -d "$PWD/.selftest.XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
python3 -m unittest discover -s tests -v
for run in first second; do
    python3 -m billnorm --input samples/month --output "$scratch/$run" \
        --generic-map samples/generic-map.json
done
for report in ledger.csv audit.csv summary.txt; do
    cmp "$scratch/first/$report" "$scratch/second/$report"
done
python3 - "$scratch/first" <<'PY'
import csv
from collections import defaultdict
from decimal import Decimal
import hashlib
from pathlib import Path
import sys

out = Path(sys.argv[1])
with (out / "ledger.csv").open(encoding="utf-8", newline="") as handle:
    ledger = list(csv.DictReader(handle))
with (out / "audit.csv").open(encoding="utf-8", newline="") as handle:
    audit = list(csv.DictReader(handle))
assert len(ledger) == 8, ledger
totals = defaultdict(Decimal)
for row in ledger:
    totals[row["source"], row["currency"]] += Decimal(row["amount"])
assert totals == {
    ("alipay", "CNY"): Decimal("22.50"),
    ("wechat", "CNY"): Decimal("-7.75"),
    ("generic", "CNY"): Decimal("-10.25"),
    ("generic", "USD"): Decimal("2.00"),
}, totals
assert all(row["source_file"] != "04-broken.csv" for row in ledger)
assert [row["reason"] for row in audit if row["source_file"] == "04-broken.csv"] == [
    "header_mismatch", "header_mismatch", "header_mismatch"
]
breakfast = [row for row in ledger if row["counterparty"] == "虚构晨光铺"]
assert [(row["date"], row["time"], row["source"], row["amount"]) for row in breakfast] == [
    ("2026-01-02", "08:00:00", "alipay", "-12.50"),
    ("2026-01-02", "08:30:00", "wechat", "-12.50"),
]
suspect, = [row for row in audit if row["reason"] == "suspect_duplicate"]
assert (suspect["status"], suspect["source_file"], suspect["source_row"],
        suspect["matched_file"], suspect["matched_source_row"], suspect["ledger_row"]) == (
    "mapped", "02-wechat.csv", "3", "01-alipay.csv", "4", "4"
)
assert any(row["reason"] == "preamble" for row in audit)
assert any(row["reason"] == "bad_amount" for row in audit)
summary = (out / "summary.txt").read_text(encoding="utf-8")
for item in ("input_files=4", "parsed_files=3", "ledger_rows=8", "mapped=8",
             "duplicate=0", "skipped=7", "rejected=4",
             "food CNY -35.25", "refund CNY 13.00", "refund USD 2.00",
             "transfer CNY 30.00", "transport CNY -3.25"):
    assert item in summary.splitlines(), item
for path in Path("samples/month").iterdir():
    assert f'{hashlib.sha256(path.read_bytes()).hexdigest()}  "{path.name}"' in summary
PY
for phrase in SYNTHETIC 离线 不要把账单粘贴进聊天机器人; do
    grep -Fq "$phrase" README.md
done
echo "billnorm selftest passed"
