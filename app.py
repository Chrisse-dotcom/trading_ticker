from flask import Flask, jsonify, request, render_template, make_response
from flask_cors import CORS
import requests
import time
import random
import math

app = Flask(__name__)
CORS(app)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict = {}


def cache_get(key: str, ttl: int = 60):
    entry = _cache.get(key)
    if entry and time.time() - entry["t"] < ttl:
        return entry["d"]
    return None


def cache_set(key: str, data):
    _cache[key] = {"d": data, "t": time.time()}


# ── Constants ──────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

MARKET_SYMBOLS = {
    "stocks": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META", "JPM"],
    "etfs":   ["SPY", "QQQ", "VTI", "IWM", "GLD", "TLT", "ARKK", "XLF"],
    "commodities": ["GC=F", "SI=F", "CL=F", "NG=F", "HG=F", "ZW=F", "ZC=F", "BZ=F"],
}

COMMODITY_META = {
    "GC=F": ("Gold",          "COMEX (CME Group)"),
    "SI=F": ("Silber",        "COMEX (CME Group)"),
    "CL=F": ("Rohöl (WTI)",  "NYMEX (CME Group)"),
    "NG=F": ("Erdgas",        "NYMEX (CME Group)"),
    "HG=F": ("Kupfer",        "COMEX (CME Group)"),
    "ZW=F": ("Weizen",        "CBOT (CME Group)"),
    "ZC=F": ("Mais",          "CBOT (CME Group)"),
    "BZ=F": ("Brent Crude",  "ICE Futures Europe"),
}

CRYPTO_DEFAULT = [
    "bitcoin", "ethereum", "solana", "binancecoin",
    "ripple", "cardano", "dogecoin", "avalanche-2",
]


# ── Helpers ────────────────────────────────────────────────────────────────────
def pct(new_val, old_val):
    return round((new_val - old_val) / old_val * 100, 2) if old_val else 0.0


def calc_recommendation(prices, current):
    prices = [p for p in (prices or []) if p is not None]
    if len(prices) < 3:
        return {
            "signal": "neutral", "label": "Neutral",
            "reason": "Nicht genug Daten für Analyse",
            "rsi": 50.0,
            "sma5": round(current, 6), "sma20": round(current, 6),
            "buy_at": round(current * 0.97, 6),
            "sell_at": round(current * 1.05, 6),
        }

    n = len(prices)
    sma5  = sum(prices[-5:]) / min(5, n)
    sma20 = sum(prices[-20:]) / min(20, n)

    diffs  = [prices[i] - prices[i - 1] for i in range(1, n)]
    gains  = [d for d in diffs if d > 0]
    losses = [abs(d) for d in diffs if d < 0]
    ag = sum(gains)  / max(len(gains),  1) if gains  else 0
    al = sum(losses) / max(len(losses), 1) if losses else 1e-9
    rsi = 100 - 100 / (1 + ag / al)

    if rsi < 30:
        sig, lbl = "strong_buy",  "Stark Kaufen"
        reason = f"Überverkauft (RSI {rsi:.0f}) – sehr attraktiver Einstiegspunkt"
    elif rsi < 45:
        sig, lbl = "buy",         "Kaufen"
        reason = f"Niedriger RSI ({rsi:.0f}) – bullisches Signal erkannt"
    elif rsi > 70:
        sig, lbl = "strong_sell", "Stark Verkaufen"
        reason = f"Stark überkauft (RSI {rsi:.0f}) – Gewinnmitnahme empfohlen"
    elif rsi > 60:
        sig, lbl = "sell",        "Verkaufen"
        reason = f"Hoher RSI ({rsi:.0f}) – Vorsicht, Abwärtspotenzial"
    else:
        sig, lbl = "hold",        "Halten"
        reason = f"Neutraler RSI ({rsi:.0f}) – Position halten und beobachten"

    return {
        "signal": sig, "label": lbl, "reason": reason,
        "rsi":    round(rsi, 1),
        "sma5":   round(sma5,  6),
        "sma20":  round(sma20, 6),
        "buy_at":  round(sma20 * 0.97, 6),
        "sell_at": round(sma20 * 1.05, 6),
    }


