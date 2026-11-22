# Synthetic before / after excerpt

Inputs are labeled SYNTHETIC. They are not real statements.

**Input A** (`samples/month/01-alipay.csv`, row 4):

`2026-01-02 08:00:00,虚构晨光铺,早餐,支出,12.50`

**Input B** (`samples/month/02-wechat.csv`, row 3):

`2026-01-02 08:30:00,虚构晨光铺,早餐,支出,¥12.50`

**Default output (`--dedupe keep`)**: both rows appear in `ledger.csv`. Clock times are preserved (`08:00:00` and `08:30:00`). The later WeChat row is still in the ledger; `audit.csv` marks it `mapped` / `suspect_duplicate` and points at the Alipay row. Nothing is dropped solely because the calendar date, signed amount, counterparty, and memo match.

**`--dedupe drop-exact`**: these two rows are still both kept, because the source fields differ (`alipay` vs `wechat`) and the times differ. The tool does not assume a WeChat payment and an Alipay payment are the same transaction.
