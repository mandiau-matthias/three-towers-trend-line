import math
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from research.model import Bar, Engine, MONTHLY, Parameters, WEEK, calculate, daily_publication, period_close
from research.data import monthly_from_daily, weekly_from_daily
from research.backtest import Signal, simulate

START = int(datetime(2010, 1, 4, tzinfo=timezone.utc).timestamp())


def bars(prices):
    return [Bar(START+i*WEEK, c, c*1.01, c*.99, c, 100) for i, c in enumerate(prices)]


class ModelTests(unittest.TestCase):
    def test_flat_prices_no_direction_or_division_error(self):
        s = calculate(bars([100.0]*130))
        self.assertTrue(s[-1].ready)
        self.assertEqual(s[-1].line, 100)
        self.assertTrue(all(x.regime == 0 and not x.event for x in s))

    def test_sustained_trends_confirm_once_after_warmup(self):
        for sign, prices in [(1, [100*1.04**i for i in range(140)]), (-1, [100*.97**i for i in range(140)])]:
            s = calculate(bars(prices))
            self.assertTrue(all(x.regime == 0 for x in s[:52]))
            self.assertEqual(s[52].regime, sign)
            self.assertEqual(sum(x.bull_event or x.bear_event for x in s), 1)
            self.assertEqual(s[-1].regime, sign)

    def test_kama_seed_and_first_update_hand_calculated(self):
        s = calculate(bars(list(range(1, 20))))
        self.assertEqual(s[9].line, 5.5)
        self.assertAlmostEqual(s[10].line, 5.5 + (2/3)**2*(11-5.5))

    def test_wilder_atr_seed_and_smoothing(self):
        b = [Bar(START+i*WEEK, 100, 101, 99, 100) for i in range(14)]
        b.append(Bar(START+14*WEEK, 100, 103, 97, 100))
        s = calculate(b)
        self.assertIsNone(s[12].atr)
        self.assertEqual(s[13].atr, 2)
        self.assertAlmostEqual(s[14].atr, 2 + 4/14)

    def test_append_invariance_and_reload(self):
        p = [100*math.exp(.01*i + .15*math.sin(i/7)) for i in range(220)]
        b = bars(p)
        full = calculate(b)
        for n in [10, 52, 73, 100, 180]:
            self.assertEqual(calculate(b[:n]), full[:n])
        self.assertEqual(calculate(b), full)

    def test_price_scale_invariance(self):
        p = [100*math.exp(.015*i + .3*math.sin(i/9)) for i in range(220)]
        a, b = calculate(bars(p)), calculate(bars([x/1e8 for x in p]))
        self.assertEqual([(x.regime, x.event) for x in a], [(x.regime, x.event) for x in b])
        self.assertAlmostEqual(a[-1].line/1e8, b[-1].line)

    def test_sharp_reversal_warning_not_repeated(self):
        p = [100*1.03**i for i in range(100)]
        p += [p[-1]*.91**(i+1) for i in range(30)]
        s = calculate(bars(p))
        self.assertEqual(s[-1].regime, -1)
        warnings = [i for i,x in enumerate(s) if x.warn_event]
        bear = next(i for i,x in enumerate(s[100:],100) if x.bear_event)
        self.assertTrue(warnings)
        self.assertLess(warnings[-1], bear)
        for old,new in zip(s,s[1:]):
            if old.weakening and new.weakening:
                self.assertFalse(new.warn_event)

    def test_small_pullback_retains_bull(self):
        p = [100*1.04**i for i in range(100)]
        p += [p[-1]*.99, p[-1]*1.01, p[-1]*1.04]
        self.assertTrue(all(s.regime == 1 for s in calculate(bars(p))[99:]))

    def test_sideways_market_has_no_chattering_flips(self):
        s = calculate(bars([100 + (1 if i%2 else -1) for i in range(150)]))
        self.assertLessEqual(sum(bool(x.bull_event or x.bear_event) for x in s), 2)

    def test_missing_period_breaks_confirmation_and_recovers(self):
        b = bars([100*1.04**i for i in range(100)])
        s = calculate(b[:52] + b[53:])
        self.assertFalse(s[52].ready)
        self.assertEqual(s[52].regime, 0)
        self.assertFalse(s[52].event)
        self.assertTrue(s[-1].ready)
        self.assertEqual(s[-1].regime, 1)

    def test_invalid_ohlc_does_not_emit(self):
        b = bars([100*1.03**i for i in range(100)])
        b[80] = replace(b[80], low=-1)
        s=calculate(b)
        self.assertFalse(s[80].ready)
        self.assertFalse(s[80].event)
        self.assertEqual(s[80].valid_bars,s[79].valid_bars)

    def test_daily_view_never_sees_future_week(self):
        s=calculate(bars([100*1.04**i for i in range(80)]))
        days=range(START,START+80*WEEK,86400)
        view=daily_publication(s,days)
        self.assertTrue(all(r['snapshot'] is None or r['snapshot'].confirmed_at <= r['time'] for r in view))
        events=[r for r in view if r['event']]
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['time'],s[52].confirmed_at)

    def test_partial_or_missing_days_do_not_make_valid_week(self):
        d=[Bar(START+i*86400,100,101,99,100) for i in range(10)]
        self.assertEqual(len(weekly_from_daily(d,START+10*86400)),1)
        self.assertFalse(weekly_from_daily(d[:3]+d[4:],START+10*86400)[0].valid)

    def test_calendar_months_include_leap_year_without_fixed_day_assumption(self):
        self.assertEqual(period_close(int(datetime(2024,2,1,tzinfo=timezone.utc).timestamp()),'monthly'),int(datetime(2024,3,1,tzinfo=timezone.utc).timestamp()))
        monthly=[]
        for i in range(72):
            t=int(datetime(2018+i//12,i%12+1,1,tzinfo=timezone.utc).timestamp())
            c=100*1.09**i
            monthly.append(Bar(t,c,c*1.01,c*.99,c))
        s=calculate(monthly,MONTHLY,'monthly')
        self.assertEqual(s[24].regime,1)
        self.assertEqual(calculate(monthly[:40],MONTHLY,'monthly'),s[:40])
        self.assertEqual(sum(x.bull_event for x in s),1)

    def test_duplicate_or_out_of_order_bars_rejected(self):
        b=bars([100,101]);self.assertRaises(ValueError,calculate,[b[0],b[0]])

    def test_trade_fills_use_next_open_and_costs(self):
        b=[Bar(START,100,101,99,100),Bar(START+WEEK,120,125,115,120),Bar(START+2*WEEK,90,95,85,90)]
        sig=[Signal(START+WEEK,True,1,True,False),Signal(START+2*WEEK,True,-1,False,True),Signal(START+3*WEEK,True,-1,False,False)]
        m,t,c=simulate(b,sig,START,START+3*WEEK,fee=.003,slippage=.005,enter_existing=False)
        self.assertEqual(t[0]['entry_time'],b[1].time)
        self.assertAlmostEqual(t[0]['entry_price'],120*1.005)
        self.assertAlmostEqual(t[0]['exit_price'],90*.995)
        self.assertEqual(m['losing_trades_within_8_weeks'],1)
        self.assertEqual(c[0]['quantity'],0)


if __name__ == '__main__':
    unittest.main()