def _age(ts):
    if not ts:
        return ""
    diff = int(time.time()) - ts
    if diff < 3600:   return f"vor {diff // 60} Min."
    if diff < 86400:  return f"vor {diff // 3600} Std."
    return f"vor {diff // 86400} Tagen"


# ── Yahoo Finance ──────────────────────────────────────────────────────────────
def yahoo_quote(symbol: str) -> dict | None:
    cached = cache_get(f"yq_{symbol}")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            headers=HEADERS,
            params={"interval": "1d", "range": "1mo"},
            timeout=9,
        )
        chart   = r.json()["chart"]["result"][0]
        meta    = chart["meta"]
        closes  = [c for c in chart["indicators"]["quote"][0].get("close", []) if c]

        current = float(meta.get("regularMarketPrice") or 0)
        prev    = float(
            meta.get("previousClose") or
            meta.get("chartPreviousClose") or
            current
        )
        w_price = closes[-6] if len(closes) >= 6 else (closes[0] if closes else current)
        m_price = closes[0]  if closes else current

        name = meta.get("shortName") or meta.get("longName") or symbol
        exch = meta.get("fullExchangeName") or meta.get("exchangeName", "N/A")

        if symbol in COMMODITY_META:
            name, exch = COMMODITY_META[symbol]

        rec = calc_recommendation(closes, current)

        data = {
            "symbol":     symbol,
            "name":       name,
            "price":      round(current, 6),
            "currency":   meta.get("currency", "USD"),
            "change_24h": pct(current, prev),
            "change_7d":  pct(current, w_price),
            "change_30d": pct(current, m_price),
            "exchange":   exch,
            "volume":     int(meta.get("regularMarketVolume") or 0),
            "sparkline":  closes[-20:],
            "recommendation": rec,
        }
        cache_set(f"yq_{symbol}", data)
        return data
    except Exception:
        return _demo_yahoo(symbol)


def _make_sparkline(base, days=20, volatility=0.02):
    """Generate a realistic-looking price history."""
    prices = [base]
    for _ in range(days - 1):
        change = random.gauss(0, volatility)
        prices.append(round(prices[-1] * (1 + change), 6))
    return prices


