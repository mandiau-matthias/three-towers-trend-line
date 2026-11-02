"""Independent, causal reference implementation. All times are UTC seconds."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math

WEEK = 604800


def period_id(stamp, period="weekly"):
    if period == "weekly":
        return stamp // WEEK
    d = datetime.fromtimestamp(stamp, timezone.utc)
    return d.year * 12 + d.month


def period_close(stamp, period="weekly"):
    if period == "weekly":
        return stamp + WEEK
    d = datetime.fromtimestamp(stamp, timezone.utc)
    return int(datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc).timestamp())


@dataclass(frozen=True)
class Parameters:
    er_length: int = 10
    fast_length: int = 2
    slow_length: int = 30
    ema_length: int = 40
    atr_length: int = 14
    atr_buffer: float = 0.5
    kama_slope: int = 2
    ema_slope: int = 4
    confirmation: int = 2
    warmup: int = 52
    # "all": SELL needs all three bearish conditions for `confirmation` weeks (frozen 1.0.0 reference).
    # "band": SELL needs the close below the adaptive line minus the ATR buffer for
    #         `confirmation` consecutive weeks (1.5.0). Entry rules are identical.
    exit_rule: str = "all"

    def __post_init__(self):
        if not 0 < self.fast_length < self.slow_length:
            raise ValueError("Fast length must be positive and smaller than slow length")
        if min(self.er_length, self.ema_length, self.atr_length, self.kama_slope,
               self.ema_slope, self.confirmation) < 1 or self.warmup < 2 or self.atr_buffer < 0:
            raise ValueError("Invalid parameter")
        if self.exit_rule not in ("all", "band"):
            raise ValueError("exit_rule must be 'all' or 'band'")


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def valid(self):
        return (all(math.isfinite(v) for v in (self.open, self.high, self.low, self.close))
                and self.low > 0 and self.high >= max(self.open, self.close)
                and self.low <= min(self.open, self.close))


@dataclass(frozen=True)
class Snapshot:
    time: int
    confirmed_at: int
    line: float | None
    ema: float | None
    atr: float | None
    regime: int
    weakening: bool
    ready: bool
    last_flip_at: int | None
    valid_bars: int
    bull_votes: int
    bear_votes: int
    bull_event: bool
    bear_event: bool
    warn_event: bool
    flip_price: float | None
    regime_bars: int
    price: float
    first_source_time: int

    @property
    def event(self):
        return 1 if self.bull_event else -1 if self.bear_event else 2 if self.warn_event else 0

    def row(self):
        return {**asdict(self), "event": self.event}


MONTHLY = Parameters(er_length=6, ema_length=12, atr_length=6, kama_slope=1, ema_slope=2, warmup=24)

# Frozen reference (1.0.0) and the launch model (1.5.0). Only the SELL rule differs.
REFERENCE = Parameters()
CURRENT = Parameters(exit_rule="band")
MODEL_VERSIONS = {"weekly-1.0.0": REFERENCE, "weekly-1.5.0": CURRENT}
CURRENT_VERSION = "weekly-1.5.0"


class Engine:
    def __init__(self, parameters=Parameters(), period="weekly"):
        if period not in ("weekly", "monthly"):
            raise ValueError("Supported periods: weekly, monthly")
        self.period = period
        self.p = parameters
        self.prices, self.times, self.lines, self.averages = [], [], [], []
        self.kama = self.ema = self.atr = None
        self.tr_seed = 0.0
        self.valid_bars = self.bull_run = self.bear_run = self.regime = 0
        self.weakening = False
        self.last_flip = self.last_valid = self.last_seen = None
        self.first_seen = None
        self.flip_price, self.regime_bars = None, 0

    def step(self, bar: Bar):
        if self.last_seen is not None and bar.time <= self.last_seen:
            raise ValueError("Bars must be strictly chronological and unique")
        self.last_seen = bar.time
        if self.first_seen is None:
            self.first_seen = bar.time
        p = self.p
        ordinal = period_id(bar.time, self.period)
        closed_at = period_close(bar.time, self.period)
        contiguous = self.last_valid is not None and ordinal - self.last_valid == 1
        ready = bull_event = bear_event = warn_event = False
        bull_votes = bear_votes = 0
        if not bar.valid or not contiguous:
            self.bull_run = self.bear_run = 0
        if bar.valid:
            prev = self.prices[-1] if self.prices else None
            tr = bar.high - bar.low if prev is None else max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev))
            self.valid_bars += 1
            self.ema = bar.close if self.ema is None else self.ema + 2 / (p.ema_length + 1) * (bar.close - self.ema)
            if self.valid_bars <= p.atr_length:
                self.tr_seed += tr
                if self.valid_bars == p.atr_length:
                    self.atr = self.tr_seed / p.atr_length
            else:
                self.atr += (tr - self.atr) / p.atr_length
            self.prices.append(bar.close)
            self.times.append(ordinal)
            n = len(self.prices)
            er_available = n > p.er_length and ordinal - self.times[-p.er_length-1] == p.er_length
            if self.kama is None and n >= p.er_length:
                self.kama = sum(self.prices[-p.er_length:]) / p.er_length
            elif self.kama is not None and er_available:
                movement = sum(abs(self.prices[i] - self.prices[i-1]) for i in range(n-p.er_length, n))
                efficiency = abs(bar.close - self.prices[-p.er_length-1]) / movement if movement else 0.0
                slow = 2 / (p.slow_length + 1)
                factor = (efficiency * (2 / (p.fast_length + 1) - slow) + slow) ** 2
                self.kama += factor * (bar.close - self.kama)
            self.lines.append(self.kama)
            self.averages.append(self.ema)
            slopes_available = (n > max(p.kama_slope, p.ema_slope)
                                and ordinal - self.times[-p.kama_slope-1] == p.kama_slope
                                and ordinal - self.times[-p.ema_slope-1] == p.ema_slope)
            ready = (self.valid_bars >= max(p.warmup, p.ema_length, p.atr_length)
                     and er_available and slopes_available and contiguous
                     and self.lines[-p.kama_slope-1] is not None
                     and self.kama is not None and self.atr is not None)
            if ready:
                old_kama = self.lines[-p.kama_slope-1]
                old_ema = self.averages[-p.ema_slope-1]
                bull_votes = sum((bar.close > self.kama + p.atr_buffer * self.atr,
                                  self.kama > old_kama,
                                  bar.close > self.ema and self.ema > old_ema))
                bear_votes = sum((bar.close < self.kama - p.atr_buffer * self.atr,
                                  self.kama < old_kama,
                                  bar.close < self.ema and self.ema < old_ema))
                below_band = bar.close < self.kama - p.atr_buffer * self.atr
                exit_condition = below_band if p.exit_rule == "band" else bear_votes == 3
                self.bull_run = self.bull_run + 1 if bull_votes == 3 else 0
                self.bear_run = self.bear_run + 1 if exit_condition else 0
                old_regime = self.regime
                if self.bull_run >= p.confirmation:
                    self.regime = 1
                elif self.bear_run >= p.confirmation:
                    self.regime = -1
                bull_event = self.regime == 1 and old_regime != 1
                bear_event = self.regime == -1 and old_regime != -1
                if bull_event or bear_event:
                    self.last_flip = closed_at
                    self.flip_price = bar.close
                    self.regime_bars = 0
                if self.regime != 0:
                    self.regime_bars += 1
                # 1.0.0: two of three bearish conditions. 1.5.0: the first week of the
                # exit rule itself, so a warning that repeats next week becomes the SELL.
                warning = self.regime == 1 and (below_band if p.exit_rule == "band" else bear_votes >= 2)
                warn_event = warning and not self.weakening
                self.weakening = warning
            else:
                self.bull_run = self.bear_run = 0
            self.last_valid = ordinal
            keep = max(p.er_length + 1, p.kama_slope + 1, p.ema_slope + 1)
            self.prices, self.times = self.prices[-keep:], self.times[-keep:]
            self.lines, self.averages = self.lines[-keep:], self.averages[-keep:]
        return Snapshot(bar.time, closed_at, self.kama, self.ema, self.atr,
                        self.regime, self.weakening, ready, self.last_flip, self.valid_bars,
                        bull_votes, bear_votes, bull_event, bear_event, warn_event,
                        self.flip_price, self.regime_bars, bar.close, self.first_seen)


def calculate(bars, parameters=Parameters(), period="weekly"):
    engine = Engine(parameters, period)
    return [engine.step(bar) for bar in bars]


def confirmed_publication(snapshots, chart_times, period="weekly"):
    """Project confirmed snapshots onto aligned viewing bars without backdated events.

    A missing boundary bar may still display the current confirmed regime when
    viewing resumes, but cannot invent a new marker at a time absent in the view.
    Caller supplies UTC bar openings (e.g. 15m, 1h, 4h, 1D); no resampling of the
    model itself takes place here.
    """
    i, current = 0, None
    previous_stamp = None
    out = []
    for stamp in chart_times:
        fresh_event = 0
        while i < len(snapshots) and snapshots[i].confirmed_at <= stamp:
            current = snapshots[i]
            fresh_event = current.event
            i += 1
        fresh = current is not None and current.confirmed_at <= stamp < period_close(current.confirmed_at, period)
        if previous_stamp is not None and stamp <= previous_stamp:
            raise ValueError("Publication timestamps must be ordered and unique")
        boundary_aligned = current is not None and stamp == current.confirmed_at
        out.append({"time": stamp, "snapshot": current, "available": bool(fresh and current.ready),
                    "event": fresh_event if fresh and current.ready and boundary_aligned else 0})
        previous_stamp = stamp
    return out


def daily_publication(snapshots, daily_times, period="weekly"):
    """Backward-compatible daily view of the confirmed publication contract."""
    return confirmed_publication(snapshots, daily_times, period)
