"""Frozen C-ALPHA logic extracted from user-supplied app on 2026-10-09. Do not edit for V1.
Daily-close proxy, NOT exchange-verified LOC fills.
"""
import numpy as np
import pandas as pd
soxl_track = "C-ALPHA"

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _soxl_c_features(soxl_series, qqq_series, smh_series):
    """C타입 시장/종목 상태 계산. 호출 시점까지의 확정 일봉만 사용합니다."""
    d = pd.concat(
        [
            pd.Series(soxl_series, name="SOXL").astype(float),
            pd.Series(qqq_series, name="QQQ").astype(float),
            pd.Series(smh_series, name="SMH").astype(float),
        ],
        axis=1,
    ).dropna()
    if d.empty:
        return None

    for p in (5, 10, 20, 50, 200):
        d[f"S_MA{p}"] = d["SOXL"].rolling(p, min_periods=max(3, p // 2)).mean()
        d[f"Q_MA{p}"] = d["QQQ"].rolling(p, min_periods=max(3, p // 2)).mean()

    d["S_RSI14"] = calc_rsi(d["SOXL"], 14)
    d["S_DIST10"] = d["SOXL"] / d["S_MA10"] - 1
    d["S_DIST20"] = d["SOXL"] / d["S_MA20"] - 1
    d["S_DIST10_D3"] = d["S_DIST10"].diff(3)
    d["S_RSI_D3"] = d["S_RSI14"].diff(3)
    d["Q_MA20_SLOPE5"] = d["Q_MA20"].pct_change(5)
    d["SMH_R5"] = d["SMH"].pct_change(5)
    d["QQQ_R5"] = d["QQQ"].pct_change(5)
    d["RS5"] = d["SMH_R5"] - d["QQQ_R5"]
    d["Q_DD252"] = d["QQQ"] / d["QQQ"].rolling(252, min_periods=50).max() - 1
    d["S_R3"] = d["SOXL"].pct_change(3)
    d["S_R5"] = d["SOXL"].pct_change(5)
    d["S_VOL20"] = d["SOXL"].pct_change().rolling(20, min_periods=10).std()
    return d

def get_soxl_loc_plan(close_series, exposure_now=0.0, base_unit=0.06, qqq_series=None, smh_series=None):
    """V32-C 계획.

    C타입 = SOXL 이평/RSI + LOT + QQQ 이평 구조 + SMH/QQQ 상대강도.
    VIX는 의도적으로 제외합니다. 주문일 당일 미래정보는 사용하지 않습니다.

    주의: 현재 버전은 C타입의 '시장상태/매수비중'을 먼저 백테스트하기 위한 프로토타입입니다.
    LOC 가격 간격은 V31과 동일한 네 가지 격자를 유지해 C타입 상태판단의 효과를 분리해서 봅니다.
    """
    ser = pd.Series(close_series).dropna().astype(float)
    if ser.empty:
        return {
            "state": "C-기본",
            "offsets": (-0.02, -0.04),
            "weights": (0.05, 0.05),
            "risk_score": 5,
            "prev_close": np.nan,
        }

    # QQQ/SMH가 없을 때만 구형 안전 폴백을 사용합니다.
    if qqq_series is None or smh_series is None:
        prev = float(ser.iloc[-1])
        return {
            "state": "C-데이터부족",
            "offsets": (-0.02, -0.04),
            "weights": (0.05, 0.05),
            "risk_score": 5,
            "prev_close": prev,
        }

    feat = _soxl_c_features(ser, qqq_series, smh_series)
    if feat is None or feat.empty:
        prev = float(ser.iloc[-1])
        return {
            "state": "C-데이터부족",
            "offsets": (-0.02, -0.04),
            "weights": (0.05, 0.05),
            "risk_score": 5,
            "prev_close": prev,
        }

    r = feat.iloc[-1]
    prev = float(r["SOXL"])
    risk = 0
    flags = []

    def add(cond, label):
        nonlocal risk
        if bool(cond):
            risk += 1
            flags.append(label)

    add(np.isfinite(r["Q_MA5"]) and np.isfinite(r["Q_MA10"]) and r["Q_MA5"] < r["Q_MA10"], "QQQ 5<10")
    add(np.isfinite(r["Q_MA10"]) and np.isfinite(r["Q_MA20"]) and r["Q_MA10"] < r["Q_MA20"], "QQQ 10<20")
    add(np.isfinite(r["Q_MA20_SLOPE5"]) and r["Q_MA20_SLOPE5"] < 0, "QQQ MA20↓")
    add(np.isfinite(r["RS5"]) and r["RS5"] < 0, "SMH<QQQ")
    add(np.isfinite(r["S_MA10"]) and r["SOXL"] < r["S_MA10"], "SOXL<MA10")
    add(np.isfinite(r["S_MA20"]) and r["SOXL"] < r["S_MA20"], "SOXL<MA20")
    add(np.isfinite(r["S_DIST10_D3"]) and r["S_DIST10_D3"] < 0, "10일선 이격확대")
    add(np.isfinite(r["S_RSI_D3"]) and r["S_RSI_D3"] < 0, "RSI↓")
    add(float(exposure_now) > 0.15, "LOT>15%")
    add(float(exposure_now) > 0.30, "LOT>30%")

    # ------------------------------------------------------------
    # 두 연구 트랙을 완전히 분리합니다.
    # C-ORIGINAL: 실제 주문에서 관측한 4+4 / 5+5 / 6+6 / 7+7만 사용.
    # C-ALPHA: ORIGINAL을 보존한 채 CAGR만 별도 연구하는 공격형 복사본(C4 계열).
    # ------------------------------------------------------------
    if soxl_track.startswith("C-ORIGINAL"):
        if risk <= 2:
            state = "ORIGINAL-공격 7+7"
            offsets = (0.04, -0.01)
            weights = (0.07, 0.07)
        elif risk <= 4:
            state = "ORIGINAL-정상 6+6"
            offsets = (-0.01, -0.04)
            weights = (0.06, 0.06)
        elif risk <= 7:
            state = "ORIGINAL-방어 5+5"
            offsets = (-0.02, -0.04)
            weights = (0.05, 0.05)
        else:
            state = "ORIGINAL-강방어 4+4"
            offsets = (-0.02, -0.06)
            weights = (0.04, 0.04)
    else:
        # C4 RETURN 계열을 ALPHA의 기준선으로만 유지합니다.
        # ORIGINAL 채점에는 절대 사용하지 않습니다.
        exp = float(exposure_now)
        d3 = float(r["S_DIST10_D3"]) if np.isfinite(r["S_DIST10_D3"]) else np.nan
        rsi_d3 = float(r["S_RSI_D3"]) if np.isfinite(r["S_RSI_D3"]) else np.nan
        q_slope = float(r["Q_MA20_SLOPE5"]) if np.isfinite(r["Q_MA20_SLOPE5"]) else np.nan
        rs5v = float(r["RS5"]) if np.isfinite(r["RS5"]) else np.nan
        rebound = (np.isfinite(d3) and d3 > 0.025 and np.isfinite(rsi_d3) and rsi_d3 > 1.5
                   and np.isfinite(q_slope) and q_slope >= -0.006 and np.isfinite(rs5v) and rs5v > -0.015)
        strong = (risk <= 3 and np.isfinite(q_slope) and q_slope >= 0 and np.isfinite(rs5v) and rs5v >= 0)
        if strong and exp < 0.55:
            state, offsets, weights = "ALPHA-강공격 9+9", (0.055, 0.000), (0.09, 0.09)
        elif rebound and exp < 0.55:
            state, offsets, weights = "ALPHA-반등공격 9+8", (0.040, -0.010), (0.09, 0.08)
        elif risk <= 4:
            state, offsets, weights = "ALPHA-공격 8+8", (0.025, -0.020), (0.08, 0.08)
        elif risk <= 6:
            if exp >= 0.65:
                state, offsets, weights = "ALPHA-고LOT 4+5", (-0.030, -0.070), (0.04, 0.05)
            else:
                state, offsets, weights = "ALPHA-중립 6+7", (-0.010, -0.045), (0.06, 0.07)
        elif exp >= 0.60:
            state, offsets, weights = "ALPHA-강약세 3+4", (-0.055, -0.110), (0.03, 0.04)
        else:
            state, offsets, weights = "ALPHA-약세 5+6", (-0.035, -0.080), (0.05, 0.06)

    return {
        "state": state,
        "offsets": offsets,
        "weights": weights,
        "risk_score": int(risk),
        "risk_flags": flags,
        "prev_close": prev,
        "rsi14": float(r["S_RSI14"]) if np.isfinite(r["S_RSI14"]) else np.nan,
        "dist10": float(r["S_DIST10"]) if np.isfinite(r["S_DIST10"]) else np.nan,
        "dist20": float(r["S_DIST20"]) if np.isfinite(r["S_DIST20"]) else np.nan,
        "rs5": float(r["RS5"]) if np.isfinite(r["RS5"]) else np.nan,
        "qqq_ma20_slope5": float(r["Q_MA20_SLOPE5"]) if np.isfinite(r["Q_MA20_SLOPE5"]) else np.nan,
    }

def simulate_soxl_reverse(
    df,
    initial_cash,
    base_unit=0.06,
    take_profit=0.06,
    max_hold_days=None,
    fee_pct=0.0,
    slippage_pct=0.0,
    fx_cost_pct=0.0,
    deployment_ratio=1.0,
    trade_start=None,
):
    """SOXL DUAL 백테스트 엔진.

    - C타입: SOXL 이평/RSI + LOT + QQQ 이평구조 + SMH/QQQ 5일 상대강도
    - VIX 제외
    - 모든 오늘 주문은 전 거래일까지의 확정 일봉으로만 계산
    - 매수비중은 관측 템플릿 4+4 / 5+5 / 6+6 / 7+7 고정
    - LOC 가격 격자와 블록별 익절/기간청산은 V31과 동일하게 유지하여 상태판단 효과를 분리
    """
    cash = float(initial_cash)
    lots = []
    trades = []
    equity_rows = []
    # C-ALPHA 최신 연구 상태 (C-ORIGINAL은 그대로 보존)
    alpha_bear = False
    alpha_boost_left = 0

    if "QQQ" not in df.columns or "SMH" not in df.columns:
        raise ValueError("V32-C SOXL 백테스트에는 QQQ와 SMH 일봉이 필요합니다.")

    close = df["TRADE"].astype(float).copy()
    qqq = df["QQQ"].astype(float).copy()
    smh = df["SMH"].astype(float).copy()
    feat = _soxl_c_features(close, qqq, smh)
    feat = feat.reindex(df.index)

    for i, dt in enumerate(df.index):
        px = float(close.iloc[i])
        if not np.isfinite(px) or px <= 0:
            continue

        # 모든 위험/반등 판정은 전 거래일 확정 일봉만 사용
        alpha_fast = False
        alpha_rebound = False
        if soxl_track.startswith("C-ALPHA") and i > 0:
            pr = feat.iloc[i - 1]
            q5v = float(pr.get("QQQ_R5", np.nan))
            q50 = float(pr.get("Q_MA50", np.nan))
            q200 = float(pr.get("Q_MA200", np.nan))
            qddv = float(pr.get("Q_DD252", np.nan))
            s3v = float(pr.get("S_R3", np.nan))
            s5v = float(pr.get("S_R5", np.nan))
            qprev = float(pr.get("QQQ", np.nan))
            alpha_fast = (np.isfinite(qprev) and np.isfinite(q50) and np.isfinite(q5v)
                          and qprev < q50 and q5v <= -0.05)
            if (np.isfinite(qprev) and np.isfinite(q200) and np.isfinite(qddv)
                    and qprev < q200 and qddv <= -0.10):
                alpha_bear = True
            if (alpha_bear and np.isfinite(s3v) and np.isfinite(s5v) and np.isfinite(q5v)
                    and s3v >= 0.06 and s5v >= 0.20 and q5v > 0):
                alpha_bear = False
                alpha_rebound = True
                alpha_boost_left = 3

        _trade_enabled = trade_start is None or pd.Timestamp(dt).normalize() >= pd.Timestamp(trade_start).normalize()

        # 1) 기존 블록 청산
        remaining = []
        for lot in lots:
            age = (pd.Timestamp(dt) - pd.Timestamp(lot["date"])).days
            tp_eff = float(take_profit)
            if soxl_track.startswith("C-ALPHA"):
                # ALPHA 기준선(C4)의 회전 규칙. ORIGINAL에는 적용하지 않음.
                mark_before = sum(x["qty"] * px for x in lots)
                eq_before = cash + mark_before
                exp_before = mark_before / eq_before if eq_before > 0 else 0.0
                if exp_before >= 0.75:
                    tp_eff = min(tp_eff, 0.025)
                elif exp_before >= 0.60:
                    tp_eff = min(tp_eff, 0.035)
                elif exp_before >= 0.45:
                    tp_eff = min(tp_eff, 0.045)
            target = lot["price"] * (1 + tp_eff)
            hit_tp = px >= target
            forced = max_hold_days is not None and age >= int(max_hold_days)
            alpha_cash = soxl_track.startswith("C-ALPHA") and (alpha_fast or alpha_bear)
            if _trade_enabled and (hit_tp or forced or alpha_cash):
                sell_px = px * (1 - slippage_pct)
                proceeds = lot["qty"] * sell_px * (1 - fee_pct - fx_cost_pct)
                cash += proceeds
                pnl = proceeds - lot["cash_cost"]
                trades.append({
                    "신호일": lot["date"],
                    "매도일": pd.Timestamp(dt),
                    "평균매수가": lot["price"],
                    "매도가": sell_px,
                    "수익률": proceeds / lot["cash_cost"] - 1 if lot["cash_cost"] > 0 else 0.0,
                    "실현손익": pnl,
                    "보유일수": age,
                    "매수횟수": 1,
                    "청산사유": ("ALPHA위험회피" if alpha_cash else ("익절" if hit_tp else "기간청산")),
                    "진입상태": lot.get("state", ""),
                })
            else:
                remaining.append(lot)
        lots = remaining

        # 2) 오늘 주문은 반드시 전일까지의 상태만 사용
        if i > 0 and _trade_enabled:
            mark_value = sum(lot["qty"] * px for lot in lots)
            equity_now = cash + mark_value
            max_invested = equity_now * float(deployment_ratio)
            invested_now = mark_value
            exposure_now = invested_now / equity_now if equity_now > 0 else 0.0

            plan = get_soxl_loc_plan(
                close.iloc[:i],
                exposure_now=exposure_now,
                base_unit=base_unit,
                qqq_series=qqq.iloc[:i],
                smh_series=smh.iloc[:i],
            )
            prev = float(plan["prev_close"])
            state = plan["state"]
            offsets = plan["offsets"]
            weights = plan["weights"]

            # 최신 C-ALPHA: FAST RISK/BEAR 현금화 + 반등 3일 2.5배 BOOST
            if soxl_track.startswith("C-ALPHA"):
                if alpha_fast or alpha_bear:
                    weights = (0.0, 0.0)
                    state = "ALPHA-현금방어"
                elif alpha_boost_left > 0:
                    offsets = (max(float(offsets[0]), 0.04), max(float(offsets[1]), 0.00))
                    weights = (min(float(weights[0]) * 2.5, 0.30),
                               min(float(weights[1]) * 2.5, 0.30))
                    state = "ALPHA-반등BOOST"

            for off, weight in zip(offsets, weights):
                limit_px = prev * (1 + float(off))
                if px > limit_px + 1e-12:
                    continue
                room = max(0.0, max_invested - invested_now)
                planned = equity_now * float(weight)
                budget = min(planned, room, cash)
                if budget <= max(1e-9, equity_now * 0.001):
                    continue
                buy_px = px * (1 + slippage_pct)
                unit_cost = buy_px * (1 + fee_pct + fx_cost_pct)
                qty = budget / unit_cost
                cash -= budget
                lots.append({
                    "date": pd.Timestamp(dt),
                    "price": buy_px,
                    "qty": qty,
                    "cash_cost": budget,
                    "state": state,
                    "loc_offset": float(off),
                    "risk_score": int(plan.get("risk_score", -1)),
                })
                invested_now += qty * px

        if soxl_track.startswith("C-ALPHA") and alpha_boost_left > 0:
            alpha_boost_left -= 1

        shares = sum(lot["qty"] for lot in lots)
        invested_value = shares * px * (1 - slippage_pct) * (1 - fee_pct - fx_cost_pct)
        equity = cash + invested_value
        exposure = invested_value / equity if equity > 0 else 0.0
        equity_rows.append((pd.Timestamp(dt), equity, cash, shares, px, exposure))

    if not equity_rows:
        raise ValueError("SOXL V32-C 백테스트에 사용할 데이터가 없습니다.")

    eq = pd.DataFrame(
        equity_rows,
        columns=["Date", "Equity", "Cash", "Shares", "TradePrice", "Exposure"],
    ).set_index("Date")
    eq["SignalPrice"] = eq["TradePrice"]
    eq["Drawdown"] = 1 - eq["TradePrice"] / eq["TradePrice"].cummax()

    final_value = float(eq["Equity"].iloc[-1])
    total_return = final_value / float(initial_cash) - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    cagr = (final_value / float(initial_cash)) ** (1 / years) - 1 if final_value > 0 else -1.0
    peak = eq["Equity"].cummax()
    mdd = float((eq["Equity"] / peak - 1).min())

    trades_df = pd.DataFrame(trades)
    trade_count = len(trades_df)
    win_rate = float((trades_df["실현손익"] > 0).mean()) if trade_count else np.nan
    avg_hold = float(trades_df["보유일수"].mean()) if trade_count else np.nan
    max_hold = float(trades_df["보유일수"].max()) if trade_count else 0.0
    forced_exit_count = int((trades_df["청산사유"] == "기간청산").sum()) if trade_count else 0

    shares = sum(lot["qty"] for lot in lots)
    avg_entry = (sum(lot["qty"] * lot["price"] for lot in lots) / shares) if shares > 0 else np.nan
    unrealized_pct = (float(close.iloc[-1]) / avg_entry - 1) if shares > 0 and avg_entry > 0 else np.nan
    if lots:
        ongoing = max((pd.Timestamp(df.index[-1]) - pd.Timestamp(lot["date"])).days for lot in lots)
        max_hold = max(max_hold, float(ongoing))

    return {
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "mdd": mdd,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_hold": avg_hold,
        "max_hold": max_hold,
        "avg_exposure": float(eq["Exposure"].mean()),
        "max_exposure": float(eq["Exposure"].max()),
        "deployment_ratio": float(deployment_ratio),
        "forced_exit_count": forced_exit_count,
        "equity": eq,
        "trades": trades_df,
        "cash": cash,
        "shares": shares,
        "avg_entry": avg_entry,
        "unrealized_pct": unrealized_pct,
        "next_level": min(len(lots) + 1, 20),
        "tranche_budget": float(initial_cash) * 0.06,
        "tranche_budgets": [float(initial_cash) * 0.06] * 20,
        "allocation_weights": [0.06] * 20,
    }
