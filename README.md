# olenara-paper-trader

面向 `olenara.com` 收盘复盘与盘前早报的 A 股**模拟交易**仓。默认初始资金 ¥100,000；免费行情优先；保守近似撮合；不连接券商、不发送真实订单。

> 重要：GitHub Actions 不是实时行情终端。免费公开数据源可能延迟、缺字段或限流；常规分钟 K 线不能证明 09:25 集合竞价成交。凡缺少竞价/分钟数据的交易，系统会标记 `DATA_MISSING` / `ESTIMATED`，不把估算说成真实成交。2026-10-12 尚未发生时，不能预先声称已买入或有收益。

## 当前实现

- 先按交易日历找目标日的上一交易日，抓取该日 close recap；若抓取失败或未解析到有效候选，再抓取目标交易日当天 morning brief。
- 保存两篇原文快照、SHA256、抓取状态，以及 `source_decision.json`（最终选用来源、URL、原因、解析结果、候选代码）。
- 若两篇均无有效策略，输出 `NO_VALID_STRATEGY` 并严格不交易。2026-10-12 五只候选仅作为用户提供的初始化表，默认不会自动启用；只有显式传入 `--allow-user-seed` 才能作为手动备用。
- 可编辑 `data/market/auction.csv`、`data/market/minute_bars.csv`，用于人工导入/复核免费行情。
- 规则引擎：竞价阈值、09:35–09:50 买点窗口、放量/不破均价线近似确认、最多一只、单次仓位不超过 20%、10cm 止损 -3%、20cm 止损 -5%。
- CSV 账本、每日 Markdown 报告、持仓快照；所有估算成交均标 `ESTIMATED`。
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

1. 解压本仓库 ZIP。
2. GitHub → New repository → 建立 `olenara-paper-trader`（建议 Private）。
3. 将解压后的所有文件上传并 commit 到默认分支 `main`。
4. 打开 **Settings → Actions → General**，确认允许 Actions。
5. 打开 **Actions**，手动运行 `Olenara paper trader`，输入 `strategy_date=2026-10-12`。
6. 查看 workflow run 的 **Summary**，下载 `paper-trader-report` artifact；每日报告也会作为 artifact 保存。
7. 若希望自动更新仓库里的账本，需额外授予 `contents: write` 并提交生成文件；本版默认不自动 commit 数据，避免竞态/覆盖。Actions artifact 是首选报告查看方式。

## 定时任务

`.github/workflows/daily.yml` 配置为周一至周五 UTC 01:52 运行（北京时间 09:52），用于抓取当日竞价及分钟行情，并回看 09:35–09:50 入场窗口，输出模拟信号。GitHub 定时 workflow 可能延迟；应查看运行时间和行情时间戳，延迟或缺少数据时不把信号当作实时提示。

## 局限与安全

- 这是研究工具，不是投资建议，也不执行真实交易。
- 免费数据源可能变更、限流、复权口径不同或中断。
- 无逐笔/盘口数据时，无法保证排队成交；触及止损但跌停无法卖出时，简单止损模型会低估风险。
- 不把缺失数据补成 0；缺数据就标记，不交易。

