"""Behaviour of the 1.5.0 SELL rule (two weekly closes below the adaptive line's lower band).

The frozen 1.0.0 reference keeps `Parameters()`; the launch model is `CURRENT`.
"""
import math
import unittest
from datetime import datetime, timezone
from research.model import Bar, CURRENT, CURRENT_VERSION, MODEL_VERSIONS, Parameters, REFERENCE, WEEK, calculate

START = int(datetime(2010, 1, 4, tzinfo=timezone.utc).timestamp())


def bars(prices):
    return [Bar(START + i * WEEK, c, c * 1.01, c * .99, c, 100) for i, c in enumerate(prices)]


class BandExitTests(unittest.TestCase):
    def test_versions_are_explicit(self):
        self.assertEqual(MODEL_VERSIONS[CURRENT_VERSION], CURRENT)
        self.assertEqual(MODEL_VERSIONS["weekly-1.0.0"], REFERENCE)
        self.assertEqual(REFERENCE, Parameters())
        self.assertEqual(CURRENT, Parameters(exit_rule="band"))
        self.assertRaises(ValueError, Parameters, exit_rule="fast")

    def test_entries_identical_to_reference_on_sustained_trends(self):
        for prices in ([100 * 1.04 ** i for i in range(140)], [100 * .97 ** i for i in range(140)]):
            a, b = calculate(bars(prices)), calculate(bars(prices), CURRENT)
            self.assertEqual([x.bull_event for x in a], [x.bull_event for x in b])
            self.assertEqual([x.regime for x in a], [x.regime for x in b])
            self.assertEqual([(x.line, x.ema, x.atr) for x in a], [(x.line, x.ema, x.atr) for x in b])

    def test_sells_no_later_than_reference_after_a_top(self):
        p = [100 * 1.03 ** i for i in range(100)]
        p += [p[-1] * .93 ** (i + 1) for i in range(30)]
        ref = next(i for i, x in enumerate(calculate(bars(p))) if x.bear_event)
        cur = next(i for i, x in enumerate(calculate(bars(p), CURRENT)) if x.bear_event)
        self.assertLess(cur, ref)
        # The exit is the second consecutive weekly close below line - buffer * ATR.
        snaps = calculate(bars(p), CURRENT)
        first_below = next(i for i, x in enumerate(snaps) if x.ready and x.regime == 1 and x.weakening)
        self.assertEqual(cur, first_below + 1)

    def test_warning_is_the_first_exit_week_and_clears_if_price_recovers(self):
        p = [100 * 1.03 ** i for i in range(100)]
        p += [p[-1] * .80, p[-1] * 1.05, p[-1] * 1.08, p[-1] * 1.12]
        snaps = calculate(bars(p), CURRENT)
        self.assertTrue(snaps[100].weakening and snaps[100].warn_event and snaps[100].regime == 1)
        self.assertFalse(snaps[101].weakening)
        self.assertTrue(all(x.regime == 1 for x in snaps[99:]))
        self.assertFalse(any(x.bear_event for x in snaps))

    def test_small_pullback_inside_band_does_not_sell(self):
        p = [100 * 1.04 ** i for i in range(100)]
        p += [p[-1] * .99, p[-1] * .985, p[-1] * 1.01, p[-1] * 1.04]
        self.assertTrue(all(s.regime == 1 and not s.weakening for s in calculate(bars(p), CURRENT)[99:]))

    def test_append_invariance_and_scale_invariance(self):
        p = [100 * math.exp(.01 * i + .15 * math.sin(i / 7)) for i in range(220)]
        b = bars(p)
        full = calculate(b, CURRENT)
        for n in (10, 52, 73, 100, 180):
            self.assertEqual(calculate(b[:n], CURRENT), full[:n])
        tiny = calculate(bars([x / 1e8 for x in p]), CURRENT)
        self.assertEqual([(x.regime, x.event) for x in full], [(x.regime, x.event) for x in tiny])

    def test_missing_week_still_breaks_confirmation(self):
        b = bars([100 * 1.04 ** i for i in range(100)])
        s = calculate(b[:52] + b[53:], CURRENT)
        self.assertFalse(s[52].ready)
        self.assertEqual(s[52].regime, 0)
        self.assertTrue(s[-1].ready and s[-1].regime == 1)

    def test_sideways_market_has_no_chattering_flips(self):
        s = calculate(bars([100 + (1 if i % 2 else -1) for i in range(150)]), CURRENT)
        self.assertLessEqual(sum(bool(x.bull_event or x.bear_event) for x in s), 2)


if __name__ == "__main__":
    unittest.main()