def _demo_yahoo(symbol: str) -> dict | None:
    """Fallback demo data when live API is unavailable."""
    demo = {
        # Stocks
        "AAPL":  ("Apple Inc.",               195.89,  "USD", "NASDAQ",        0.8e12),
        "MSFT":  ("Microsoft Corp.",           415.26,  "USD", "NASDAQ",        3.1e12),
        "NVDA":  ("NVIDIA Corp.",              875.39,  "USD", "NASDAQ",        2.2e12),
        "GOOGL": ("Alphabet Inc.",             171.94,  "USD", "NASDAQ",        2.1e12),
        "AMZN":  ("Amazon.com Inc.",           183.75,  "USD", "NASDAQ",        1.9e12),
        "TSLA":  ("Tesla Inc.",                177.48,  "USD", "NASDAQ",        0.57e12),
        "META":  ("Meta Platforms Inc.",       510.23,  "USD", "NASDAQ",        1.3e12),
        "JPM":   ("JPMorgan Chase & Co.",      196.33,  "USD", "NYSE",          0.57e12),
        # ETFs
        "SPY":   ("SPDR S&P 500 ETF",         524.85,  "USD", "NYSE Arca",     0),
        "QQQ":   ("Invesco QQQ Trust",         440.70,  "USD", "NASDAQ",        0),
        "VTI":   ("Vanguard Total Stock",      240.12,  "USD", "NYSE Arca",     0),
        "IWM":   ("iShares Russell 2000 ETF",  198.44,  "USD", "NYSE Arca",     0),
        "GLD":   ("SPDR Gold Shares",          214.63,  "USD", "NYSE Arca",     0),
        "TLT":   ("iShares 20+ Year Treasury",  92.18,  "USD", "NASDAQ",        0),
        "ARKK":  ("ARK Innovation ETF",         46.72,  "USD", "NYSE Arca",     0),
        "XLF":   ("Financial Select Sector",    41.25,  "USD", "NYSE Arca",     0),
        # Commodities
        "GC=F":  ("Gold",                     2328.40,  "USD", "COMEX (CME Group)",  0),
        "SI=F":  ("Silber",                     27.45,  "USD", "COMEX (CME Group)",  0),
        "CL=F":  ("Rohöl (WTI)",               78.62,  "USD", "NYMEX (CME Group)",  0),
        "NG=F":  ("Erdgas",                      2.18,  "USD", "NYMEX (CME Group)",  0),
        "HG=F":  ("Kupfer",                      4.51,  "USD", "COMEX (CME Group)",  0),
        "ZW=F":  ("Weizen",                    560.25,  "USD", "CBOT (CME Group)",   0),
        "ZC=F":  ("Mais",                      449.50,  "USD", "CBOT (CME Group)",   0),
        "BZ=F":  ("Brent Crude",                82.14,  "USD", "ICE Futures Europe", 0),
    }
    if symbol not in demo:
        return None
    name, price, currency, exchange, market_cap = demo[symbol]
    random.seed(hash(symbol) % 9999)
    spark  = _make_sparkline(price * 0.95, days=20, volatility=0.015)
    c24h   = round(random.uniform(-3.5, 4.0), 2)
    c7d    = round(random.uniform(-8.0, 9.0), 2)
    c30d   = round(random.uniform(-15.0, 18.0), 2)
    rec    = calc_recommendation(spark, price)
    return {
        "symbol":     symbol,
        "name":       name,
        "price":      price,
        "currency":   currency,
        "change_24h": c24h,
        "change_7d":  c7d,
        "change_30d": c30d,
        "exchange":   exchange,
        "volume":     int(random.uniform(5e6, 80e6)),
        "market_cap": int(market_cap) if market_cap else 0,
        "sparkline":  spark,
        "recommendation": rec,
        "_demo": True,
    }


# ── CoinGecko ─────────────────────────────────────────────────────────────────
def coingecko_tops(ids=None) -> list:
    id_list = ids or CRYPTO_DEFAULT
    key = "cg_tops_" + ",".join(id_list)
    cached = cache_get(key)
    if cached:
        return cached
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            headers=HEADERS,
            params={
                "vs_currency": "eur",
                "ids": ",".join(id_list),
                "sparkline": "true",
                "price_change_percentage": "24h,7d,30d",
                "order": "market_cap_desc",
            },
            timeout=12,
        )
        result = []
        for c in r.json():
            spark = c.get("sparkline_in_7d", {}).get("price", [])
            price = float(c.get("current_price") or 0)
            rec   = calc_recommendation(spark, price)
            result.append({
                "symbol":     c["symbol"].upper(),
                "id":         c["id"],
                "name":       c["name"],
                "price":      price,
                "currency":   "EUR",
                "change_24h": round(float(c.get("price_change_percentage_24h") or 0), 2),
                "change_7d":  round(float(c.get("price_change_percentage_7d_in_currency") or 0), 2),
                "change_30d": round(float(c.get("price_change_percentage_30d_in_currency") or 0), 2),
                "exchange":   "Binance / Coinbase / Kraken",
                "volume":     int(c.get("total_volume") or 0),
                "market_cap": int(c.get("market_cap") or 0),
                "image":      c.get("image", ""),
                "sparkline":  spark[-20:] if len(spark) > 20 else spark,
                "recommendation": rec,
            })
        cache_set(key, result)
        return result
    except Exception:
        return _demo_crypto(id_list)


CRYPTO_DEMO_DATA = {
    "bitcoin":     ("BTC", "Bitcoin",      67850.0,   1.26e12),
    "ethereum":    ("ETH", "Ethereum",      3542.0,   4.25e11),
    "solana":      ("SOL", "Solana",         185.4,   8.5e10),
    "binancecoin": ("BNB", "BNB",            607.2,   9.1e10),
    "ripple":      ("XRP", "XRP",             0.626,  3.5e10),
    "cardano":     ("ADA", "Cardano",         0.483,  1.7e10),
    "dogecoin":    ("DOGE","Dogecoin",        0.172,  2.5e10),
    "avalanche-2": ("AVAX","Avalanche",       39.8,   1.6e10),
}


