"""Rebuild the illustrated warning from synthetic weekly candles."""
from pathlib import Path
from datetime import datetime, timezone
import sys
from html import escape
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.backtest import simulate
from research.model import Bar, CURRENT, WEEK, calculate
prices = [100 * 1.03**i for i in range(100)]
peak = prices[-1]
prices.extend(peak * n for n in (.80, 1.05, 1.08, 1.12))
start = int(datetime(2022, 1, 3, tzinfo=timezone.utc).timestamp())
bars = [Bar(start+i*WEEK, p, p*1.01, p*.99, p, 100) for i,p in enumerate(prices)]
signals = calculate(bars, CURRENT)
series = signals[94:]
_, trades, curve = simulate(
    bars, signals, start, bars[-1].time + WEEK,
    fee=0.003, slippage=0, allocation=0.99, enter_existing=False,
)
positions = {point["time"]: point["invested"] for point in curve}
assert series[6].warn_event and series[6].regime == 1
assert not series[7].weakening and not series[7].bear_event
assert positions[series[6].confirmed_at] and positions[series[7].confirmed_at]
assert len(trades) == 1 and trades[0]["exit_time"] is None
def x(i): return 58 + i * 66
def y(v): return 221 - (v-1200)*.16
def points(field): return " ".join(f"{x(i)},{y(getattr(row,field)):.1f}" for i,row in enumerate(series))
svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 296" role="img" aria-label="Synthetic prices dip, trigger a warning, then recover the following week."><rect width="720" height="296" rx="16" fill="#191923"/><g fill="#f6f3eb" font-family="system-ui,sans-serif"><text x="30" y="36" font-size="22">A warning that cleared without a sell</text><text x="30" y="59" fill="#ccc4df" font-size="14">Synthetic weekly closes · violet price / gold adaptive line</text>']
for field,color in [('line','#e9bb64'),('price','#c4adff')]:
    svg.append(f'<polyline fill="none" stroke="{color}" stroke-width="3.5" points="{points(field)}"/>')
for i,label,dy in [(6,'Warning',33),(7,'Recovered',-16)]:
    row=series[i]; svg.append(f'<circle cx="{x(i)}" cy="{y(row.price):.1f}" r="5" fill="#fff"/><text x="{x(i)}" y="{y(row.price)+dy:.1f}" text-anchor="middle" font-size="14">{escape(label)}</text>')
svg.append('<text x="30" y="273" fill="#ccc4df" font-size="14">One point per completed week · illustrative price units</text></g></svg>')
out=ROOT/'examples/warning.svg'
out.parent.mkdir(exist_ok=True)
out.write_text("".join(svg)+"\n")
for row in series[5:8]:
    position = "holding" if positions[row.confirmed_at] else "cash"
    print(datetime.fromtimestamp(row.confirmed_at, timezone.utc).date(), f"close={row.price:.2f}",
          f"warning={row.weakening}", f"sell={row.bear_event}", f"backtest={position}")
entry = datetime.fromtimestamp(trades[0]["entry_time"], timezone.utc).date()
print(f"Trade entered {entry} at the next open. Still open at the end of the example.")
