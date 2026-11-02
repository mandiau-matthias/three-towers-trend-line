"""Public-source acquisition, provenance, and strict UTC OHLC aggregation."""
import argparse
import calendar
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import time
from .model import Bar, WEEK

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def timestamp(value):
    try:
        return int(float(value))
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())


def write_bars(path, bars):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow([b.time, b.open, b.high, b.low, b.close, b.volume])


def read_bars(path):
    with Path(path).open(encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        if not rows.fieldnames or len(rows.fieldnames) != len(set(rows.fieldnames)):
            raise ValueError("CSV column names must be present and unique")
        result = [Bar(timestamp(r["time"]), *[float(r[k]) for k in ("open", "high", "low", "close")], float(r.get("volume") or r.get("Volume") or 0)) for r in rows]
    if any(b.time <= a.time for a, b in zip(result, result[1:])):
        raise ValueError("CSV timestamps must be ordered and unique")
    return result


def validate_daily_input(bars):
    if any(b.time % DAY for b in bars):
        raise ValueError("Daily bars must open at UTC midnight")
    if any(b.time <= a.time for a,b in zip(bars,bars[1:])):
        raise ValueError("Daily bars must be ordered and unique")


def weekly_from_daily(bars, as_of):
    """Only full Monday–Sunday UTC weeks; missing/invalid days invalidate a week."""
    validate_daily_input(bars)
    groups = {}
    for bar in bars:
        date = datetime.fromtimestamp(bar.time, timezone.utc)
        if bar.time % DAY:
            raise ValueError("Daily bars must open at UTC midnight")
        start = bar.time - date.weekday() * DAY
        groups.setdefault(start, {})[bar.time] = bar
    if not groups:
        return []
    result = []
    for start in range(min(groups), max(groups) + 1, WEEK):
        if start + WEEK > as_of:
            continue
        days = groups.get(start, {})
        ordered = [days.get(start + i * DAY) for i in range(7)]
        if any(b is None or not b.valid for b in ordered):
            result.append(Bar(start, float("nan"), float("nan"), float("nan"), float("nan")))
        else:
            result.append(Bar(start, ordered[0].open, max(b.high for b in ordered),
                              min(b.low for b in ordered), ordered[-1].close, sum(b.volume for b in ordered)))
    return result


def monthly_from_daily(bars, as_of):
    validate_daily_input(bars)
    groups = {}
    for bar in bars:
        d = datetime.fromtimestamp(bar.time, timezone.utc)
        groups.setdefault((d.year, d.month), {})[d.day] = bar
    result = []
    for (year, month), days in sorted(groups.items()):
        start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
        count = calendar.monthrange(year, month)[1]
        if start + count * DAY > as_of:
            continue
        ordered = [days.get(i) for i in range(1, count + 1)]
        if any(b is None or not b.valid for b in ordered):
            result.append(Bar(start, float("nan"), float("nan"), float("nan"), float("nan")))
        else:
            result.append(Bar(start, ordered[0].open, max(b.high for b in ordered),
                              min(b.low for b in ordered), ordered[-1].close, sum(b.volume for b in ordered)))
    return result


def get_json(url, params=None):
    import requests
    for attempt in range(3):
        response = requests.get(url, params=params, timeout=40, headers={"Accept": "application/json"})
        if response.status_code == 429 and attempt < 2:
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json(), response.url
    raise RuntimeError("Request retries exhausted")


def download_btc(as_of):
    cursor = int(datetime(2011, 8, 1, tzinfo=timezone.utc).timestamp())
    daily, urls = {}, []
    while cursor < as_of:
        end = min(cursor + 999 * DAY, as_of - 1)
        data, url = get_json("https://www.bitstamp.net/api/v2/ohlc/btcusd/", {
            "step": DAY, "limit": 1000, "start": cursor, "end": end, "exclude_current_candle": "true"})
        urls.append(url)
        for r in data["data"]["ohlc"]:
            t = int(r["timestamp"])
            if t + DAY <= as_of:
                daily[t] = Bar(t, *[float(r[k]) for k in ("open", "high", "low", "close", "volume")])
        cursor = end + DAY
    return sorted(daily.values(), key=lambda b: b.time), {"provider": "Bitstamp public OHLC API", "requests": urls}


def download_pool(market, as_of):
    import requests
    root = f'https://api.geckoterminal.com/api/v2/networks/{market["network"]}/pools/{market["pool"]}'
    metadata, url = get_json(root, {"include": "base_token,quote_token"})
    rel = metadata["data"]["relationships"]
    token_ids = {rel[k]["data"]["id"].lower() for k in ("base_token", "quote_token")}
    if f'{market["network"]}_{market["token"]}'.lower() not in token_ids:
        raise ValueError("Configured token does not belong to the configured pool")
    cursor, daily, urls, failures = as_of, {}, [url], []
    for _ in range(8):
        try:
            data, url = get_json(root + "/ohlcv/day", {"aggregate": 1, "limit": 1000,
                "currency": "usd", "token": market["token"], "before_timestamp": cursor})
        except requests.HTTPError as exc:
            failures.append(str(exc))
            break
        urls.append(url)
        rows = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        if not rows:
            break
        for r in rows:
            if int(r[0]) + DAY <= as_of:
                daily[int(r[0])] = Bar(int(r[0]), *map(float, r[1:6]))
        oldest = min(int(r[0]) for r in rows)
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(2)
    return sorted(daily.values(), key=lambda b: b.time), {"provider": "GeckoTerminal public pool OHLCV API",
        "requests": urls, "pool_metadata": metadata, "request_failures": failures,
        "caveat": "Same on-chain pool as the configured TradingView symbol; independently supplied candles may differ."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-09-05T00:00:00+00:00")
    parser.add_argument("--assets", nargs="*", default=["BTC", "PLS", "PLSX", "pHEX"])
    args = parser.parse_args()
    as_of = timestamp(args.as_of)
    markets = json.loads((ROOT / "config/markets.json").read_text())
    for asset in args.assets:
        market = markets[asset]
        bars, source = download_btc(as_of) if asset == "BTC" else download_pool(market, as_of)
        daily_path, weekly_path = ROOT / f"data/{asset}_daily.csv", ROOT / f"data/{asset}_weekly.csv"
        weekly = weekly_from_daily(bars, as_of)
        write_bars(daily_path, bars)
        write_bars(weekly_path, weekly)
        source.update({"asset": asset, "market": market, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
            "as_of_utc": iso(as_of), "daily_count": len(bars), "weekly_count": len(weekly),
            "valid_weeks": sum(b.valid for b in weekly), "invalid_weeks": [iso(b.time) for b in weekly if not b.valid],
            "first_daily": iso(bars[0].time) if bars else None, "last_daily": iso(bars[-1].time) if bars else None,
            "weekly_sha256": hashlib.sha256(weekly_path.read_bytes()).hexdigest()})
        (ROOT / f"data/{asset}_provenance.json").write_text(json.dumps(source, indent=2) + "\n")
        print(f"{asset}: {len(bars)} daily bars, {len(weekly)} closed weeks, {source['valid_weeks']} valid", flush=True)


if __name__ == "__main__":
    main()
