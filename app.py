"""
BacktestLab - EDUCATIONAL prompt-to-backtest platform (crypto + Indian index/stocks)
Run:  pip install flask pandas numpy requests yfinance   ->   python app.py   ->  http://127.0.0.1:5000
Not investment advice. Not a SEBI-registered Investment Adviser / Research Analyst.
"""
import os, re, json, math, difflib
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE, "static"))

# ----------------------------------------------------------------------------- universe
CRYPTO = {"BTCUSDT": ["btc", "bitcoin"], "ETHUSDT": ["eth", "ethereum"], "SOLUSDT": ["sol", "solana"],
          "XRPUSDT": ["xrp", "ripple"], "BNBUSDT": ["bnb"], "DOGEUSDT": ["doge", "dogecoin"],
          "LINKUSDT": ["link", "chainlink"]}
INDIA = {"^NSEI": ["nifty", "nifty50", "nifty 50"], "^NSEBANK": ["banknifty", "bank nifty"], "^BSESN": ["sensex"],
         "RELIANCE.NS": ["reliance"], "TCS.NS": ["tcs"], "INFY.NS": ["infosys", "infy"],
         "HDFCBANK.NS": ["hdfcbank", "hdfc bank"], "SBIN.NS": ["sbin"], "ICICIBANK.NS": ["icicibank", "icici bank"],
         "VEDL.NS": ["vedl", "vedanta"], "TRENT.NS": ["trent"], "COALINDIA.NS": ["coalindia", "coal india"]}
DHAN_IDS = {"^NSEI": ("13", "IDX_I", "INDEX"), "^NSEBANK": ("25", "IDX_I", "INDEX"), "^BSESN": ("51", "IDX_I", "INDEX")}
BPD = {"crypto": {"5m": 288, "15m": 96, "1h": 24, "1d": 1}, "india": {"5m": 75, "15m": 25, "1h": 7, "1d": 1}}
DEFAULT_DAYS = {"1d": 1095, "1h": 365, "15m": 90, "5m": 30}

# ----------------------------------------------------------------------------- data loaders
def _clean(df):
    df = df[["open", "high", "low", "close", "volume"]].astype(float).dropna()
    return df[~df.index.duplicated()].sort_index()

