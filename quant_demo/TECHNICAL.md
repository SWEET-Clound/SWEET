# A股宽基 ETF 量化 Demo 技术文档

## 1. 系统边界

这是一个个人学习用途的单标的日频程序：

- 标的：上交所 `510300` 沪深300ETF；
- 策略：SMA20/SMA60，只做多或空仓；
- 流程：在线获取数据、离线回测、本地纸面账本、人工复盘；
- 不连接券商，不读取账户，不发送真实订单；
- 不提供分钟行情、股票池、机器学习、自动调参或 Web 界面。

代码集中在一个脚本中。当前需求没有证据支持回测框架、数据库、数据源接口层或策略基类，新增这些结构只会增加故障面。

## 2. 文件结构

```text
quant_demo/
├── .gitignore
├── LEARNING.md
├── REVIEW.md
├── TECHNICAL.md
├── quant_demo.py
└── requirements.txt
```

首次运行后生成的内容位于 `quant_demo/runtime/`，整个目录由 Git 忽略：

```text
runtime/
├── cache/
│   └── 510300/
│       ├── <sha256>.csv
│       └── current.json
├── backtest_510300_20_60_<config-tag>_<data-tag>/
│   ├── equity.csv
│   ├── trades.csv
│   └── summary.json
├── robustness_510300_<config-tag>_<data-tag>/
│   ├── grid.csv
│   └── summary.json
└── paper_510300_20_60.csv
```

仓库不包含合成或真实行情 CSV。离线验证数据由 `self-check` 在内存中构造，避免维护 fixture，也避免再分发第三方行情。

## 3. Windows 环境安装

当前机器存在 `py.exe` 启动器，但尚未安装 Python 运行时。请先从 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/) 安装 64 位 Python 3.13，并包含 launcher 与 pip。

在仓库根目录执行：

```powershell
py -0p
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\quant_demo\requirements.txt
```

直接依赖固定为：

```text
pandas==3.0.5
akshare==1.18.94
```

AKShare 会安装自己的传递依赖；本项目不重复声明它们。

## 4. 命令行

以下命令均从仓库根目录运行：

```powershell
# 完全离线，不访问网络
.\.venv\Scripts\python.exe .\quant_demo\quant_demo.py self-check

# 获取 510300 历史行情、前复权收盘和累计分红
.\.venv\Scripts\python.exe .\quant_demo\quant_demo.py fetch --symbol 510300 --start 2013-01-01

# 只使用本地缓存执行回测
.\.venv\Scripts\python.exe .\quant_demo\quant_demo.py backtest --symbol 510300

# 只检查 2023 年前训练段的固定 3×3 均线邻域，不选最优参数
.\.venv\Scripts\python.exe .\quant_demo\quant_demo.py robustness --symbol 510300

# 必须成功在线刷新后，推进一次纸面账本
.\.venv\Scripts\python.exe .\quant_demo\quant_demo.py paper --symbol 510300
```

v0 的均线固定为 `fast=20`、`slow=60`，普通回测和纸面命令不提供修改入口；相邻参数只由 `robustness` 的固定网格观察。可覆盖的资金和费用参数为：

```text
--initial-cash 100000
--commission-bps 3
--min-commission 5
--slippage-bps 5
--sell-tax-bps 0
```

使用真实券商费率时应新建纸面账本，不能用新参数续写旧账户历史。

## 5. 公共函数

```python
fetch_market_data(symbol, start, end) -> DataFrame
validate_bars(frame) -> None
make_targets(frame, fast, slow) -> Series
run_backtest(frame, config) -> tuple[DataFrame, DataFrame, dict]
run_robustness(frame, config) -> tuple[DataFrame, dict]
paper_step(frame, journal_path, config) -> dict
main(argv=None) -> int
```

`Config` 是不可变数据类，保存标的、资金、均线、费用、滑点与交易单位。它不是策略插件系统。

v1 明确拒绝 `510300` 以外的代码。CLI 保留 `--symbol` 是为了让命令和账本标识显式可审计，不代表已经支持其他 ETF。

## 6. 数据获取与契约

`fetch_market_data` 调用三个零密钥数据入口：

1. `fund_etf_hist_em(adjust="")`：原始 OHLCV 与成交额；
2. `fund_etf_hist_em(adjust="qfq")`：前复权收盘价；
3. `fund_etf_dividend_sina`：累计现金分红。

标准化后的字段为：

