"""Long/cash simulation with next-open fills and explicit per-side costs."""
from dataclasses import dataclass
from statistics import median
from .model import period_close, period_id


@dataclass(frozen=True)
class Signal:
    confirmed_at: int
    ready: bool
    regime: int
    bull_event: bool
    bear_event: bool


def benchmarks(bars, period="weekly", warmup=52, ema_length=40):
    ema = atr = prev_close = upper = lower = line = None
    tr_sum, count, ema_state, st_state = 0.0, 0, 0, 0
    ema_signals, st_signals = [], []
    prev_time = None
    for b in bars:
        old_ema_state, old_st_state = ema_state, st_state
        ready = False
        if b.valid:
            count += 1
            ema = b.close if ema is None else ema + 2 / (ema_length + 1) * (b.close - ema)
            tr = b.high - b.low if prev_close is None else max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
            old_atr = atr
            if count <= 14:
                tr_sum += tr
                if count == 14:
                    atr = tr_sum / 14
            else:
                atr += (tr - atr) / 14
            if atr is not None:
                basic_upper, basic_lower = (b.high + b.low) / 2 + 3 * atr, (b.high + b.low) / 2 - 3 * atr
                prev_upper, prev_lower, prev_line = upper, lower, line
                upper = basic_upper if upper is None or basic_upper < upper or prev_close > upper else upper
                lower = basic_lower if lower is None or basic_lower > lower or prev_close < lower else lower
                if old_atr is None:
                    st_state = -1
                elif prev_line == prev_upper:
                    st_state = 1 if b.close > upper else -1
                else:
                    st_state = -1 if b.close < lower else 1
                line = lower if st_state == 1 else upper
            ema_state = 1 if b.close > ema else -1 if b.close < ema else ema_state
            ready = (count >= warmup and prev_time is not None
                     and period_id(b.time, period) - period_id(prev_time, period) == 1)
            prev_close, prev_time = b.close, b.time
        close_at = period_close(b.time, period)
        ema_signals.append(Signal(close_at, ready, ema_state,
                                  ready and ema_state == 1 and old_ema_state != 1,
                                  ready and ema_state == -1 and old_ema_state != -1))
        st_signals.append(Signal(close_at, ready and atr is not None, st_state,
                                 ready and st_state == 1 and old_st_state != 1,
                                 ready and st_state == -1 and old_st_state != -1))
    return ema_signals, st_signals


def simulate(bars, signals, start, end, fee=0.003, slippage=0.005, buy_hold=False,
             period="weekly", allocation=0.99, enter_existing=True):
    if len(bars) != len(signals):
        raise ValueError("Each bar must have a signal snapshot")
    if start >= end:
        raise ValueError("Evaluation end must follow its start")
    if any(b.time <= a.time for a,b in zip(bars,bars[1:])):
        raise ValueError("Backtest bars must be ordered and unique")
    if any(s.confirmed_at != period_close(b.time,period) for b,s in zip(bars,signals)):
        raise ValueError("Signal timestamps must match their source candle close")
    if not (0 <= fee < 1 and 0 <= slippage < 1 and 0 < allocation <= 1 / (1 + fee)):
        raise ValueError("Costs or allocation are invalid")
    cash, qty, peak = 10000.0, 0.0, 10000.0
    active = None
    trades, curve = [], []
    held_bars = total_bars = 0
    held_seconds = total_seconds = 0
    first_eligible = True
    pending_initial = enter_existing
    previous_evaluated = None
    for i, b in enumerate(bars):
        if b.time < start or period_close(b.time, period) > end:
            continue
        if not b.valid:
            raise ValueError("Incomplete OHLC inside the evaluation window; do not publish performance")
        if previous_evaluated is not None and period_close(previous_evaluated.time,period) != b.time:
            raise ValueError("Missing calendar candle inside the evaluation window; do not publish performance")
        previous_evaluated = b
        previous = signals[i-1] if i > 0 else None
        usable = previous is not None and previous.ready and previous.confirmed_at == b.time
        exit_now = not buy_hold and usable and previous.bear_event and qty > 0
        if exit_now:
            exit_price = b.open * (1 - slippage)
            proceeds = qty * exit_price * (1 - fee)
            cash += proceeds
            active.update({"exit_time": b.time, "exit_signal_time": previous.confirmed_at,
                           "exit_price": exit_price, "net_return_pct": 100 * (proceeds / active["entry_cost"] - 1),
                           "holding_days": (b.time - active["entry_time"]) / 86400,
                           "status": "closed"})
            trades.append(active)
            active, qty = None, 0.0
        initial_entry = pending_initial and usable and previous.regime == 1
        enter_now = qty == 0 and ((buy_hold and first_eligible) or (not buy_hold and usable and (previous.bull_event or initial_entry)))
        if enter_now:
            fill = b.open * (1 + slippage)
            notional = cash * allocation
            cost = notional * (1 + fee)
            qty = notional / fill
            cash -= cost
            active = {"entry_time": b.time, "entry_signal_time": previous.confirmed_at if previous else None,
                      "entry_price": fill, "quantity": qty, "entry_cost": cost,
                      "entry_reason": "buy_hold" if buy_hold else "existing_bull_at_start" if initial_entry and not previous.bull_event else "bullish_confirmation"}
        # Initial-regime treatment applies to the first available decision, never to later missed flips.
        if usable:
            pending_initial = False
        first_eligible = False
        total_bars += 1
        held_bars += qty > 0
        duration = period_close(b.time, period) - b.time
        total_seconds += duration
        held_seconds += duration if qty > 0 else 0
        equity = cash + qty * b.close
        peak = max(peak, equity)
        curve.append({"time": period_close(b.time, period), "equity": equity,
                      "drawdown_pct": 100 * (equity / peak - 1), "invested": int(qty > 0),
                      "cash": cash, "quantity": qty, "close": b.close})
    if not curve:
        raise ValueError("No complete periods in evaluation window")
    if active:
        active.update({"exit_time": None, "exit_signal_time": None, "exit_price": None,
                       "net_return_pct": None, "holding_days": (curve[-1]["time"] - active["entry_time"]) / 86400,
                       "status": "open"})
        trades.append(active)
    closed = [t for t in trades if t["status"] == "closed"]
    metrics = {"return_pct": 100 * (curve[-1]["equity"] / 10000 - 1),
               "max_drawdown_pct": min(r["drawdown_pct"] for r in curve),
               "exposure_pct": 100 * held_seconds / total_seconds,
               "closed_trades": len(closed), "open_trades": sum(t["status"] == "open" for t in trades),
               "median_holding_days": median(t["holding_days"] for t in closed) if closed else None,
               "losing_trades_within_8_weeks": sum(t["net_return_pct"] < 0 and t["holding_days"] <= 56 for t in closed),
               "bars": total_bars, "ending_equity": curve[-1]["equity"]}
    return metrics, trades, curve