def fetch_binance(symbol, interval, days):
    import requests
    end = int(datetime.now(timezone.utc).timestamp() * 1000); start = end - days * 86400000
    err, rows = "no data", []
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            rows, cur = [], start
            while cur < end and len(rows) < 300000:
                r = requests.get(host + "/api/v3/klines", timeout=20,
                                 params=dict(symbol=symbol, interval=interval, startTime=cur, limit=1000))
                r.raise_for_status(); k = r.json()
                if not k: break
                rows += k; cur = k[-1][0] + 1
            if rows: break
        except Exception as e:
            err = str(e)
    if not rows: raise RuntimeError("Binance fetch failed: " + err)
    df = pd.DataFrame(rows).iloc[:, :6]; df.columns = ["t", "open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.pop("t"), unit="ms")
    return _clean(df), []

def fetch_yf(symbol, interval, days):
    import yfinance as yf
    lim = {"5m": 59, "15m": 59, "1h": 729, "1d": 36500}[interval]; notes = []
    if days > lim: notes.append(f"yfinance gives only {lim} days of {interval} data; period shortened."); days = lim
    d = yf.download(symbol, start=(datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
                    interval=interval, auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)
    if d.empty: raise RuntimeError("yfinance returned no data")
    d.index = pd.to_datetime(d.index).tz_localize(None)
    return _clean(d), notes

def fetch_dhan(symbol, interval, days):  # NOTE: untested here - verify against Dhan docs
    import requests
    tok, cid = os.getenv("DHAN_ACCESS_TOKEN"), os.getenv("DHAN_CLIENT_ID", "")
    if not tok or symbol not in DHAN_IDS:
        raise RuntimeError("Set env DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID (never hard-code them). Supported: NIFTY, BANKNIFTY, SENSEX.")
    sid, seg, ins = DHAN_IDS[symbol]; notes = []
    h = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    if interval != "1d" and days > 85: days = 85; notes.append("Dhan intraday limited to ~90 days per call.")
    frm, to = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d")
    if interval == "1d":
        url = "https://api.dhan.co/v2/charts/historical"
        body = dict(securityId=sid, exchangeSegment=seg, instrument=ins, expiryCode=0, fromDate=frm, toDate=to)
    else:
        url = "https://api.dhan.co/v2/charts/intraday"
        body = dict(securityId=sid, exchangeSegment=seg, instrument=ins, interval={"5m": "5", "15m": "15", "1h": "60"}[interval],
                    fromDate=frm + " 09:15:00", toDate=to + " 15:30:00")
    r = requests.post(url, headers=h, json=body, timeout=30); r.raise_for_status(); j = r.json()
    df = pd.DataFrame({k: j[k] for k in ("open", "high", "low", "close", "volume", "timestamp")})
    df.index = pd.to_datetime(df.pop("timestamp"), unit="s") + pd.Timedelta(hours=5, minutes=30)
    return _clean(df), notes

def demo_data(market, interval, days):
    bpd = BPD[market][interval]; n = int(days * bpd); rng = np.random.default_rng(42)
    vol = 0.02 / math.sqrt(bpd); r = rng.normal(0.0001 / bpd, vol, n)
    c = 100 * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]; sp = np.abs(rng.normal(0, vol / 1.5, n))
    idx = pd.date_range(end=datetime.now().replace(second=0, microsecond=0), periods=n, freq=pd.Timedelta(seconds=86400 / bpd))
    return pd.DataFrame(dict(open=o, high=np.maximum(o, c) * (1 + sp), low=np.minimum(o, c) * (1 - sp), close=c,
                             volume=rng.uniform(1e3, 1e4, n)), index=idx), ["SYNTHETIC random data - results are meaningless."]

def csv_data(text):
    import io
    df = pd.read_csv(io.StringIO(text)); df.columns = [str(c).strip().lower() for c in df.columns]
    tc = next((c for c in ("time", "date", "datetime", "timestamp") if c in df.columns), None)
    if tc is None or not {"open", "high", "low", "close"} <= set(df.columns):
        raise RuntimeError("CSV needs columns: time/date, open, high, low, close (volume optional)")
    if "volume" not in df.columns: df["volume"] = 0
    num = pd.to_numeric(df[tc], errors="coerce")
    df.index = pd.to_datetime(num, unit="ms" if num.dropna().median() > 1e11 else "s") if num.notna().all() else pd.to_datetime(df[tc])
    return _clean(df), []

def load_data(p):
    src, mk, sym, iv, days = p["source"], p["market"], p["symbol"], p["interval"], int(p["days"])
    if src == "demo": return demo_data(mk, iv, days)
    if src == "csv": return csv_data(p.get("csv", ""))
    if src == "binance": return fetch_binance(sym, iv, days)
    if src == "yfinance": return fetch_yf(sym, iv, days)
    if src == "dhan": return fetch_dhan(sym, iv, days)
    raise RuntimeError("Unknown source")

# ----------------------------------------------------------------------------- indicators / rules
def ind(df, tok):
    try: return float(tok)
    except (TypeError, ValueError): pass
    m = re.fullmatch(r"(\w+)(?:\((\d+)\))?", str(tok).strip().lower())
    if not m: raise ValueError(f"Bad indicator '{tok}'")
    name, n = m.group(1), int(m.group(2) or 0); c = df["close"]
    if name in ("open", "high", "low", "close", "volume"): return df[name]
    if n < 1 or n > 1000: raise ValueError(f"Bad period in '{tok}'")
    if name == "sma": return c.rolling(n).mean()
    if name == "ema": return c.ewm(span=n, adjust=False).mean()
    if name == "rsi":
        d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean(); out = 100 - 100 / (1 + up / dn); out[dn == 0] = 100; return out
    if name == "bb_upper": return c.rolling(n).mean() + 2 * c.rolling(n).std()
    if name == "bb_lower": return c.rolling(n).mean() - 2 * c.rolling(n).std()
    if name == "highest": return df["high"].rolling(n).max().shift(1)   # shift(1): no look-ahead
    if name == "lowest": return df["low"].rolling(n).min().shift(1)
    if name == "zscore": return (c - c.rolling(n).mean()) / c.rolling(n).std()
    raise ValueError(f"Unknown indicator '{name}'")

def cond(df, c):
    L, R = ind(df, c["left"]), ind(df, c["right"]); op = c["op"]
    if not isinstance(L, pd.Series): raise ValueError("Left side must be an indicator")
    if not isinstance(R, pd.Series): R = pd.Series(R, index=df.index)
    if op == ">": out = L > R
    elif op == "<": out = L < R
    elif op == "crosses_above": out = (L > R) & (L.shift(1) <= R.shift(1))
    elif op == "crosses_below": out = (L < R) & (L.shift(1) >= R.shift(1))
    else: raise ValueError(f"Bad operator '{op}'")
    return out.fillna(False)

def describe(c):
    o = {"crosses_above": "crosses above", "crosses_below": "crosses below", ">": "is above", "<": "is below"}[c["op"]]
    return f"{c['left']} {o} {c['right']}"

# ----------------------------------------------------------------------------- engine
def run_engine(df, spec, capital, fee, slip):
    n = len(df); o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ent = np.ones(n, bool)
    for x in spec["entry"]: ent &= cond(df, x).to_numpy()
    ex = np.zeros(n, bool)
    for x in spec.get("exit", []): ex |= cond(df, x).to_numpy()
    side = 1 if spec.get("side", "long") == "long" else -1
    sl, tp, mb = spec.get("stop_pct"), spec.get("take_pct"), spec.get("max_bars")
    f, s = fee / 100, slip / 100
    eq = capital; fees = 0.0; pos = 0; qty = epx = efee = 0.0; stop = tgt = None; ei = 0
    curve = np.full(n, float(capital)); trades = []

    def book(i, xp, why):
        nonlocal eq, fees, pos
        xp = xp * (1 - pos * s); pnl = pos * (xp - epx) * qty; fx = xp * qty * f
        eq += pnl - fx; fees += fx
        trades.append(dict(entry_time=str(df.index[ei]), exit_time=str(df.index[i]), side="LONG" if pos == 1 else "SHORT",
                           entry=round(epx, 4), exit=round(xp, 4), pnl=round(pnl - fx - efee, 2),
                           ret_pct=round((pnl - fx - efee) / (epx * qty) * 100, 3), bars=i - ei, reason=why))
        pos = 0

    for i in range(1, n):
        fresh = False
        if pos == 0 and ent[i - 1]:                      # signal on prior close -> fill at this open
            epx = o[i] * (1 + side * s); qty = eq / epx; efee = eq * f; eq -= efee; fees += efee
            pos, ei, fresh = side, i, True
            stop = epx * (1 - side * sl / 100) if sl else None
            tgt = epx * (1 + side * tp / 100) if tp else None
        if pos:
            xp = why = None
            if not fresh and ex[i - 1]: xp, why = o[i], "signal"
            elif stop is not None and ((pos == 1 and l[i] <= stop) or (pos == -1 and h[i] >= stop)):
                xp, why = (min(o[i], stop) if pos == 1 else max(o[i], stop)), "stop"   # stop checked first = conservative
            elif tgt is not None and ((pos == 1 and h[i] >= tgt) or (pos == -1 and l[i] <= tgt)): xp, why = tgt, "target"
            elif mb and i - ei >= mb: xp, why = c[i], "time"
            if xp is not None: book(i, xp, why)
        curve[i] = eq + (pos * (c[i] - epx) * qty if pos else 0)
    if pos: book(n - 1, c[-1], "end"); curve[-1] = eq
    return curve, trades, fees

def summarize(curve, trades, capital):
    pn = np.array([t["pnl"] for t in trades], float)
    w, ls = pn[pn > 0], pn[pn < 0]
    pf = float(w.sum() / abs(ls.sum())) if ls.size and ls.sum() < 0 else None
    streak = best = 0
    for v in pn:
        streak = streak + 1 if v < 0 else 0; best = max(best, streak)
    return dict(trades=int(len(pn)), net=float(curve[-1] - capital), ret=float((curve[-1] / capital - 1) * 100),
                win=float((pn > 0).mean() * 100) if len(pn) else 0.0, pf=pf,
                exp=float(pn.mean()) if len(pn) else 0.0, avg_win=float(w.mean()) if w.size else 0.0,
                avg_loss=float(ls.mean()) if ls.size else 0.0, max_loss_streak=int(best))

def full_metrics(df, curve, trades, capital, fees, ppy):
    m = summarize(curve, trades, capital); eqs = pd.Series(curve, index=df.index)
    dd = eqs - eqs.cummax(); m["max_dd"] = float(dd.min()); m["max_dd_pct"] = float((dd / eqs.cummax()).min() * 100)
    daily = eqs.resample("1D").last().dropna().pct_change().dropna()
    m["sharpe"] = float(daily.mean() / daily.std() * math.sqrt(ppy)) if len(daily) > 5 and daily.std() > 0 else None
    m["buy_hold"] = float((df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100); m["fees"] = float(fees)
    gross = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    m["fee_share"] = float(fees / gross * 100) if gross > 0 else None
    pn = sorted((t["pnl"] for t in trades), reverse=True)
    m["top_trade_share"] = float(pn[0] / m["net"] * 100) if pn and m["net"] > 0 and pn[0] > 0 else None
    return m

def reality_check(m, iso, oos, notes, demo):
    r = []
    add = lambda lvl, t: r.append(dict(level=lvl, text=t))
    if demo: add("bad", "Synthetic data: nothing here says anything about real markets.")
    if m["trades"] < 30: add("bad", f"Only {m['trades']} trades. Fewer than ~30 trades is too small to trust any win rate or profit factor.")
    if m["trades"] >= 5 and m["win"] > 80: add("warn", f"Win rate {m['win']:.0f}% looks too good. Check for look-ahead, tiny samples or missing costs.")
    if m["pf"] and m["pf"] > 3: add("warn", f"Profit factor {m['pf']:.1f} is unusually high; real strategies rarely sustain this.")
    if m["ret"] < m["buy_hold"]: add("info", f"Strategy return ({m['ret']:.1f}%) is below simple buy & hold ({m['buy_hold']:.1f}%).")
    if m["fee_share"] and m["fee_share"] > 30: add("warn", f"Costs eat {m['fee_share']:.0f}% of gross profit. Small edges vanish after fees/slippage.")
    if m["top_trade_share"] and m["top_trade_share"] > 40: add("warn", f"One trade makes {m['top_trade_share']:.0f}% of net profit - result depends on luck, not repeatability.")
    if m["max_dd_pct"] < -30: add("warn", f"Max drawdown {m['max_dd_pct']:.0f}%: most people abandon a strategy long before this.")
    if m["max_loss_streak"] >= 6: add("info", f"Longest losing streak: {m['max_loss_streak']} trades in a row.")
    if iso and oos and iso["trades"] >= 5:
        if oos["trades"] < 5: add("info", "Out-of-sample period has too few trades to judge.")
        elif (iso["ret"] > 0 and oos["ret"] <= 0) or (iso["pf"] and oos["pf"] and oos["pf"] < iso["pf"] * 0.5):
            add("bad", "Out-of-sample (last 30%) is much worse than in-sample (first 70%): classic overfitting sign.")
    for n in notes: add("info", n)
    add("info", "Entries fill at the NEXT bar open after a signal (no look-ahead). If stop and target are both touched in one bar, the stop is assumed hit first.")
    add("info", "Past performance does not predict future results. Educational use only.")
    return r

def num(x, lo, hi, d):
    try: x = float(x)
    except (TypeError, ValueError): return d
    return min(max(x, lo), hi)

def check_spec(s):
    if not isinstance(s, dict) or not s.get("entry"): raise ValueError("Strategy has no entry rule.")
    out = dict(name=str(s.get("name", "Custom"))[:80], side="short" if s.get("side") == "short" else "long",
               entry=[], exit=[], stop_pct=None, take_pct=None, max_bars=None)
    for key in ("entry", "exit"):
        for c in s.get(key, [])[:6]:
            if c.get("op") not in (">", "<", "crosses_above", "crosses_below"): raise ValueError("Bad operator")
            out[key].append(dict(left=str(c["left"]), op=c["op"], right=str(c["right"])))
    if s.get("stop_pct"): out["stop_pct"] = num(s["stop_pct"], 0.05, 50, None)
    if s.get("take_pct"): out["take_pct"] = num(s["take_pct"], 0.05, 500, None)
    if s.get("max_bars"): out["max_bars"] = int(num(s["max_bars"], 1, 100000, 0)) or None
    return out

# ----------------------------------------------------------------------------- prompt parser (rule based)
VOCAB = ["ema", "sma", "rsi", "bollinger", "breakout", "crossover", "stoploss", "target", "bearish", "bullish", "nifty",
         "banknifty", "sensex", "bitcoin", "ethereum", "solana", "below", "above", "under", "over", "daily", "hourly",
         "minute", "strategy", "backtest", "reversion", "momentum", "golden", "death", "zscore", "reliance", "infosys",
         "average", "moving", "profit"]
KNOWN = set("when enter exit price close high low stop loss take buy sell long short with than days the and for candle bars "
            "last hour week month year chart time cross crosses crossed then rule using trade trades market value above".split())

def parse_prompt(text):
    t = text.lower().strip(); corr = []
    def fix(m):
        w = m.group(0)
        if w in VOCAB or w in KNOWN: return w
        c = difflib.get_close_matches(w, VOCAB, n=1, cutoff=0.8)
        if c: corr.append(f"'{w}' -> '{c[0]}'"); return c[0]
        return w
    t = re.sub(r"[a-z]{4,}", fix, t)
    asm, warn = [], []
    # instrument
    hits = []
    for table, mk in ((CRYPTO, "crypto"), (INDIA, "india")):
        for sym, al in table.items():
            for a in al:
                if re.search(r"\b" + re.escape(a) + r"\b", t): hits.append((len(a), mk, sym))
    if hits: _, market, symbol = max(hits)
    else: market, symbol = "india", "^NSEI"; asm.append("No instrument found: assumed NIFTY 50.")
    # unsupported
    if re.search(r"option|spread|condor|straddle|strangle", t):
        warn.append("Options strategies need option-chain/Greeks data. v1 backtests the UNDERLYING only, so your view is mapped to a directional trade on it as a proxy. Real option P&L (theta, IV, bid-ask) will differ a lot.")
    # side
    explicit = bool(re.search(r"bearish|\bshort\b|\bsell\b|selling|downtrend|\blong\b|\bbuy\b|bullish|uptrend", t))
    side = "short" if re.search(r"bearish|\bshort\b|\bsell\b|selling|downtrend", t) and not re.search(r"\bbuy\b|bullish", t) else "long"
    entry, exit_ = [], []
    C = lambda l, o, r: dict(left=l, op=o, right=str(r))
    # moving-average crossover pair
    m = re.search(r"(ema|sma)\s*(\d+)\s*(?:/|and|&|-|x)\s*(\d+)", t) or None
    pair = None
    if m: pair = (m.group(1), int(m.group(2)), int(m.group(3)))
    else:
        m = re.search(r"(\d+)\s*(?:/|and|&|-|x)\s*(\d+)\s*(ema|sma)", t)
        if m: pair = (m.group(3), int(m.group(1)), int(m.group(2)))
        else:  # "20 ema crosses above 50 ema"
            mm = re.findall(r"(\d+)[ -]?(?:day |period |bar )?(ema|sma)", t)
            if len(mm) >= 2 and mm[0][1] == mm[1][1] and mm[0][0] != mm[1][0]:
                pair = (mm[0][1], int(mm[0][0]), int(mm[1][0]))
                if re.search(r"below|under", t) and not re.search(r"above|over", t): side = "short"
    if re.search(r"golden cross", t): pair = ("sma", 50, 200); side = "long"
    if re.search(r"death cross", t): pair = ("sma", 50, 200); side = "short"
    single = None
    if pair:
        k, a, b = pair; f, sl_ = f"{k}({min(a, b)})", f"{k}({max(a, b)})"
        up, dn = ("crosses_above", "crosses_below") if side == "long" else ("crosses_below", "crosses_above")
        entry.append(C(f, up, sl_)); exit_.append(C(f, dn, sl_))
    else:
        m = re.search(r"(\d+)[ -]?(?:day |period |bar )?(ema|sma)", t) or re.search(r"(ema|sma)\s*(\d+)", t)
        if m:
            g = m.groups(); k, n_ = (g[1], g[0]) if g[1] in ("ema", "sma") else (g[0], g[1])
            ma = f"{k}({n_})"; word = "above" if re.search(r"above|over|breaks? up", t) else "below" if re.search(r"below|under|breaks? down", t) else None
            if word is None: word = "above" if side == "long" else "below"; asm.append(f"Direction vs {ma} not stated: assumed price crosses {word} it.")
            if word == "above": entry.append(C("close", "crosses_above", ma)); exit_.append(C("close", "crosses_below", ma)); side = side if explicit else "long"
            else: entry.append(C("close", "crosses_below", ma)); exit_.append(C("close", "crosses_above", ma)); side = "short" if not explicit or side == "short" else side
    m = re.search(r"rsi(?:\s*\(?(\d+)\)?)?\s*(?:is\s*)?(below|under|<|above|over|>)\s*(\d+)", t)
    if m:
        n_ = m.group(1) or 14; low = m.group(2) in ("below", "under", "<"); lvl = m.group(3)
        if not explicit: side = "long" if low else "short"; asm.append(f"RSI mean-reversion assumed: {'buy' if low else 'sell short'} when RSI {'<' if low else '>'} {lvl}.")
        entry.append(C(f"rsi({n_})", "<" if low else ">", lvl)); exit_.append(C(f"rsi({n_})", ">" if low else "<", 50))
        asm.append("RSI exit rule not given: exit when RSI crosses 50.")
    if re.search(r"bollinger|\bbb\b", t):
        up_ = re.search(r"upper", t) is not None
        if not explicit: side = "short" if up_ else "long"
        if up_: entry.append(C("close", ">", "bb_upper(20)")); exit_.append(C("close", "<", "sma(20)"))
        else: entry.append(C("close", "<", "bb_lower(20)")); exit_.append(C("close", ">", "sma(20)"))
        asm.append("Bollinger (20, 2 std) mean-reversion; exit at the 20-bar average.")
    if re.search(r"breakout|\d+[- ]?(?:day|bar|candle|period)?\s*high", t):
        m = re.search(r"(\d+)[- ]?(?:day|bar|candle|period)?\s*(?:high|low|breakout)", t); n_ = int(m.group(1)) if m else 20
        if side == "long": entry.append(C("close", ">", f"highest({n_})")); exit_.append(C("close", "<", f"lowest({max(n_ // 2, 2)})"))
        else: entry.append(C("close", "<", f"lowest({n_})")); exit_.append(C("close", ">", f"highest({max(n_ // 2, 2)})"))
        asm.append(f"Donchian breakout over {n_} bars; exit on {max(n_ // 2, 2)}-bar opposite extreme.")
    m = re.search(r"z-?score\s*(?:is\s*)?(below|under|<|above|over|>)\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        low = m.group(1) in ("below", "under", "<"); x = abs(float(m.group(2)))
        if not explicit: side = "long" if low else "short"
        entry.append(C("zscore(20)", "<" if low else ">", -x if low else x)); exit_.append(C("zscore(20)", ">" if low else "<", 0))
        asm.append("Z-score uses a 20-bar window; exit when it returns to 0.")
    sl = tp = None
    m = re.search(r"(?:stop ?loss|\bsl\b)\s*(?:of|at)?\s*(\d+(?:\.\d+)?)\s*%", t); sl = float(m.group(1)) if m else None
    m = re.search(r"(?:target|\btp\b|take profit)\s*(?:of|at)?\s*(\d+(?:\.\d+)?)\s*%", t); tp = float(m.group(1)) if m else None
    spec = None
    if not entry:
        warn.append("I could not find an entry rule. Try: 'Buy NIFTY when 20 EMA crosses above 50 EMA, stop loss 2%, target 4%' or 'BTC RSI below 30 on 1h'.")
    else:
        if not exit_ and not sl: sl, tp = 2.0, 4.0; asm.append("No exit given: added 2% stop and 4% target as a safe default.")
        spec = dict(name="From prompt", side=side, entry=entry, exit=exit_, stop_pct=sl, take_pct=tp)
    # interval / days
    iv = None; m = re.search(r"(\d+)\s*(?:min|minute|m)\b", t)
    if m:
        v = int(m.group(1)); near = min((5, 15, 60), key=lambda x: abs(x - v)); iv = {5: "5m", 15: "15m", 60: "1h"}[near]
        if near != v: corr.append(f"{v}m not supported -> {iv}")
    elif re.search(r"\b1\s*h\b|hourly|\bhour\b", t): iv = "1h"
    elif re.search(r"\bdaily\b|\b1d\b", t): iv = "1d"
    if iv is None: iv = "1d"; asm.append("Timeframe not stated: assumed daily bars.")
    days = DEFAULT_DAYS[iv]; m = re.search(r"(?:last|past)\s*(\d+)\s*(day|week|month|year)", t)
    if m: days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)]
    if len(entry) > 2: warn.append("More than 2 entry conditions: the more rules you add, the easier it is to overfit.")
    desc = ""
    if spec:
        desc = (f"{spec['side'].upper()} {symbol} on {iv} bars for the last {days} days. Enter when " + " AND ".join(describe(c) for c in entry)
                + ("; exit when " + " OR ".join(describe(c) for c in exit_) if exit_ else "")
                + (f"; stop loss {sl}%" if sl else "") + (f"; target {tp}%" if tp else "")
                + ". Fees 0.05% + slippage 0.05% per side. Entries fill at next bar open.")
    return dict(clean_prompt=desc, corrections=corr, assumptions=asm, warnings=warn, spec=spec, market=market,
                symbol=symbol, interval=iv, days=days)

# ----------------------------------------------------------------------------- strategy library
def S(name, side, entry, exit_, sl=None, tp=None): return dict(name=name, side=side, entry=entry, exit=exit_, stop_pct=sl, take_pct=tp)
C_ = lambda l, o, r: dict(left=l, op=o, right=str(r))
LIBRARY = [
    dict(id="ema_cross", name="EMA 20/50 Trend Crossover", tags=["trend"], desc="Long when fast EMA crosses above slow EMA; exit on the reverse cross.",
         interval="1d", spec=S("EMA 20/50", "long", [C_("ema(20)", "crosses_above", "ema(50)")], [C_("ema(20)", "crosses_below", "ema(50)")], 3)),
    dict(id="golden", name="Golden Cross 50/200", tags=["trend", "slow"], desc="Classic 50/200 SMA cross. Very few trades, so statistically weak.",
         interval="1d", spec=S("Golden Cross", "long", [C_("sma(50)", "crosses_above", "sma(200)")], [C_("sma(50)", "crosses_below", "sma(200)")])),
    dict(id="rsi_bounce", name="RSI(14) Oversold Bounce", tags=["mean-reversion"], desc="Buy when RSI < 30, exit when RSI > 50, 3% stop.",
         interval="1h", spec=S("RSI Bounce", "long", [C_("rsi(14)", "<", 30)], [C_("rsi(14)", ">", 50)], 3)),
    dict(id="bb_rev", name="Bollinger Mean Reversion", tags=["mean-reversion"], desc="Buy a close below the lower band; exit at the 20-bar average.",
         interval="1h", spec=S("Bollinger Reversion", "long", [C_("close", "<", "bb_lower(20)")], [C_("close", ">", "sma(20)")], 3)),
    dict(id="donchian", name="20-Bar Breakout (Donchian)", tags=["breakout", "trend"], desc="Buy a close above the 20-bar high; exit below the 10-bar low.",
         interval="1d", spec=S("Donchian 20", "long", [C_("close", ">", "highest(20)")], [C_("close", "<", "lowest(10)")])),
    dict(id="z_rev", name="Z-score Mean Reversion", tags=["mean-reversion"], desc="Buy when price is 2 std below its 20-bar mean; exit at the mean.",
         interval="1h", spec=S("Z-score", "long", [C_("zscore(20)", "<", -2)], [C_("zscore(20)", ">", 0)], 4)),
    dict(id="bear_ema", name="Bearish: Close below 20 EMA (short)", tags=["trend", "short"], desc="Short when price crosses below its 20 EMA; cover on cross back above.",
         interval="1d", spec=S("Bearish 20 EMA", "short", [C_("close", "crosses_below", "ema(20)")], [C_("close", "crosses_above", "ema(20)")], 2)),
]

DISCLAIMER = [
    ("Educational use only", "BacktestLab is a learning tool. It is not a SEBI-registered Investment Adviser or Research Analyst and does not give investment advice, tips, recommendations or solicitations to buy, sell or hold any security, derivative or crypto asset."),
    ("Backtests are hypothetical", "Results use historical data and simplifying assumptions. They do not predict future results. Real trading involves slippage, liquidity gaps, outages, taxes and emotions that a backtest cannot capture."),
    ("SEBI", "Trading in equity, index and F&O is risky; SEBI's own studies have found that a large majority of individual F&O traders make net losses. Only SEBI-registered intermediaries may offer investment advice or research recommendations. Verify at sebi.gov.in."),
    ("RBI / FEMA", "Residents' overseas transactions are governed by FEMA, 1999 and the Liberalised Remittance Scheme. Margin/forex trading on platforms not authorised by RBI is not permitted (see RBI's Alert List). Verify at rbi.org.in."),
    ("Crypto / VDAs", "Virtual digital assets are not legal tender and are not regulated like securities in India; they can lose all value. Gains are taxed under Indian law (currently a flat 30% plus 1% TDS - verify current rules). Exchanges can be hacked, delisted or blocked."),
    ("Your responsibility", "Do not trade real money on the basis of anything shown here. Consult a SEBI-registered adviser and a tax professional. Never share API keys or access tokens with any website, including this one."),
]
EXAMPLES = ["Buy NIFTY when 20 EMA crosses above 50 EMA, stop loss 2%, target 4%",
            "BTC RSI below 30 on 1h, exit at RSI 50",
            "Short BANKNIFTY when price closes below 20 EMA daily, sl 1.5%",
            "ETH 20-day breakout with 3% stop loss",
            "bactest sol bolinger lower band 1h"]

# ----------------------------------------------------------------------------- routes
@app.get("/")
def home(): return send_from_directory(app.static_folder, "index.html")

@app.get("/api/config")
def api_config():
    return jsonify(crypto=[dict(value=k, label=k.replace("USDT", "/USDT")) for k in CRYPTO],
                   india=[dict(value=k, label=v[0].upper()) for k, v in INDIA.items()],
                   library=LIBRARY, disclaimer=DISCLAIMER, examples=EXAMPLES)

@app.get("/api/failures")
def api_failures():
    try: return jsonify(json.load(open(os.path.join(BASE, "failures.json"), encoding="utf-8")))
    except Exception: return jsonify([])

@app.post("/api/parse")
def api_parse():
    return jsonify(parse_prompt(str((request.get_json(force=True) or {}).get("prompt", ""))[:600]))

@app.post("/api/backtest")
def api_backtest():
    p = request.get_json(force=True) or {}
    try:
        spec = check_spec(p.get("spec")); market = "crypto" if p.get("market") == "crypto" else "india"
        iv = p.get("interval", "1d"); iv = iv if iv in BPD[market] else "1d"
        p.update(market=market, interval=iv, days=int(num(p.get("days"), 5, 3650, 365)))
        capital, fee, slip = num(p.get("capital"), 1000, 1e9, 100000), num(p.get("fee"), 0, 2, 0.05), num(p.get("slip"), 0, 2, 0.05)
        df, notes = load_data(p)
        if len(df) < 60: raise RuntimeError(f"Only {len(df)} bars of data - need at least 60.")
        ppy = 365 if market == "crypto" else 252
        curve, trades, fees = run_engine(df, spec, capital, fee, slip)
        m = full_metrics(df, curve, trades, capital, fees, ppy)
        k = int(len(df) * 0.7); isr = osr = None
        if len(df) > 200:
            a, b = df.iloc[:k], df.iloc[k:]
            ca, ta, _ = run_engine(a, spec, capital, fee, slip); cb, tb, _ = run_engine(b, spec, capital, fee, slip)
            isr, osr = summarize(ca, ta, capital), summarize(cb, tb, capital)
        idx = np.linspace(0, len(df) - 1, min(len(df), 500)).astype(int)
        bench = (capital * df["close"] / df["open"].iloc[0]).to_numpy()
        return jsonify(ok=True, metrics=m, insample=isr, oos=osr, bars=len(df), start=str(df.index[0]), end=str(df.index[-1]),
                       curve=dict(t=[str(df.index[i]) for i in idx], strat=[round(float(curve[i]), 2) for i in idx],
                                  bench=[round(float(bench[i]), 2) for i in idx]),
                       trades=trades[-100:], notes=notes, spec=spec,
                       checks=reality_check(m, isr, osr, notes, p["source"] == "demo"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