def _demo_crypto(ids=None) -> list:
    result = []
    for coin_id in (ids or CRYPTO_DEFAULT):
        if coin_id not in CRYPTO_DEMO_DATA:
            continue
        sym, name, price, mcap = CRYPTO_DEMO_DATA[coin_id]
        random.seed(hash(coin_id) % 9999)
        spark  = _make_sparkline(price * 0.9, days=20, volatility=0.03)
        c24h   = round(random.uniform(-5.0, 6.0), 2)
        c7d    = round(random.uniform(-12.0, 14.0), 2)
        c30d   = round(random.uniform(-25.0, 30.0), 2)
        rec    = calc_recommendation(spark, price)
        result.append({
            "symbol":     sym,
            "id":         coin_id,
            "name":       name,
            "price":      price,
            "currency":   "EUR",
            "change_24h": c24h,
            "change_7d":  c7d,
            "change_30d": c30d,
            "exchange":   "Binance / Coinbase / Kraken",
            "volume":     int(random.uniform(1e9, 50e9)),
            "market_cap": int(mcap),
            "image":      "",
            "sparkline":  spark,
            "recommendation": rec,
            "_demo": True,
        })
    return result


def coingecko_detail(coin_id: str) -> dict | None:
    cached = cache_get(f"cg_detail_{coin_id}", ttl=120)
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            headers=HEADERS,
            params={"sparkline": "true", "localization": "false"},
            timeout=12,
        )
        c  = r.json()
        md = c.get("market_data", {})
        price = float(md.get("current_price", {}).get("eur") or 0)
        spark = md.get("sparkline_7d", {}).get("price", [])

        # Best exchange by bid volume from tickers
        tickers = sorted(
            c.get("tickers", []),
            key=lambda t: float(t.get("volume") or 0),
            reverse=True
        )
        best_exch = tickers[0]["market"]["name"] if tickers else "Binance / Coinbase / Kraken"

        desc_de = (c.get("description", {}).get("de") or "").strip()
        desc_en = (c.get("description", {}).get("en") or "").strip()
        description = (desc_de or desc_en)[:600]
        # Strip HTML tags simply
        import re
        description = re.sub(r"<[^>]+>", "", description)

        data = {
            "symbol":     c["symbol"].upper(),
            "id":         c["id"],
            "name":       c["name"],
            "price":      price,
            "currency":   "EUR",
            "change_24h": round(float(md.get("price_change_percentage_24h") or 0), 2),
            "change_7d":  round(float(md.get("price_change_percentage_7d")  or 0), 2),
            "change_30d": round(float(md.get("price_change_percentage_30d") or 0), 2),
            "exchange":   best_exch,
            "volume":     int(md.get("total_volume", {}).get("eur") or 0),
            "market_cap": int(md.get("market_cap",   {}).get("eur") or 0),
            "image":      c.get("image", {}).get("small", ""),
            "sparkline":  spark[-20:] if len(spark) > 20 else spark,
            "description": description,
            "recommendation": calc_recommendation(spark, price),
        }
        cache_set(f"cg_detail_{coin_id}", data)
        return data
    except Exception:
        items = _demo_crypto([coin_id])
        return items[0] if items else None


# ── News via Yahoo Finance ─────────────────────────────────────────────────────
def fetch_news(query: str) -> list:
    cached = cache_get(f"news_{query}", ttl=300)
    if cached:
        return cached
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            headers=HEADERS,
            params={"q": query, "newsCount": 6, "enableFuzzyQuery": "false"},
            timeout=8,
        )
        news = []
        for item in r.json().get("news", []):
            news.append({
                "title":     item.get("title", ""),
                "publisher": item.get("publisher", ""),
                "link":      item.get("link", "#"),
                "age":       _age(item.get("providerPublishTime", 0)),
            })
        cache_set(f"news_{query}", news)
        return news
    except Exception:
        return _demo_news(query)