| 字段 | 含义 | 用途 |
| --- | --- | --- |
| `date` | 交易日期 | 排序与成交时序 |
| `open/high/low/close` | 原始价格 | 模拟成交与估值 |
| `volume/amount` | 成交量与成交额 | 数据校验、停牌近似 |
| `adj_close` | 前复权收盘价 | 计算均线信号 |
| `cash_dividend` | 当日每份现金分红 | 账户现金入账 |

写缓存前必须满足：

- 日期合法、唯一、严格递增；
- 原始与前复权历史日期完全一致；
- OHLC、前复权收盘价大于 0；
- `low <= min(open, close) <= max(open, close) <= high`；
- 成交量、成交额和现金分红非负；
- 接口字段发生变化时明确失败。

缓存采用完整、不可变快照而不是尾部拼接，因为新的分红可能导致前复权历史整体重算。CSV 以内容 SHA-256 命名；写完并校验新快照后，只原子替换 `current.json` 指针。若中途失败，旧指针仍引用旧快照；新快照成为可审计的孤立版本，不会破坏当前可用缓存。指针记录来源、上游、AKShare 版本、抓取时间、区间、行数、文件名和 SHA-256。

对固定标的 510300，分红接口空表被视为上游失败；不能把“请求失败”静默解释为“从未分红”。内部辅助函数仍保持简单的数据形态，但 v1 没有其他证券代码的公开入口。

接口返回的区间内分红日期必须能在原始行情中找到，否则抓取失败。当前免费接口只有除息日与累计分红，没有现金实际支付日；Demo 因而在除息日把分红计为可用现金。这是教学近似，会轻微高估除息日至支付日期间的购买力，不能据此把结果升级为可实盘的纸面候选。

