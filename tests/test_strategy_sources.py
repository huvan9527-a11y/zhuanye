import unittest
from datetime import date
from src.main import select_strategy, parse_candidates, previous_trading_day


def meta(url, available=True):
    return {"url": url, "available": available, "sha256": "testhash" if available else None}


class StrategySourceTests(unittest.TestCase):
    def test_close_recap_wins_when_valid(self):
        close = "交易指引 300369 竞价"
        morning = "交易指引 002212 竞价"
        result = select_strategy(date(2026, 10, 13), date(2026, 10, 12), close,
                                 meta("close"), morning, meta("morning"))
        self.assertEqual(result["selected_source"], "close_recap")
        self.assertEqual([x["code"] for x in result["selected_candidates"]], ["300369"])

    def test_morning_fallback_when_close_unavailable(self):
        morning = "交易指引 002212 竞价"
        result = select_strategy(date(2026, 10, 13), date(2026, 10, 12), "",
                                 meta("close", False), morning, meta("morning"))
        self.assertEqual(result["selected_source"], "morning_brief")
        self.assertEqual([x["code"] for x in result["selected_candidates"]], ["002212"])

    def test_morning_fallback_when_close_has_no_actionable_guidance(self):
        close = "市场今日整体震荡，暂无明确方向"
        morning = "交易指引 603067 竞价"
        result = select_strategy(date(2026, 10, 13), date(2026, 10, 12), close,
                                 meta("close"), morning, meta("morning"))
        self.assertEqual(result["selected_source"], "morning_brief")

    def test_no_strategy_means_no_trade(self):
        result = select_strategy(date(2026, 10, 13), date(2026, 10, 12), "无有效策略",
                                 meta("close"), "暂无候选", meta("morning"))
        self.assertEqual(result["status"], "NO_VALID_STRATEGY")
        self.assertEqual(result["selected_candidates"], [])

    def test_unknown_codes_not_accepted(self):
        self.assertEqual(parse_candidates("交易指引 999999 竞价")[0], [])

    def test_weekend_fallback(self):
        # Calendar fallback only tested as a function contract; live AkShare may provide the actual calendar.
        self.assertLess(previous_trading_day(date(2026, 10, 26))[0], date(2026, 10, 26))


if __name__ == "__main__":
    unittest.main()