DEMO_NEWS_TEMPLATES = [
    ("{name}: Analysten heben Kursziel auf neues Allzeithoch",          "Handelsblatt"),
    ("{name} übertrifft Gewinnerwartungen im letzten Quartal",          "Reuters"),
    ("Institutionelle Investoren erhöhen {name}-Positionen deutlich",   "Bloomberg"),
    ("{name}: Technische Analyse zeigt starke Unterstützungszone",      "Finanzen.net"),
    ("Marktüberblick: {name} im Fokus der Anleger",                    "Börse Online"),
    ("Analyst-Kommentar: {name} mit positivem Ausblick für Q4",        "DZ Bank Research"),
]


def _demo_news(query: str) -> list:
    random.seed(hash(query) % 7777)
    result = []
    for i, (title_tpl, pub) in enumerate(random.sample(DEMO_NEWS_TEMPLATES, 4)):
        result.append({
            "title":     title_tpl.format(name=query),
            "publisher": pub,
            "link":      "#",
            "age":       f"vor {random.randint(1, 23)} Std.",
        })
    return result


# ── OHLCV / Candlestick Data ───────────────────────────────────────────────────
def fetch_ohlcv_yahoo(symbol: str, interval: str = "1d", period: str = "3mo") -> list:
    cached = cache_get(f"ohlcv_{symbol}")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            headers=HEADERS,
            params={"interval": interval, "range": period},
            timeout=9,
        )
        chart = r.json()["chart"]["result"][0]
        ts    = chart.get("timestamp", [])
        q     = chart["indicators"]["quote"][0]
        opens  = q.get("open",   [])
        highs  = q.get("high",   [])
        lows   = q.get("low",    [])
        closes = q.get("close",  [])
        vols   = q.get("volume", [None] * len(ts))

        candles = []
        for i, t in enumerate(ts):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
            if all(x is not None for x in [o, h, l, c]):
                candles.append({
                    "t": t,
                    "o": round(o, 6), "h": round(h, 6),
                    "l": round(l, 6), "c": round(c, 6),
                    "v": int(v or 0),
                })
        cache_set(f"ohlcv_{symbol}", candles)
        return candles
    except Exception:
        return _demo_ohlcv(symbol)


