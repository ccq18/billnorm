import ast
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from billnorm.__main__ import ALIASES, main, normalize, read_records, run


class BillnormTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source = root / "input"
        self.source.mkdir()
        self.output = root / "output"

    def report(self, name):
        with (self.output / name).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_encoding_and_multiline_record(self):
        records, errors = read_records('甲,乙\r\n"多\r\n行",2\r\n3,4'.encode("gb18030"))
        self.assertEqual(records, [(1, ["甲", "乙"]), (2, ["多\r\n行", "2"]), (4, ["3", "4"])])
        self.assertEqual(errors, [])

    def test_zero_and_conflicting_direction(self):
        indices = {field: index for index, field in enumerate(ALIASES["alipay"])}
        row = ["2026-01-01", "-0.0", "支出", "店", "  午餐  "]
        item = normalize(row, indices, "alipay", "a.csv", 2)
        self.assertEqual((item.amount, item.direction, item.raw_memo), ("0.00", "zero", "  午餐  "))
        row[1:3] = ["-2", "收入"]
        with self.assertRaisesRegex(ValueError, "amount_direction_conflict"):
            normalize(row, indices, "alipay", "a.csv", 2)

    def test_same_file_repeated_rows_and_malformed_tail(self):
        header = "交易时间,金额,收/支,交易对方,商品说明\n"
        transaction = "2026-01-01,2,支出,店,午餐\n"
        (self.source / "a.csv").write_text(
            header + transaction * 2 + '"broken\nstill broken\n', encoding="utf-8-sig"
        )
        (self.source / "b.csv").write_text(header + transaction, encoding="utf-8")
        self.assertEqual(run(self.source, self.output, None, None), 0)
        self.assertEqual(len(self.report("ledger.csv")), 3)
        self.assertEqual([row["reason"] for row in self.report("audit.csv")], [
            "header", "normalized", "suspect_duplicate", "csv_parse_error",
            "unparsed_after_csv_error", "header", "suspect_duplicate",
        ])

    def normalize_files(self, dedupe=None):
        argv = ["billnorm", "--input", str(self.source), "--output", str(self.output),
                "--generic-map", "samples/generic-map.json"]
        if dedupe is not None:
            argv.extend(["--dedupe", dedupe])
        with patch("sys.argv", argv):
            self.assertEqual(main(), 0)

    def test_soft_matches_with_different_time_currency_or_source(self):
        (self.source / "a.csv").write_text(
            "Day,Net,Who,Note,CCY\n"
            "2026-01-01 08:00:00,-8,虚构店,午餐,CNY\n"
            "2026-01-01T08:30:00,-8,虚构店,午餐,CNY\n"
            "2026-01-01 08:00:00,-8,虚构店,午餐,USD\n"
            "2026-01-01,-8,虚构店,午餐,CNY\n",
            encoding="utf-8",
        )
        (self.source / "b.csv").write_text(
            "交易时间,金额,收/支,交易对方,商品说明\n"
            "2026-01-01 08:00:00,8,支出,虚构店,午餐\n",
            encoding="utf-8",
        )
        for mode in (None, "drop-exact"):
            with self.subTest(mode=mode):
                self.normalize_files(mode)
                ledger = self.report("ledger.csv")
                self.assertEqual([row["date"] for row in ledger], ["2026-01-01"] * 5)
                self.assertEqual(
                    [(row["time"], row["currency"], row["source"]) for row in ledger],
                    [("08:00:00", "CNY", "generic"), ("08:30:00", "CNY", "generic"),
                     ("08:00:00", "USD", "generic"), ("", "CNY", "generic"),
                     ("08:00:00", "CNY", "alipay")],
                )
                suspects = [row for row in self.report("audit.csv")
                            if row["reason"] == "suspect_duplicate"]
                self.assertEqual(
                    [(row["status"], row["ledger_row"], row["matched_file"],
                      row["matched_source_row"]) for row in suspects],
                    [("mapped", str(number), "a.csv", "2") for number in range(2, 6)],
                )

    def test_date_only_exact_match_and_opposite_sign(self):
        header = "Day,Net,Who,Note,CCY\n"
        (self.source / "a.csv").write_text(
            header + "2026-01-01,-8,虚构店, 鞋 ,CNY\n", encoding="utf-8",
        )
        (self.source / "b.csv").write_text(
            header + "2026-01-01,-8.00,虚构店,鞋,CNY\n2026-01-01,+8,虚构店,鞋,CNY\n",
            encoding="utf-8",
        )
        for mode, amounts, status, reason, ledger_row in (
            ("keep", ["-8.00", "-8.00", "8.00"], "mapped", "suspect_duplicate", "2"),
            ("drop-exact", ["-8.00", "8.00"], "duplicate", "exact_same_source_record", "1"),
        ):
            with self.subTest(mode=mode):
                self.normalize_files(mode)
                ledger = self.report("ledger.csv")
                self.assertEqual([(row["time"], row["amount"]) for row in ledger],
                                 [("", amount) for amount in amounts])
                match, refund = [row for row in self.report("audit.csv")
                                 if row["source_file"] == "b.csv" and row["status"] != "skipped"]
                self.assertEqual((match["status"], match["reason"], match["ledger_row"],
                                  match["matched_file"], match["matched_source_row"]),
                                 (status, reason, ledger_row, "a.csv", "2"))
                self.assertEqual(refund["reason"], "normalized")

    def test_identical_file_reimport(self):
        data = (
            "SYNTHETIC\nDay,Net,Who,Note,CCY\n"
            "2026-01-01 08:00:00,-8,虚构店,午餐,CNY\n"
            "2026-01-01 08:30:00,-8,虚构店,午餐,CNY\n"
        ).encode("utf-8")
        for filename in ("a.csv", "b.csv"):
            (self.source / filename).write_bytes(data)
        for mode, files, status, reason, ledger_rows, matched_rows in (
            (None, ["a.csv", "a.csv", "b.csv", "b.csv"], "mapped", "suspect_duplicate",
             ["3", "4"], ["3", "3"]),
            ("drop-exact", ["a.csv", "a.csv"], "duplicate", "identical_file_reimport",
             ["1", "2"], ["3", "4"]),
        ):
            with self.subTest(mode=mode):
                self.normalize_files(mode)
                self.assertEqual([row["source_file"] for row in self.report("ledger.csv")], files)
                matches = [row for row in self.report("audit.csv")
                           if row["source_file"] == "b.csv" and row["status"] != "skipped"]
                self.assertEqual(
                    [(row["status"], row["reason"], row["ledger_row"],
                      row["matched_file"], row["matched_source_row"]) for row in matches],
                    [(status, reason, number, "a.csv", matched)
                     for number, matched in zip(ledger_rows, matched_rows)],
                )

    def test_runtime_imports_are_offline_stdlib(self):
        allowed = {"argparse", "collections", "csv", "dataclasses", "datetime",
                   "decimal", "hashlib", "io", "json", "pathlib", "re", "tempfile"}
        for path in Path("billnorm").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name, allowed)
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed)

    def test_unreadable_file_does_not_stop_batch(self):
        inaccessible = self.source / "a.csv"
        inaccessible.touch()
        (self.source / "b.csv").write_text("交易时间,金额,收/支,交易对方,商品说明\n",
                                          encoding="utf-8")
        original_open = Path.open

        def open_test_file(path, *args, **kwargs):
            if path == inaccessible:
                raise PermissionError("test unreadable input")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", open_test_file):
            self.assertEqual(run(self.source, self.output, None, None), 0)
        self.assertEqual([row["reason"] for row in self.report("audit.csv")],
                         ["unreadable_file", "header"])

    def test_no_recognized_file_writes_audit(self):
        (self.source / "bad.xlsx").write_bytes(b"test")
        (self.source / "bad.csv").write_text("?,?\n1,2\n", encoding="utf-8")
        with patch("sys.argv", ["billnorm", "--input", str(self.source),
                               "--output", str(self.output)]):
            self.assertEqual(main(), 1)
        self.assertEqual(self.report("ledger.csv"), [])
        self.assertEqual([row["reason"] for row in self.report("audit.csv")],
                         ["header_mismatch", "header_mismatch", "unsupported_format"])

    def test_alipay_web_trade_record_headers(self):
        header = (
            '交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,'
            '交易对方,商品名称,金额（元）,收/支,交易状态,服务费（元）,成功退款（元）,备注,资金状态'
        )
        preamble = chr(10).join([
            'SYNTHETIC 支付宝网页交易记录样例',
            '账号已脱敏',
            '起始时间：[2026-01-01]',
            '导出时间：[2026-01-31]',
            '',
        ])
        row1 = ('SYN-1,M-1,2026-01-08 09:15:00,2026-01-08 09:15:01,2026-01-08 09:15:01,'
                '店铺,在线支付,虚构文具店,笔记本,18.00,支出,交易成功,0.00,0.00,,已支出')
        row2 = ('SYN-2,M-2,2026-01-09 11:00:00,2026-01-09 11:00:02,2026-01-09 11:00:02,'
                '店铺,在线支付,虚构文具店,退款,18.00,收入,退款成功,0.00,18.00,,已收入')
        body = preamble + header + chr(10) + row1 + chr(10) + row2 + chr(10)
        (self.source / 'web.csv').write_text(body, encoding='gb18030')
        self.assertEqual(run(self.source, self.output, None, None), 0)
        ledger = self.report('ledger.csv')
        self.assertEqual(
            [(row['date'], row['time'], row['amount'], row['direction'], row['source'],
              row['counterparty'], row['raw_memo']) for row in ledger],
            [
                ('2026-01-08', '09:15:00', '-18.00', 'expense', 'alipay', '虚构文具店', '笔记本'),
                ('2026-01-09', '11:00:00', '18.00', 'income', 'alipay', '虚构文具店', '退款'),
            ],
        )
        reasons = [row['reason'] for row in self.report('audit.csv')]
        self.assertEqual(reasons[:5], ['preamble', 'preamble', 'preamble', 'preamble', 'header'])
        self.assertIn('normalized', reasons)
