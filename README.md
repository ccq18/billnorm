# billnorm

billnorm 是一个本地批量对账工具。它读取一个文件夹里的 CSV 账单，输出统一的交易表、审计表和汇总。运行全程离线，无遥测，无需账号、API 密钥或第三方 Python 包。它不提供记账服务或财务建议。

**不要把账单粘贴进聊天机器人。** 账单可能包含姓名、交易对象、金额和消费习惯。用 billnorm 可以在本机批量处理整个文件夹，使用相同输入和映射重跑得到相同结果，并通过审计表追溯每笔导入或拒绝的记录。请同样妥善保管生成的报表。

本项目采用 MIT 许可证，见 `LICENSE`。仓库中的账单样例均为原创 **SYNTHETIC（合成）** 数据。兼容性仅通过合成样例验证，尚未用真实微信/支付宝导出文件验收。

## 运行

需要 Python 3.8 或更高版本。从 Release 下载 ZIP 后解压，进入解压所得的 `billnorm/` 目录；或克隆本仓库后在仓库根目录运行。以下命令会把样例报告写入单独的目录。

```sh
python3 -m billnorm --input samples/month --output ./billnorm-out \
    --generic-map samples/generic-map.json
sh scripts/selftest.sh
```

处理自己的文件时，替换输入和输出路径。只导入符合内置表头的支付宝和微信账单时，省略 `--generic-map`。网页版支付宝「交易记录」样例在 `samples/web/05-alipay-web.csv`。工具读取输入目录直属文件，跳过子目录和作为配置使用的 JSON 文件。输出目录须与输入目录不同，同名报告会被更新。

## 从 App 导出个人对账文件

