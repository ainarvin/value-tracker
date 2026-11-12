"""Value tracker: live margin-of-safety dashboard for long-term holdings.

    python value_tracker.py            # opens http://127.0.0.1:8060
    python value_tracker.py --no-open  # just start the server
    python value_tracker.py --lan      # also reachable from other devices on your Wi-Fi
    python value_tracker.py --export site   # static snapshot for GitHub Pages (used by the scheduled job)

Live prices/fundamentals come from Yahoo (yfinance). Your valuation assumptions live in
valuation.json; edit that file and refresh the page to re-run the models.
"""
import json
import math
import os
import shutil
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yfinance as yf

HOST, PORT = ("0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"), 8060
HERE = os.path.dirname(os.path.abspath(__file__))
INFO_TTL, HIST_TTL = 300, 1800
_cache, _lock = {}, threading.Lock()

INFO_KEYS = ["currentPrice", "regularMarketPrice", "currency", "marketCap", "trailingPE", "forwardPE",
             "forwardEps", "enterpriseToEbitda", "freeCashflow", "totalRevenue", "revenueGrowth",
             "operatingMargins", "debtToEquity", "totalDebt", "totalCash", "ebitda", "returnOnEquity",
             "dividendYield", "dividendRate", "trailingAnnualDividendRate", "payoutRatio", "exDividendDate",
             "beta", "fiftyTwoWeekLow", "fiftyTwoWeekHigh", "targetMeanPrice"]


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def fetch_info(t):
    def go():
        try:
            i = yf.Ticker(t).info
            return {k: i.get(k) for k in INFO_KEYS}
        except Exception as e:  # network / Yahoo hiccup
            return {"error": str(e)}
    return cached("info:" + t, INFO_TTL, go)


def fetch_history(tickers):
    def go():
        df = yf.download(tickers, period="1y", interval="1d", auto_adjust=True, progress=False)["Close"]
        out = {}
        for t in tickers:
            if t not in df:
                continue
            s = df[t].dropna()
            out[t] = {"dates": [d.strftime("%Y-%m-%d") for d in s.index], "close": [round(float(v), 4) for v in s]}
        return out
    return cached("hist:" + ",".join(sorted(tickers)), HIST_TTL, go)


