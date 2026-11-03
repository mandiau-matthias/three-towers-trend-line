"""Compare completed 1W bars from a TradingView chart export with the Python model."""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.data import read_bars, timestamp
from research.model import CURRENT_VERSION, MODEL_VERSIONS, calculate, period_close

# Diagnostic names are the titles in pine/view.pinepart, for either generated script.
PLOTS = {
    "regime": "TT Regime",
    "weakening": "TT Weakening",
    "line": "Trend Line",
    "ema": "TT EMA",
    "atr": "TT ATR",
    "confirmed_at": "TT Confirmed UTC seconds",
    "event": "TT Event",
    "first_source_time": "TT First source UTC seconds",
    "valid_bars": "TT Valid bars",
    "ready": "TT Ready",
    "model": "TT Model",
    "defaults": "TT Defaults",
    "exit_rule": "TT SELL rule (1 = below the sell line, 0 = 1.4 classic)",
}


def plot_columns(fields):
    columns = {}
    for field, title in PLOTS.items():
        matches = [name for name in fields if name == title or name.endswith(": " + title)]
        if len(matches) != 1:
            raise ValueError(f"Expected one {title!r} column, found {len(matches)}. Export one script at a time.")
        columns[field] = matches[0]
    return columns


def chart_value(cell):
    if cell is None:
        raise ValueError("Incomplete CSV row")
    raw = cell.strip()
    if raw.lower() in {"", "na", "nan", "null"}:
        return None
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"Non-finite chart value: {raw}")
    return value


def expected_values(snapshot, parameters):
    return {
        "regime": snapshot.regime if snapshot.ready else None,
        "weakening": int(snapshot.weakening) if snapshot.ready else None,
        "line": snapshot.line,
        "ema": snapshot.ema,
        "atr": snapshot.atr,
        "confirmed_at": snapshot.confirmed_at,
        "event": snapshot.event,
        "first_source_time": snapshot.first_source_time,
        "valid_bars": snapshot.valid_bars,
        "ready": int(snapshot.ready),
        "model": 1,
        "defaults": 1,
        "exit_rule": int(parameters.exit_rule == "band"),
    }


def check(path, cutoff, model=CURRENT_VERSION):
    # read_bars also checks duplicate headers and unsorted or duplicate timestamps.
    bars = read_bars(path)
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = plot_columns(reader.fieldnames or [])
        rows = list(reader)
    completed = [bar for bar in bars if period_close(bar.time, "weekly") <= cutoff]
    if not completed:
        raise ValueError("No completed weekly bars before the cutoff")
    if any(not bar.valid for bar in completed):
        raise ValueError("Completed candles must have valid finite OHLC prices")
    parameters = MODEL_VERSIONS[model]
    snapshots = calculate(completed, parameters, "weekly")
    if not any(s.ready for s in snapshots):
        raise ValueError("Export more history: the reference model never completed warm-up.")

    mismatch_count = 0
    examples = []
    for row, snapshot in zip(rows, snapshots):
        if None in row:
            raise ValueError("CSV row has more cells than headers")
        for field, expected in expected_values(snapshot, parameters).items():
            actual = chart_value(row[columns[field]])
            same = actual == expected
            if field in {"line", "ema", "atr"} and actual is not None and expected is not None:
                same = math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-12)
            if not same:
                mismatch_count += 1
                if len(examples) < 10:
                    examples.append({"bar_start": snapshot.time, "field": field, "pine": actual, "python": expected})
    return {
        "status": "failed" if mismatch_count else "passed",
        "model": model,
        "bars": len(completed),
        "first_source_time": completed[0].time,
        "last_confirmation": snapshots[-1].confirmed_at,
        "mismatch_count": mismatch_count,
        "first_mismatches": examples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--as-of", required=True, help="UTC cutoff as ISO date, timestamp, or Unix seconds")
    parser.add_argument("--model", choices=sorted(MODEL_VERSIONS), default=CURRENT_VERSION)
    args = parser.parse_args()
    try:
        result = check(args.csv, timestamp(args.as_of), args.model)
    except (OSError, KeyError, TypeError, ValueError, OverflowError, csv.Error) as error:
        parser.exit(2, f"Cannot verify chart export: {error}\n")
    print(json.dumps(result, indent=2))
    return 1 if result["mismatch_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
