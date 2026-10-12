# olenara-paper-trader

面向 `olenara.com` 收盘复盘与盘前早报的 A 股**模拟交易**仓。默认初始资金 ¥100,000；免费行情优先；保守近似撮合；不连接券商、不发送真实订单。

> 重要：GitHub Actions 不是实时行情终端。免费公开数据源可能延迟、缺字段或限流；常规分钟 K 线不能证明 09:25 集合竞价成交。凡缺少竞价/分钟数据的交易，系统会标记数据不完整并停止推断，不把估算说成真实成交。所有成交均为模拟估算，报告会标记数据来源和完整性。

## 当前实现

- 先按交易日历找目标日的上一交易日，抓取该日 close recap；若抓取失败或未解析到有效候选，再抓取目标交易日当天 morning brief。
- 保存两篇原文快照、SHA256、抓取状态，以及 `source_decision.json`（最终选用来源、URL、原因、解析结果、候选代码）。
- 若两篇均无有效策略，输出 `NO_VALID_STRATEGY` 并严格不交易。2026-10-12 五只候选仅作为用户提供的初始化表，默认不会自动启用；只有显式传入 `--allow-user-seed` 才能作为手动备用。
- 可编辑 `data/market/auction.csv`、`data/market/minute_bars.csv`，用于人工导入/复核免费行情。
- 规则引擎：竞价阈值、09:35–09:50 买点窗口、放量/不破均价线近似确认、最多一只、单次仓位不超过 20%、10cm 止损 -3%、20cm 止损 -5%。
- 持久模拟账户：10万元初始资金、单票最多20%仓位、100股整数手；逐笔账本、费用、持仓快照和每日净值都提交回仓库。所有估算成交明确标记来源。
- GitHub Actions 可定时运行，也可手动指定策略日期。

## 快速开始（本地）

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m src.main --strategy-date 2026-10-12 --dry-run
# 如需显式用用户提供的 10/12 候选表做初始化测试（不是网页自动解析）：
python -m src.main --strategy-date 2026-10-12 --dry-run --allow-user-seed
# 盘中会话：在目标交易日 09:25 前启动，等待竞价并监控入场窗口
python -m src.session --strategy-date 2026-10-12
```

没有当日真实行情文件时，程序只输出信号与数据缺失，不虚构买入。

## 策略来源选择逻辑

对目标交易日 `T`，程序按以下顺序执行：

1. 用 AkShare 的新浪交易日历接口尝试确定 `T` 的上一交易日 `P`。
2. 先抓取 `https://olenara.com/{P}-close.html`。
3. 若复盘无法抓取，或没有解析出本项目支持的有效候选，再抓取 `https://olenara.com/morning-{T}.html`。
4. 若早报也没有有效候选，保存 `NO_VALID_STRATEGY`，不交易。
5. 将两个来源的状态、URL、SHA256、解析原因和最终选择写入 `data/signals/T/source_decision.json`，同时保存 HTML/TXT 快照。

交易日历接口不可用时会使用工作日回退并标记 `WEEKDAY_FALLBACK_NOT_HOLIDAY_AWARE`。这不识别中国法定休市日，正式运行时应核验日志中的日历来源；建议网络和 AkShare 正常可用。

策略有效性检查是保守的启发式：正文须含有行动类关键词，并出现项目已审核规则表中的候选代码。未知股票不会被自动加入。网页结构变化时宁可不交易，也不会把预置名单当成网页解析结果。

## 数据文件格式

### `data/market/auction.csv`
列：`date,code,auction_price,prev_close,auction_pct,source,observed_at`
- `auction_pct` 是相对前收的百分比数值，例如 `2.4` 表示 +2.4%。
- 只能在竞价结束后用实际可获得的数据填写；不要从开盘价倒推并冒充竞价。

### `data/market/minute_bars.csv`
列：`date,code,time,open,high,low,close,volume,amount,avg_price`
- `time` 格式 `09:35`；用于近似执行 09:35–09:50 确认。
- `avg_price` 优先使用数据源的当日均价/VWAP；缺失时可由累计成交额/成交量近似，但单位不一致时不要混用。

