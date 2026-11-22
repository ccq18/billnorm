# billnorm acceptance (public excerpt)

Updated 2026-11-23 01:30 Asia/Shanghai.

Independent selftest (10 unit tests plus the official monthly sample) passed after adding the documented Alipay web trade-record header layout. Official monthly sample results are unchanged: 8 ledger rows by default; both synthetic breakfast payments are kept; the later WeChat row is flagged suspect_duplicate.

A separate synthetic fixture samples/web/05-alipay-web.csv uses the publicly documented web columns (交易创建时间, 金额（元）, 商品名称) and is recognized as source alipay. That fixture is original synthetic data, not a real export.

All packaged samples are SYNTHETIC. This is not a claim about real personal bills, accuracy, or paid use.