数据接口说明见 [AKShare ETF 文档](https://akshare.akfamily.xyz/data/fund/fund_public.html)。AKShare 代码许可证不等于获得上游行情的再分发许可，缓存只用于本地个人研究。

## 7. 信号和成交状态机

### 信号

```text
fast = adj_close 最近 20 日均值
slow = adj_close 最近 60 日均值
target = 1 if fast > slow else 0
```

前 60 个样本的目标固定为 0。相等时空仓，不引入额外止损或回撤规则。

### 时序

循环处理交易日 `t` 时，只允许执行 `t-1` 收盘后产生的目标：

```text
先给 t 日之前已持有的份额记现金分红
-> 读取 t-1 的目标
-> 按 t 日原始开盘价尝试成交
-> 以 t 日原始收盘价估值
-> 保存 t 日收盘后目标
```

因此不存在用同一收盘价同时计算信号和成交的路径。

### 成交

- 买价：`open × (1 + slippage_bps / 10000)`，向上取整到 0.001 元；
- 卖价：`open × (1 - slippage_bps / 10000)`，向下取整到 0.001 元；
- 买入数量：在计入佣金后可负担的最大 100 份整数倍；
- 卖出数量：全部持仓；
- 非零订单佣金：`max(min_commission, notional × commission_bps / 10000)`；
- 卖出额外税费：`notional × sell_tax_bps / 10000`，ETF 默认 0；
- 无有效开盘或成交量为 0 时不成交。

日线无法观察真实盘口。本实现仅对价格全天相同且相对经现金分红调整的前收盘变动至少约 9.5% 的方向性订单进行保守阻断。代码中使用 `ponytail:` 注释标明这个上限；扩展到个股前必须改为可靠的每日涨跌停价与停牌状态。

## 8. 回测输出

`backtest` 只读取现有缓存，不隐式访问网络。终端明确打印缓存抓取时间和数据截止日。

`equity.csv` 包含策略现金、份额、净值、目标、动作，以及买入持有基准净值和双方回撤。`trades.csv` 同时保留已成交和被阻断的订单，字段包括信号日、成交日、方向、数量、价格、金额、费用、状态和原因。

`summary.json` 包含：

- 完整区间与 2023-01-01 以来的留后观察段（holdout）；
- 策略与同成本买入持有；
- 双倍佣金、最低佣金和 20 bps 滑点压力场景；
- 累计收益、CAGR、年化波动、Sharpe、最大回撤、Calmar、在场比例、换手、交易次数和费用；
- 是否通过预先写下的 holdout 研究门槛（字段名 `holdout_gates_passed`）。

holdout 的首日收益以前一交易日收盘净值为基线，但交易、费用和暴露只从 holdout 首日开始统计。该区间由项目作者反复可见，因此不是独立第三方 OOS，只能作为纪律化的留后观察段。

`holdout_gates_passed` 只是机械检查，不等于“纸面候选”。是否进入纸面观察仍需人工确认数据完整性、分红支付时点近似和稳健性结果。

机械门槛只有在研究样本具备资格时才可能为真：历史必须从 2013 年 1 月开始，2023 年前至少有 `slow + 2` 根日线，2023 年起至少有 252 根日线。资格和每个门槛分别写入摘要；短样本不会被包装成通过。零回撤时 Calmar 数学上未定义，JSON 写为 `null`；内部比较将“正 CAGR 且零回撤”视为正无穷，但不会输出非标准 JSON 的 `Infinity`。

压力滑点取 `max(20 bps, 基础滑点)`，佣金与最低佣金为基础值的两倍，因此用户提高基础滑点后，压力配置不会反而更便宜。

`robustness` 固定检查 `(15,20,25) × (50,60,70)` 九组均线，只使用 2023 年前数据，输出累计收益、最大回撤、Calmar、暴露率、交易次数、费用及各指标的 min/median/max。九组都从最长 70 日热身后的共同基线开始，避免短慢线凭更早入场获得不公平优势。它先切断 holdout 再做数值校验，不排名、不选优，也不读取或验证 2023 年后的数值列。

回测和稳健性目录同时包含配置内容和输入快照的短 SHA-256 指纹；改变资金、费用、滑点或行情快照都会写入新目录，避免静默覆盖不可比较的旧结果。完整配置和输入快照 SHA-256 也写入 JSON 摘要。相同配置与快照的重复运行仍会逐文件原子替换，因此程序崩溃时不能保证三个输出文件组的整体事务性；输入和计算是确定性的，重跑同一命令即可修复。

买入持有在 60 日热身完成后的下一开盘买入，使用与策略相同的费用、分红与成交模型。

## 9. 纸面账本

纸面交易只有一个状态源：

```text
runtime/paper_<symbol>_<fast>_<slow>.csv
```

主要字段包括：

```text
as_of_date, signal_date, fill_date, action, qty, exec_price,
fee, dividend, cash, shares, close, equity, target_next,
symbol, initial_cash, fast, slow, commission_bps,
min_commission, slippage_bps, sell_tax_bps, note
```

`signal_date` 是本行尝试执行的待处理信号来源日；`as_of_date` 同时是本行收盘估值日，以及 `target_next` 的生成日。首次 `INIT` 时两者相同。

处理规则：

1. 首次运行只用最新收盘生成待执行目标，写 `INIT`，不伪造历史成交；
2. 数据日期未前进时返回 `no-op`，不追加行；
3. 出现新日线时，只在第一根新日线开盘兑现上次已保存的目标；
4. 漏跑超过一根新日线时记录 `MISSED_WINDOW`，旧待成交指令过期且不按历史开盘补成交；原持仓分红仍入账；
5. 以最新收盘生成新的 `target_next`；
6. 策略或费用参数与账本不一致时拒绝续写；
7. 账本最后日期必须仍能在刷新后的行情中精确找到，防止上游历史修订后用错误前收盘继续计算；
8. 在线刷新、数据校验或完整日线检查失败时，账本保持不变。

脚本假设由一个人工进程运行，不实现文件锁。只有实际出现并发写入需求时才升级为 SQLite 或显式锁。

## 10. 内置验证

项目不创建独立测试文件。`self-check` 使用内存 DataFrame 和临时目录，通过 `assert` 验证：

- 慢均线热身；
- 信号在下一开盘成交；
- 原始除息缺口不影响前复权信号；
- 字符串输入被统一规范化，NaN/Inf 被拒绝；
- 费用与滑点降低最终净值；
- 100 份交易单位与非负现金；
- 停牌/一字涨停阻断；
- 纸面模式同日幂等；
- 配置变化被拒绝；
- 漏跑期间不伪造历史成交；
- 刷新历史缺少账本最后日期时拒绝推进；
- 固定 3×3 稳健性报告采用共同基线、记录配置，并在数值层面隔离 holdout。
- 短样本不能通过 holdout 资格，零回撤 Calmar 输出 `null`，压力滑点不低于基础滑点。

退出码为 0 表示内置验证通过，非 0 会在标准错误输出具体原因。

## 11. 人工验收

按顺序执行，任何一步失败都不要进入下一步：

1. 安装 64 位 Python 3.13，创建 `.venv` 并按第 3 节安装依赖；
2. 运行 `self-check`，确认输出 `self-check: OK` 且退出码为 0；
3. 运行 `fetch --symbol 510300 --start 2013-01-01`，确认 `current.json`、其指向的快照、行数、截止日和 SHA-256 一致；
4. 运行 `robustness` 与 `backtest`，确认生成带配置/数据指纹的目录，且 `summary.json` 同时包含策略、买入持有、压力场景和 holdout；
5. 在同一份完整日线上连续运行两次 `paper`，第二次必须返回 `no-op`，账本行数与文件哈希不变；
6. 记录账本 `Get-FileHash`，临时断开网络后运行 `paper`，命令必须退出失败；恢复网络后确认账本哈希未变；
7. 不刷新缓存，连续运行两次同参数 `backtest`，分别记录三个输出文件的 `Get-FileHash`，两次必须完全一致。

第 6 步是受控故障演练，只在本机方便恢复网络时执行。失败时按下一节的具体排查路径处理，不删除旧缓存或账本。

## 12. 故障排查思路

### `fetch` 失败

1. 确认 Python 与依赖版本：`python --version`、`python -c "import akshare; print(akshare.__version__)"`；
2. 确认能访问 AKShare 对应的东方财富和新浪上游；
3. 查看错误是否为字段缺失、空表或日期不一致；
4. 不删除旧缓存，等待上游恢复或核对 AKShare 变更记录；
5. 不临时混入另一数据源补洞，因为复权与字段口径可能不同。

### `backtest` 拒绝缓存

1. 检查 `current.json` 指向的快照是否存在；
2. 核对指针中的 SHA-256 与快照是否匹配；
3. 检查日期、OHLC、成交量和分红列；
4. 重新运行 `fetch` 生成完整快照，不手工拼接尾部。

### `paper` 没有新增记录

1. 查看返回状态是否为 `no-op`；
2. 比较缓存最新日期与账本 `as_of_date`；
3. 周末、节假日或上游尚未更新时，没有新日线属于正常情况；
4. 当天 15:30 前出现的日线不会被视为完成数据；
5. 最新数据超过 10 个自然日时会触发灾难性陈旧保护；这不能替代正式交易日历校验；
6. 数据异常时程序应停止，而不是继续生成订单。

### 账本配置不匹配

费率、均线或初始资金已经改变。保留旧账本作为审计记录，用新的文件名或清晰归档后开始新一轮纸面观察，不修改旧行。

## 13. 方案迭代记录

1. 初稿考虑全市场股票动量；因需要历史股票池、退市、ST、公司行动和板块规则，改为单一宽基 ETF。
2. 初稿考虑 Tushare；因第一版要求零密钥，改为 AKShare，并接受无 SLA、只适合个人学习的边界。
3. 初稿考虑 JSON 状态加 CSV 流水；为避免双写失败，合并为一个可人工审计的 CSV。
4. 初稿考虑提交合成 CSV；最终由 `self-check` 在内存生成，减少文件并避免误解为真实行情。
5. 没有增加通用数据源、券商或策略抽象；只有出现第二个真实实现时才提取接口。
6. 第二轮评审修复了分红空响应与日期错位、holdout 边界、非有限数值、字符串日期、空交易表结构和压力场景缺项；加入共同基线的训练段稳健性报告，并禁止漏跑后的历史补成交。
7. 输出目录加入配置与行情快照指纹，纸面推进要求账本日期仍在刷新历史中，自动门槛也改名为 `holdout_gates_passed`，避免把机械检查包装成人工晋级结论。
8. 经多视角评审，漏跑语义正式改为 `MISSED_WINDOW`：只兑现仍处于下一根新日线窗口内的既有目标，超过该窗口不事后声称成交。v1 同时封闭为仅支持 510300。

## 14. 已知限制与升级条件

- 涨跌停判断是日线近似，不能代表真实排队成交；
- 免费数据无 SLA，分红或历史复权仍需人工抽查；
- 分红现金在除息日入账是因免费接口缺少支付日而采用的教学近似；
- 10 天陈旧阈值只是灾难熔断，不能证明数据等于最近已完成交易日；
- 未建模基金拆分、申购赎回、IOPV、折溢价套利和市场冲击；
- 现金收益率固定为 0；
- 纸面模式记录理论开盘成交，不代替真实模拟券商账户；
- 尚未安装 Python 时只能静态审查，必须安装后完成 `self-check`、抓取、回测和纸面幂等验收。

扩展到个股前必须新增时点化股票池、退市、ST、复权、公司行动、涨跌停和停牌数据。连接券商前必须增加对账、权限隔离、紧急停机，并重新核实现行程序化交易规定：[证监会规定](https://www.csrc.gov.cn/csrc/c100028/c7480577/content.shtml)。