免费数据源：
- [Ashare](https://github.com/mpquant/Ashare)：公开源封装，包含分钟数据；可用性、历史覆盖和实际时效需要逐日验证。
- [AkShare](https://github.com/akfamily/akshare)：免费开源数据接口；不同接口的数据口径与限流情况会变化。
- 本项目不假定任何免费源能稳定提供历史 09:25 集合竞价撮合数据。请优先人工导入可靠竞价快照，来源写入 `source` 字段。

## 策略口径（10/12 复盘原文）

- 只做表 A 候选，不主动买表 B；表 C 排除。
- 竞价门槛：绿盟科技 +2%；天融信 +2%；中文在线 +3%；吉视传媒 +2%；振华股份 +1.5%。
- 原文同时写有竞价强度排序（≥+4%优先、≥+2%次之），并要求 09:35–09:50「放量不破均价」确认；满足条件中只买一只，最多 20% 仓位。
- 竞价≤0% 对绿盟、中文在线明确放弃；天融信平开观察。其他候选依各自门槛执行。
- 10cm 标的止损 -3%；20cm 标的止损 -5%；跌破均价线且 5 分钟不收回则退出。
- 原文的「若时代万恒/九阳双双一字封死，可加大表 A 仓位」和「若其中一只竞价低开破位则表 A 全部降级为空仓」需要观察池的可靠竞价数据。当前版本**不自动放大超过 20%**，且缺数据时不猜。

若文章没有明确止盈/持有期限，报告会标记未定义；本版不自行编造止盈规则。任何手动补充规则请记录在 `data/signals/`，不要覆盖原文快照。

## 部署到 GitHub

1. 确认仓库中已包含 `config/paper_account.json`、`data/paper_account/` 和 `docs/paper-account-operations.md`。
2. 在 **Settings → Actions → General** 中允许 Actions 工作流运行，并确认工作流拥有 `contents: write` 权限；定时任务会将账本、行情快照和报告提交回当前分支。
3. 在 **Actions** 中可手动运行盘前流程预览或盘后恢复流程。手动运行默认不改写正式账本；只有核对指定日期及输出正确后，盘后恢复才显式选择 `commit_state=true`。
4. 自动运行后，检查仓库提交记录以及 `data/paper_account/trades.csv`、`positions.csv`、`daily_nav.csv`。Artifacts 仅是运行诊断副本，正式跨日状态以仓库账本为准。
5. 按 [`docs/paper-account-operations.md`](docs/paper-account-operations.md) 核对盘前、盘后报告与数据完整性。首次运行会从已提交的 2026-10-12 `backfill` 记录开始；这是回放重建，不代表当时真实记账成交。

## 定时任务

`.github/workflows/daily.yml` 配置为周一至周五 UTC 01:20（北京时间 09:20）启动盘中会话；`.github/workflows/daily-close.yml` 配置为 UTC 07:05（北京时间 15:05）盘后结算。盘后作业读取持仓的完整分钟线，评估 T+1 允许的退出条件并写每日净值。完整操作顺序、核查清单和故障恢复方式见 [`docs/paper-account-operations.md`](docs/paper-account-operations.md)。本地盘中运行可在目标交易日 09:25 前执行 `python -m src.session --strategy-date YYYY-MM-DD`，本地盘后核对执行 `python -m src.close_session --strategy-date YYYY-MM-DD`。

GitHub 定时 workflow 可能延迟；如果任务启动时已错过盘前阶段，报告中的竞价/分钟数据只能用于事后模拟，不能视作实时信号。没有可靠竞价记录或分钟均价时，系统标记数据不足并跳过交易。所有模拟成交基于分钟线估算，不代表真实排队成交或券商订单。

## 局限与安全

- 这是研究工具，不是投资建议，也不执行真实交易。
- 免费数据源可能变更、限流、复权口径不同或中断。
- 无逐笔/盘口数据时，无法保证排队成交；触及止损但跌停无法卖出时，简单止损模型会低估风险。
- 不把缺失数据补成 0；缺数据就标记，不交易。