- 微信支付：打开「我」→「服务」→「钱包」→「账单」，在「常见问题」中找「下载账单」，选择「用于个人对账」，按 App 提示选择时间段并保存文件。[财付通说明](https://pay.wechatpay.cn/xdc/weardeviceagreementweb/html/agreement/tenpay_user_privacy)
- 支付宝：打开「我的」→「账单」→右上角更多菜单，找「开具交易流水证明」，选择「用于个人对账」，按 App 提示选择时间段并保存文件。[公开导出说明](https://help.icostapp.com/guide/import/alipay.html)

菜单名称可能随版本变化，以 App 当前页面为准。下载得到 ZIP 时，请先在本机解压。MVP 仅支持 CSV。若导出为 Excel，用本地表格软件打开含交易明细的工作表，另存为逗号分隔的 **CSV UTF-8**，保留表头及金额精度。支持 UTF-8（含 BOM）和 GB18030，统一输出 UTF-8。

## 表头与金额规则

下列列名是根据常见公开的个人对账导出说明做出的**假设**，可能随平台版本变化。支付宝存在至少两种公开布局：App「用于个人对账」常见为 `交易时间` / `金额` / `商品说明`；网页「交易记录」常见为 `交易创建时间` / `金额（元）` / `商品名称`。两种都识别为来源 `alipay`。尚未用真实导出文件验收。每个必需列必须恰好出现一次，可以重排列、保留附加列。识别表头后，表头前的记录归为前言。未知表头的记录会被审计为 `header_mismatch`，工具不会猜测金额列。

| 格式 | 必需表头（顺序任意） |
| --- | --- |
| 支付宝 App「用于个人对账」样式 | `交易时间`、`金额`、`收/支`、`交易对方`、`商品说明` |
| 支付宝网页「交易记录」样式 | `交易创建时间`、`金额（元）`、`收/支`、`交易对方`、`商品名称`（日期取「交易创建时间」，不是「付款时间」） |
| 微信样式 | `交易时间`、`金额(元)`、`收/支`、`交易对方`、`商品` |
| 通用 CSV | 通过 JSON 明确映射 `date`、`amount`、`counterparty`、`raw_memo`，可选 `currency` 和 `direction` |

通用映射示例为 `{"date":"Day","amount":"Net","counterparty":"Who","raw_memo":"Note","currency":"CCY"}`。键是规范字段，值是输入列名。一次运行可提供一份通用映射。内置表头优先。

- 日期接受 `YYYY-MM-DD` 或带秒的 ISO 本地日期时间（日期与时间之间用空格或 `T`）。输出的 `date` 保留日历日期，`time` 以 `HH:MM:SS` 保留输入中的时间，仅有日期时留空。
- 金额接受整数或最多两位小数，可带符号、人民币符号和规范的逗号千位分隔。CSV 中含逗号的值需加引号。收入为正，支出为负，零标为 `zero`。
- 映射了方向列时，允许「收入」「支出」或 `income`、`expense`、`in`、`out`。无符号金额按方向处理，显式符号与方向冲突的非零金额会被拒绝。无方向列时使用金额本身的符号。非收支方向（例如「不计收支」）会被拒绝。
- 币种默认 CNY。通用映射可指定一个三字母币种列。输出按币种分别加总，无汇率换算。
- 交易状态等附加列不参与筛选。分类仅按交易对象和备注中的关键词猜测，匹配顺序为退款、转账、交通、餐饮，其余为 `other`。请以原始账单核对。

## 去重及审计

按文件名排序读取，文件内按原顺序处理。默认模式 `--dedupe keep` 将每条成功规范化的记录写入账本，包括重新导入的文件。当天的带符号金额、交易对象和去掉首尾空白的备注相同时，后续记录会标记为疑似重复，即使时间、币种或来源不同。交易对象会去掉首尾空白，账本保留原始备注。退款和支出的金额符号相反时，两者均保留。

显式指定 `--dedupe drop-exact` 才会省略符合以下条件的记录。

- 后续文件与已导入的普通文件具有相同的原始字节 SHA-256。该文件中成功规范化的记录标为 `identical_file_reimport`。
- 记录与较早保留的记录具有相同的日期、时间、币种、带符号金额、交易对象、去掉首尾空白的备注、方向和来源。时间必须同时为空或完全相同。此规则也适用于同一文件内的记录，审计原因是 `exact_same_source_record`。

来源字段为 `alipay`、`wechat` 或 `generic`。交易 ID 不参与比较，也不假定不同平台的交易 ID 可以对应。即使使用 `drop-exact`，同一来源的两笔真实交易也可能在所有比较字段上完全相同，导致后出现的一笔从账本省略，因此该模式需要显式启用。

- `ledger.csv` 记录交易日期、时间、带符号金额、币种、方向、来源、交易对象、原始备注、猜测分类和源文件位置。
- `audit.csv` 记录导入、跳过、拒绝或重复的行。疑似重复行的 `status` 为 `mapped`，`reason` 为 `suspect_duplicate`，`ledger_row` 指向该行自己的账本位置，`matched_file` 和 `matched_source_row` 指向较早保留的匹配记录。被省略的行标为 `duplicate`，相关位置字段指向保留的记录。源行号以 1 开始，为 CSV 记录的起始物理行。账本行号从第一笔交易算起，不含表头。文件不可读或格式不支持时使用文件级审计。遇到无法继续解析的 CSV，记录故障行并将剩余物理行标为未解析。
- `summary.txt` 记录文件和行的计数、按来源及猜测分类分组的各币种净额，以及文件原始字节的 SHA-256。不可读文件的哈希标为 `unavailable`。

至少一个 CSV 表头识别成功且报表写入成功时返回 0，即使所有数据行都被拒绝。没有识别到任何 CSV 时仍写报表并返回 1。参数、映射或输出错误返回 2。账本保留账单里的任意文本，在表格软件打开时请将来源、交易对象、备注等文本列按纯文本导入。

## English

Offline Python 3 CLI for batch normalization of local bank or payment CSVs. The three reports contain the normalized ledger, row-level audit, and currency-separated totals with file hashes. All included statements are synthetic. By default, every normalized row is kept and soft matches are flagged in the audit. The ledger preserves the time when provided. Opt in to `--dedupe drop-exact` to omit byte-identical re-imports or records matching on all documented same-source fields. Distinct payments may share those fields. Licensed under MIT. Run `sh scripts/selftest.sh` from the extracted directory.
