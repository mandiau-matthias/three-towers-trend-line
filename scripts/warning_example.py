"""Rebuild the illustrated warning from synthetic weekly candles."""
from pathlib import Path
from datetime import datetime, timezone
import sys
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

def sell_line(row):
    return row.line - CURRENT.atr_buffer * row.atr


def x(i):
    return 78 + i * 70


def y(price):
    return 285 - (price - 1400) * 0.25


assert series[6].price < sell_line(series[6])
assert series[7].price > sell_line(series[7])

svg = [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 342" role="img" aria-labelledby="title desc">',
    '<title id="title">A warning that cleared without a sell</title>',
    '<desc id="desc">The weekly close fell below the dashed sell line on 11 December 2023, '
    'then recovered above it on 18 December. The backtest held throughout.</desc>',
    '<rect width="760" height="342" rx="16" fill="#191923"/>',
    '<g fill="#f6f3eb" font-family="system-ui,sans-serif">',
    '<text x="28" y="37" font-size="24">A warning that cleared without a sell</text>',
    '<path d="M28 64h27" stroke="#c4adff" stroke-width="3"/>',
    '<text x="63" y="69" font-size="14">Weekly close</text>',
    '<path d="M181 64h27" stroke="#e9bb64" stroke-width="3" stroke-dasharray="6 4"/>',
    '<text x="216" y="69" font-size="14">Sell line</text>',
]
for price in (1500, 1750, 2000):
    svg.append(f'<path d="M78 {y(price)}h630" stroke="#393947"/>')
    svg.append(f'<text x="66" y="{y(price)+5}" text-anchor="end" fill="#ccc4df" font-size="13">{price:,}</text>')
for values, style in [
    ([sell_line(row) for row in series], 'stroke="#e9bb64" stroke-dasharray="6 4"'),
    ([row.price for row in series], 'stroke="#c4adff"'),
]:
    points = " ".join(f"{x(i)},{y(value):.1f}" for i, value in enumerate(values))
    svg.append(f'<polyline fill="none" {style} stroke-width="3" points="{points}"/>')
for i, label, offset in [(6, "Warning", 25), (7, "Recovered", -17)]:
    row = series[i]
    date = datetime.fromtimestamp(row.confirmed_at, timezone.utc).strftime("%d %b")
    svg.append(f'<circle cx="{x(i)}" cy="{y(row.price):.1f}" r="5" fill="#fff"/>')
    svg.append(f'<text x="{x(i)}" y="{y(row.price)+offset:.1f}" text-anchor="middle" font-size="14">{date} · {label}</text>')
svg.append('<text x="28" y="322" fill="#ccc4df" font-size="14">Fictional prices · one point per completed week</text></g></svg>')
out = ROOT / "examples/warning.svg"
out.parent.mkdir(exist_ok=True)
out.write_text("\n".join(svg) + "\n")
for row in series[5:8]:
    position = "holding" if positions[row.confirmed_at] else "cash"
    print(datetime.fromtimestamp(row.confirmed_at, timezone.utc).date(), f"close={row.price:.2f}",
          f"sell_line={sell_line(row):.2f}", f"warning={row.weakening}", f"sell={row.bear_event}", f"backtest={position}")
entry = datetime.fromtimestamp(trades[0]["entry_time"], timezone.utc).date()
print(f"Trade entered {entry} at the next open. Still open at the end of the example.")
