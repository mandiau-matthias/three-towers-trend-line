"""Synthetic fixtures test the comparison tool. They are not TradingView evidence."""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.model import CURRENT, WEEK, calculate
from scripts.check_tradingview_export import check
from test_model import START, bars


class ExportCheckerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "weekly.csv"
        history = bars([100 * 1.03**i for i in range(100)])
        self.cutoff = START + len(history) * WEEK
        self.fields = ["time", "open", "high", "low", "close", "TT Regime", "TT Weakening",
                       "Trend Line", "TT EMA", "TT ATR", "TT Confirmed UTC seconds", "TT Event",
                       "TT First source UTC seconds", "TT Valid bars", "TT Ready", "TT Model",
                       "TT Defaults", "TT SELL rule (1 = below the sell line, 0 = 1.4 classic)"]
        self.rows = []
        for bar, s in zip(history, calculate(history, CURRENT)):
            self.rows.append([bar.time, bar.open, bar.high, bar.low, bar.close,
                              s.regime if s.ready else None, int(s.weakening) if s.ready else None,
                              s.line, s.ema, s.atr, s.confirmed_at, s.event, s.first_source_time,
                              s.valid_bars, int(s.ready), 1, 1, 1])

    def write(self):
        with self.path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(self.fields)
            writer.writerows(self.rows)

    def test_prefixed_columns_match_and_unclosed_week_is_excluded(self):
        self.fields[5:] = ["Trend Line: " + name for name in self.fields[5:]]
        self.rows[-1][11] = -1
        self.write()
        result = check(self.path, self.cutoff - 1)
        self.assertEqual((result["status"], result["bars"]), ("passed", 99))
        self.assertEqual(result["last_confirmation"], self.cutoff - WEEK)

    def test_wrong_event_and_confirmation_are_reported(self):
        self.rows[52][11] = -1
        self.rows[52][10] += 1
        self.write()
        result = check(self.path, self.cutoff)
        self.assertEqual(result["status"], "failed")
        self.assertEqual({m["field"] for m in result["first_mismatches"]}, {"event", "confirmed_at"})

    def test_settings_and_history_start_must_match(self):
        self.rows[-1][-2] = 0
        self.rows[-1][-1] = 0
        self.rows.pop(0)
        self.write()
        result = check(self.path, self.cutoff)
        self.assertEqual(result["status"], "failed")
        self.assertIn("first_source_time", [m["field"] for m in result["first_mismatches"]])
        self.assertGreater(result["mismatch_count"], 10)

    def test_duplicate_plots_and_warmup_only_are_rejected(self):
        self.write()
        with self.assertRaisesRegex(ValueError, "warm-up"):
            check(self.path, START + 3 * WEEK)
        self.fields.append("Another script: TT Event")
        for row in self.rows:
            row.append(0)
        self.write()
        with self.assertRaisesRegex(ValueError, "Export one script"):
            check(self.path, self.cutoff)
