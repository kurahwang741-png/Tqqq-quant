"""Automatic daily-close-proxy forward paper simulation. No broker orders."""
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yfinance as yf
from alpha_v1_frozen import simulate_soxl_reverse

START = pd.Timestamp("2026-09-30")
END = pd.Timestamp("2027-09-30")
INITIAL = 10000.0
OUT = Path(__file__).parent / "forward_data"
OUT.mkdir(exist_ok=True)
SYMBOLS = ["SOXL", "QQQ", "SMH"]

def fetch():
    # Require all three symbols on exactly matching completed NYSE trading dates.
    raw = yf.download(SYMBOLS, start="2024-01-01", auto_adjust=True,
                      group_by="column", progress=False, threads=False)
    if raw is None or raw.empty:
        raise RuntimeError("Yahoo daily data unavailable")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    close = close[SYMBOLS].copy().dropna()
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    # Never use today's bar even if Yahoo returns a partial one.
    close = close[close.index.date < now_et.date()]
    if len(close) < 252:
        raise RuntimeError("Insufficient history for indicators")
    last = close.index[-1]
    # Reject stale data if more than 1 business day is missing; NYSE holidays
    # may require an extra business day and should be checked manually.
    age_business = len(pd.bdate_range(last + pd.Timedelta(days=1),
                                     pd.Timestamp(now_et.date()) - pd.Timedelta(days=1)))
    if age_business > 1:
        raise RuntimeError(f"Stale data: last complete bar {last.date()}")
    return close.rename(columns={"SOXL":"TRADE"})

def main():
    df = fetch()
    df = df[df.index <= END]
    if df.index[-1] < START:
        print("Waiting for first forward-test bar")
        return
    # Current uploaded app's engine fills on CLOSE as a proxy. Its orders are
    # NOT true LOC executions. Results must be labelled accordingly.
    sim = simulate_soxl_reverse(df, initial_cash=INITIAL, base_unit=.06,
        take_profit=.05, max_hold_days=180, fee_pct=0, slippage_pct=0,
        fx_cost_pct=0, deployment_ratio=1, trade_start=START)
    eq = sim["equity"].loc[START:END].copy()
    if eq.empty: return
    eq["StrategyReturn"] = eq["Equity"] / INITIAL - 1
    eq["StrategyDrawdown"] = eq["Equity"] / eq["Equity"].cummax() - 1
    bench = df.loc[START:eq.index[-1],"TRADE"]
    if not bench.empty:
        eq["BuyHoldEquity"] = (bench / bench.iloc[0] * INITIAL).reindex(eq.index)
    eq.to_csv(OUT / "equity.csv", index_label="Date", float_format="%.8f")
    trades = sim["trades"]
    trades.to_csv(OUT / "closed_trades.csv", index=False, encoding="utf-8-sig")
    last = eq.iloc[-1]
    state = dict(strategy="C-ALPHA V1 (frozen 2026-10-09)",
        execution_model="DAILY_CLOSE_PROXY_NOT_TRUE_LOC",
        start=str(START.date()), end=str(END.date()), initial_cash=INITIAL,
        last_bar=str(eq.index[-1].date()), updated_at=datetime.now(timezone.utc).isoformat(),
        equity=float(last["Equity"]), cash=float(last["Cash"]),
        shares=float(last["Shares"]), exposure=float(last["Exposure"]),
        return_pct=float(last["StrategyReturn"]*100),
        mdd_pct=float(eq["StrategyDrawdown"].min()*100),
        buy_hold_return_pct=float((last.get("BuyHoldEquity",INITIAL)/INITIAL-1)*100),
        closed_trades=len(trades))
    (OUT / "summary.json").write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Updated:",state["last_bar"],"equity:",state["equity"])

if __name__ == "__main__": main()