def num(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x


def dcf(base, g1, g2, tg, r, start=0):
    """Two-stage owner-earnings DCF. start=5 values the business as of year 5."""
    v, cf = 0.0, base
    for yr in range(1, 11):
        cf *= 1 + (g1 if yr <= 5 else g2)
        if yr > start:
            v += cf / (1 + r) ** (yr - start)
    return v + cf * (1 + tg) / (r - tg) / (1 + r) ** (10 - start)


def five_year_return(cfg, price):
    """Annualised 5y return if the stock ends at its year-5 intrinsic value, counting
    owner earnings of years 1-5 as returned to you (dividends + buybacks)."""
    b, g1 = cfg["base"], cfg["g1"]
    cash = sum(b * (1 + g1) ** y for y in range(1, 6))
    iv5 = 0.6 * dcf(b, g1, cfg["g2"], cfg["tg"], cfg["r"], start=5) + 0.4 * cfg["fair_pe"] * b * (1 + g1) ** 5
    return ((iv5 + cash) / price) ** 0.2 - 1


def pct_change(close, n):
    return None if len(close) <= n else close[-1] / close[-1 - n] - 1


def analyse(cfg, info, hist):
    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    row = {k: cfg.get(k) for k in ("ticker", "name", "group", "risk", "basis", "driver", "black_swan", "moat",
                                   "base", "g1", "g2", "tg", "r", "fair_pe", "required_mos")}
    row["info"] = {k: num(v) for k, v in info.items()}
    if hist and hist.get("close"):
        c = hist["close"]
        price = price or c[-1]
        ma200 = sum(c[-200:]) / min(len(c), 200)
        row["perf"] = {"1m": pct_change(c, 21), "3m": pct_change(c, 63), "1y": c[-1] / c[0] - 1,
                       "from_high": c[-1] / max(c) - 1, "vs_ma200": c[-1] / ma200 - 1}
    if not price:
        row["error"] = info.get("error", "no price")
        return row
    iv_dcf = dcf(cfg["base"], cfg["g1"], cfg["g2"], cfg["tg"], cfg["r"])
    iv_mult = cfg["fair_pe"] * cfg["base"]
    iv = 0.6 * iv_dcf + 0.4 * iv_mult
    bear = cfg["bear_eps"] * cfg["bear_pe"]
    up, down = iv / price - 1, max(0.0, 1 - bear / price)
    cagr = five_year_return(cfg, price)
    div = num(info.get("dividendRate")) or num(info.get("trailingAnnualDividendRate")) or 0
    ex = num(info.get("exDividendDate"))
    row["dividend"] = {"per_share": div, "yield": div / price if div else 0,
                       "payout": num(info.get("payoutRatio")),
                       "ex_date": datetime.fromtimestamp(ex, timezone.utc).strftime("%Y-%m-%d") if ex and div else None}
    mos = 1 - price / iv
    req = cfg.get("required_mos", 0.2)
    signal = ("BUY ZONE" if mos >= req else "ACCUMULATE" if mos >= req / 2 else
              "HOLD" if mos >= -0.10 else "EXPENSIVE")
    row.update(price=price, iv_dcf=iv_dcf, iv_mult=iv_mult, iv=iv, bear=bear, mos=mos, upside=up,
               downside=down, rr=(up / down if down > 0.005 else None), cagr5=cagr,
               buy_below=iv * (1 - req), signal=signal, earnings_yield=cfg["base"] / price)
    return row


def load_cfg():
    with open(os.path.join(HERE, "valuation.json"), encoding="utf-8") as f:
        return json.load(f)


def build():
    cfg = load_cfg()
    stocks = cfg["stocks"]
    tickers = [s["ticker"] for s in stocks]
    with ThreadPoolExecutor(8) as ex:
        infos = dict(zip(tickers, ex.map(fetch_info, tickers)))
    try:
        hist = fetch_history(tickers)
    except Exception:
        hist = {}
    rows = [analyse(s, infos[s["ticker"]], hist.get(s["ticker"])) for s in stocks]
    bench = next((r for r in rows if r["ticker"] == cfg.get("benchmark")), None)
    for r in rows:
        if bench and r.get("cagr5") is not None and bench.get("cagr5") is not None:
            r["alpha5"] = r["cagr5"] - bench["cagr5"]
    return {"asof": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows,
            "history": hist, "benchmark": cfg.get("benchmark")}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/data"):
            try:
                self._send(200, json.dumps(build(), allow_nan=False, default=str), "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *a):
        pass


def export(out_dir):
    """Write a static snapshot (index.html + data.json) for GitHub Pages.
    Refuses to overwrite the last good snapshot if Yahoo returned too little data."""
    data = build()
    good = sum(1 for r in data["rows"] if r.get("price"))
    if good < len(data["rows"]) / 2:
        sys.exit(f"Only {good}/{len(data['rows'])} prices fetched; keeping the previous snapshot.")
    data["source"] = "snapshot"
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, "data.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, allow_nan=False, default=str, separators=(",", ":"))
    os.replace(tmp, os.path.join(out_dir, "data.json"))
    shutil.copyfile(os.path.join(HERE, "index.html"), os.path.join(out_dir, "index.html"))
    print(f"Snapshot written to {out_dir}: {good}/{len(data['rows'])} prices, as of {data['asof']}")


if __name__ == "__main__":
    if "--export" in sys.argv:
        i = sys.argv.index("--export")
        export(sys.argv[i + 1] if len(sys.argv) > i + 1 else os.path.join(HERE, "site"))
        sys.exit(0)
    srv = ThreadingHTTPServer((HOST, PORT), H)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Value tracker on {url}  (Ctrl+C to stop)")
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    srv.serve_forever()