def fetch_ohlcv_crypto(coin_id: str, days: int = 30) -> list:
    cached = cache_get(f"ohlcv_cg_{coin_id}")
    if cached:
        return cached
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
            headers=HEADERS,
            params={"vs_currency": "eur", "days": days},
            timeout=10,
        )
        candles = [
            {"t": d[0] // 1000, "o": d[1], "h": d[2], "l": d[3], "c": d[4], "v": 0}
            for d in r.json()
        ]
        cache_set(f"ohlcv_cg_{coin_id}", candles)
        return candles
    except Exception:
        return _demo_ohlcv_crypto(coin_id)


def _demo_ohlcv(symbol: str) -> list:
    base_prices = {
        "AAPL": 195.89, "MSFT": 415.26, "NVDA": 875.39, "GOOGL": 171.94,
        "AMZN": 183.75, "TSLA": 177.48, "META": 510.23, "JPM":   196.33,
        "SPY":  524.85, "QQQ":  440.70, "VTI":  240.12, "IWM":   198.44,
        "GLD":  214.63, "TLT":   92.18, "ARKK":  46.72, "XLF":    41.25,
        "GC=F": 2328.40, "SI=F":  27.45, "CL=F":  78.62, "NG=F":   2.18,
        "HG=F":    4.51, "ZW=F": 560.25, "ZC=F": 449.50, "BZ=F":  82.14,
    }
    base = base_prices.get(symbol, 100.0)
    return _gen_ohlcv(base, volatility=0.015, seed=hash(symbol) % 9999)


def _demo_ohlcv_crypto(coin_id: str) -> list:
    base_prices = {
        "bitcoin": 67850.0, "ethereum": 3542.0, "solana": 185.4,
        "binancecoin": 607.2, "ripple": 0.626, "cardano": 0.483,
        "dogecoin": 0.172, "avalanche-2": 39.8,
    }
    base = base_prices.get(coin_id, 1.0)
    return _gen_ohlcv(base, volatility=0.03, seed=hash(coin_id) % 9999 + 1)


def _gen_ohlcv(base: float, days: int = 45, volatility: float = 0.02, seed: int = 42) -> list:
    """Generate realistic demo OHLCV candlestick data."""
    random.seed(seed)
    now   = int(time.time())
    start = now - days * 86400
    price = base * 0.88
    candles = []
    for i in range(days):
        drift  = random.gauss(0.0005, volatility)
        open_p = price
        close_p = price * (1 + drift)
        wick_range = abs(close_p - open_p) * random.uniform(0.3, 1.2)
        high_p  = max(open_p, close_p) + wick_range * random.uniform(0.1, 0.7)
        low_p   = min(open_p, close_p) - wick_range * random.uniform(0.1, 0.7)
        candles.append({
            "t": start + i * 86400,
            "o": round(open_p,  6), "h": round(high_p,  6),
            "l": round(low_p,   6), "c": round(close_p, 6),
            "v": int(random.uniform(1e6, 50e6)),
        })
        price = close_p
    return candles


# ── Candlestick Pattern Detection ──────────────────────────────────────────────
def detect_patterns(candles: list) -> list:
    """
    Detect common candlestick patterns in the last ~10 candles.
    Returns a list of detected patterns with index, name, signal, desc.
    """
    patterns = []
    n = len(candles)
    if n < 2:
        return patterns

    for i in range(1, n):
        c    = candles[i]
        prev = candles[i - 1]
        body       = abs(c["c"] - c["o"])
        total      = c["h"] - c["l"] or 1e-9
        upper_w    = c["h"] - max(c["o"], c["c"])
        lower_w    = min(c["o"], c["c"]) - c["l"]
        is_bull    = c["c"] >= c["o"]
        body_ratio = body / total

        prev_body  = abs(prev["c"] - prev["o"])
        prev_bull  = prev["c"] >= prev["o"]

        # Only annotate recent candles (last 10) to avoid clutter
        if i < n - 10:
            continue

        name = sig = desc = None

        # ── Single-candle patterns ─────────────────────────────────────
        if body_ratio < 0.08:
            name = "Doji"
            sig  = "neutral"
            desc = "Unentschlossenheit im Markt – mögliche Trendwende"

        elif lower_w > 2.0 * body and upper_w < body * 0.5:
            if not prev_bull:
                name, sig = "Hammer", "bullish"
                desc = "Käufer drücken Kurs nach oben – Aufwärtswende möglich"
            else:
                name, sig = "Hängender Mann", "bearish"
                desc = "Warnsignal: Verkaufsdruck trotz Aufwärtstrend"

        elif upper_w > 2.0 * body and lower_w < body * 0.5:
            if prev_bull:
                name, sig = "Shooting Star", "bearish"
                desc = "Abstoßung von oben – Abwärtswende wahrscheinlich"
            else:
                name, sig = "Inv. Hammer", "bullish"
                desc = "Mögliche Kaufwelle nach Abwärtstrend"

        elif body_ratio > 0.85:
            if is_bull:
                name, sig = "Bull. Marubozu", "bullish"
                desc = "Starke Käufer – klarer Aufwärtstrend"
            else:
                name, sig = "Bear. Marubozu", "bearish"
                desc = "Starke Verkäufer – klarer Abwärtstrend"

        # ── Two-candle patterns ────────────────────────────────────────
        elif is_bull and not prev_bull and body > prev_body * 1.05:
            name, sig = "Bull. Engulfing", "bullish"
            desc = "Käufer übernehmen vollständig – starkes Umkehrsignal"

        elif not is_bull and prev_bull and body > prev_body * 1.05:
            name, sig = "Bear. Engulfing", "bearish"
            desc = "Verkäufer übernehmen vollständig – starkes Umkehrsignal"

        elif is_bull and not prev_bull and c["o"] < prev["c"] and c["c"] > (prev["o"] + prev["c"]) / 2:
            name, sig = "Piercing Line", "bullish"
            desc = "Bullisches Umkehrmuster: Kurs schließt über Mitte der Vorkerze"

        elif not is_bull and prev_bull and c["o"] > prev["c"] and c["c"] < (prev["o"] + prev["c"]) / 2:
            name, sig = "Dark Cloud Cover", "bearish"
            desc = "Bearisches Muster: Kurs schließt unter Mitte der Vorkerze"

        if name:
            patterns.append({"index": i, "name": name, "signal": sig, "desc": desc})

    # ── Three-candle patterns ──────────────────────────────────────────
    for i in range(max(2, n - 10), n):
        c0, c1, c2 = candles[i - 2], candles[i - 1], candles[i]
        b0 = abs(c0["c"] - c0["o"])
        b1 = abs(c1["c"] - c1["o"])
        b2 = abs(c2["c"] - c2["o"])

        # Morning Star: bearish → small → bullish closing above midpoint of c0
        if (c0["c"] < c0["o"] and b1 < b0 * 0.4
                and c2["c"] > c2["o"] and c2["c"] > (c0["o"] + c0["c"]) / 2):
            patterns.append({
                "index": i, "name": "Morning Star", "signal": "bullish",
                "desc": "Starkes 3-Kerzen-Umkehrsignal nach oben",
            })

        # Evening Star: bullish → small → bearish closing below midpoint of c0
        elif (c0["c"] > c0["o"] and b1 < b0 * 0.4
              and c2["c"] < c2["o"] and c2["c"] < (c0["o"] + c0["c"]) / 2):
            patterns.append({
                "index": i, "name": "Evening Star", "signal": "bearish",
                "desc": "Starkes 3-Kerzen-Umkehrsignal nach unten",
            })

        # Three White Soldiers: three consecutive rising bullish candles
        elif all(candles[i - k]["c"] > candles[i - k]["o"] for k in range(3)):
            if (c2["c"] > c1["c"] > c0["c"]
                    and c1["o"] > c0["o"] and c2["o"] > c1["o"]):
                patterns.append({
                    "index": i, "name": "3 Weiße Soldaten", "signal": "bullish",
                    "desc": "Drei starke Aufwärtskerzen – anhaltender Trend",
                })

        # Three Black Crows: three consecutive falling bearish candles
        elif all(candles[i - k]["c"] < candles[i - k]["o"] for k in range(3)):
            if (c2["c"] < c1["c"] < c0["c"]
                    and c1["o"] < c0["o"] and c2["o"] < c1["o"]):
                patterns.append({
                    "index": i, "name": "3 Schwarze Raben", "signal": "bearish",
                    "desc": "Drei starke Abwärtskerzen – anhaltender Abwärtstrend",
                })

    # Deduplicate by index (keep last match per candle)
    seen = {}
    for p in patterns:
        seen[p["index"]] = p
    return list(seen.values())


# ── Trend Prediction ───────────────────────────────────────────────────────────
def predict_trend(candles: list, patterns: list, rsi: float, sma5: float, sma20: float) -> dict:
    if not candles:
        return {"direction": "neutral", "confidence": 50, "desc": "Keine Daten"}

    score = 0
    max_score = 0

    # Pattern signals – recent patterns weighted more
    for p in patterns:
        age_weight = 3 if p["index"] >= len(candles) - 3 else 2
        max_score += age_weight
        if p["signal"] == "bullish":
            score += age_weight
        elif p["signal"] == "bearish":
            score -= age_weight

    # RSI
    max_score += 2
    if   rsi < 30: score += 2
    elif rsi < 45: score += 1
    elif rsi > 70: score -= 2
    elif rsi > 60: score -= 1

    # SMA crossover (price vs SMA20)
    last_close = candles[-1]["c"]
    max_score += 2
    if   sma5 > sma20 * 1.001: score += 2
    elif sma5 < sma20 * 0.999: score -= 2
    else:
        score += 1 if last_close > sma20 else -1

    # Recent candle momentum (last 5 candles)
    recent = candles[-5:]
    bull_count = sum(1 for c in recent if c["c"] >= c["o"])
    max_score += 2
    if   bull_count >= 4: score += 2
    elif bull_count == 3: score += 1
    elif bull_count == 1: score -= 2
    elif bull_count == 0: score -= 2
    else:                 score -= 1

    # Volume trend (rising volume with price move = stronger signal)
    vols = [c["v"] for c in candles[-5:] if c["v"] > 0]
    if len(vols) >= 3:
        vol_trend = vols[-1] > sum(vols[:-1]) / len(vols[:-1])
        max_score += 1
        score += 1 if (vol_trend and bull_count >= 3) else -1 if (vol_trend and bull_count <= 1) else 0

    # Normalise to 0-100
    confidence = int(50 + (score / max(max_score, 1)) * 50)
    confidence = max(10, min(92, confidence))

    if confidence >= 63:
        direction = "bullish"
        desc = f"Aufwärtstrend wahrscheinlich – {confidence}% Konfidenz"
    elif confidence <= 37:
        direction = "bearish"
        desc = f"Abwärtstrend wahrscheinlich – {100 - confidence}% Konfidenz"
    else:
        direction = "neutral"
        desc = f"Seitwärtsbewegung erwartet – unklares Signal"

    return {"direction": direction, "confidence": confidence, "desc": desc}


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/candles/<market>/<path:symbol>")
def api_candles(market, symbol):
    if market == "crypto":
        candles = fetch_ohlcv_crypto(symbol)
    else:
        candles = fetch_ohlcv_yahoo(symbol)

    if not candles:
        return jsonify({"error": "No OHLCV data"}), 404

    # Use last 35 candles for display (we show 30 + some context for patterns)
    display = candles[-35:]
    patterns = detect_patterns(display)

    closes = [c["c"] for c in display]
    rec    = calc_recommendation(closes, closes[-1] if closes else 0)

    # SMA20 for overlay
    sma20_line = [
        round(sum(closes[max(0, i - 19): i + 1]) / min(20, i + 1), 6)
        for i in range(len(closes))
    ]

    prediction = predict_trend(
        display, patterns,
        rec.get("rsi", 50), rec.get("sma5", closes[-1]), rec.get("sma20", closes[-1])
    )

    return jsonify({
        "candles":    display,
        "patterns":   patterns,
        "prediction": prediction,
        "sma20":      sma20_line,
    })


@app.route("/api/tops/<market>")
def api_tops(market):
    if market == "crypto":
        return jsonify(coingecko_tops())
    syms = MARKET_SYMBOLS.get(market)
    if not syms:
        return jsonify({"error": "Unbekannter Markt"}), 400
    results = [q for s in syms if (q := yahoo_quote(s))]
    return jsonify(results)


@app.route("/api/detail/<market>/<path:symbol>")
def api_detail(market, symbol):
    if market == "crypto":
        data = coingecko_detail(symbol)
    else:
        data = yahoo_quote(symbol)
    if not data:
        return jsonify({"error": "Symbol nicht gefunden"}), 404
    data["news"] = fetch_news(data.get("name") or symbol)
    return jsonify(data)


@app.route("/api/search")
def api_search():
    q      = request.args.get("q", "").strip()
    market = request.args.get("type", "stocks")
    if not q:
        return jsonify([])

    if market == "crypto":
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/search",
                headers=HEADERS, params={"query": q}, timeout=8
            )
            return jsonify([
                {
                    "symbol": c["symbol"].upper(),
                    "name":   c["name"],
                    "id":     c["id"],
                    "thumb":  c.get("thumb", ""),
                    "type":   "crypto",
                }
                for c in r.json().get("coins", [])[:8]
            ])
        except Exception:
            return jsonify([])
    else:
        try:
            r = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                headers=HEADERS,
                params={"q": q, "quotesCount": 8},
                timeout=8,
            )
            return jsonify([
                {
                    "symbol":   qt.get("symbol", ""),
                    "name":     qt.get("shortname") or qt.get("longname", ""),
                    "exchange": qt.get("exchDisp", ""),
                    "type":     qt.get("quoteType", "").lower(),
                }
                for qt in r.json().get("quotes", [])[:8]
                if qt.get("symbol")
            ])
        except Exception:
            return jsonify([])


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
