import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
from io import StringIO

st.set_page_config(
    page_title="TQQQ / 코코레 QUANT V11",
    page_icon="📈",
    layout="centered",
)

st.title("📈 TQQQ / 코코레 QUANT V11")
st.caption("일봉 전용 · 가중비중 자동비교 + TQQQ LOC + 단기회전 + 실제 매매기록")

market = st.radio(
    "시장",
    ["🇺🇸 미국장 — TQQQ", "🇰🇷 한국장 — 코코레"],
    horizontal=True,
)

if market.startswith("🇺🇸"):
    currency = "$"
    unit = "달러"
    default_investment = 4000.0
    min_investment = 1000.0
    step_investment = 1000.0
    default_tps = [0.10, 0.15, 0.20]
else:
    currency = "₩"
    unit = "원"
    default_investment = 10_000_000.0
    min_investment = 100_000.0
    step_investment = 100_000.0
    default_tps = [0.05, 0.10, 0.15]

investment = st.number_input(
    f"💰 초기 투자금액 ({unit})",
    min_value=min_investment,
    value=default_investment,
    step=step_investment,
)

st.info("모든 계산은 일봉 종가 기준입니다. 장중 체결가격과는 차이가 날 수 있습니다.")

with st.expander("⚙️ 백테스트 설정", expanded=False):
    if market.startswith("🇺🇸"):
        us_execution_mode = st.radio(
            "미국장 체결 방식",
            ["일반 종가모드", "LOC 모드"],
            horizontal=True,
            help=(
                "일반 종가모드는 신호 발생일 종가 체결 가정입니다. "
                "LOC 모드는 직전 거래일 종가 기준으로 LOC 한도가격을 정하고, "
                "당일 종가가 한도가격 조건을 만족할 때만 체결된 것으로 계산합니다."
            ),
        )
        loc_buy_offset = st.select_slider(
            "LOC 매수 한도",
            options=[0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02],
            value=0.005,
            format_func=lambda x: f"전일 종가 대비 -{x:.2%}",
            disabled=(us_execution_mode != "LOC 모드"),
            help="예: -0.50%이면 전일 종가보다 0.5% 낮거나 같은 종가에서만 매수 체결로 봅니다.",
        )
        loc_sell_offset = st.select_slider(
            "LOC 익절 한도 여유",
            options=[0.0, 0.0025, 0.005, 0.0075, 0.01],
            value=0.0,
            format_func=lambda x: f"+{x:.2%}",
            disabled=(us_execution_mode != "LOC 모드"),
            help="0%이면 목표 익절가 이상 종가에서만 매도 체결로 봅니다.",
        )
    else:
        us_execution_mode = "일반 종가모드"
        loc_buy_offset = 0.0
        loc_sell_offset = 0.0

    anchor_mode = st.selectbox(
        "하락 기준점",
        ["최근 60거래일 고점", "최근 120거래일 고점", "역대 고점"],
        index=0,
    )

    buy_steps = st.multiselect(
        "비교할 매수 간격",
        [0.03, 0.05, 0.07, 0.10],
        default=[0.05, 0.07, 0.10],
        format_func=lambda x: f"{x:.0%}",
    )

    take_profits = st.multiselect(
        "비교할 익절률",
        [0.05, 0.07, 0.10, 0.15, 0.20, 0.25, 0.30],
        default=default_tps,
        format_func=lambda x: f"{x:.0%}",
    )

    tranche_count = st.slider(
        "분할매수 횟수",
        min_value=2,
        max_value=10,
        value=5,
        step=1,
    )

    weight_modes = st.multiselect(
        "분할매수 비중 자동 비교",
        ["균등비중", "약한 가중", "강한 가중"],
        default=["균등비중", "약한 가중", "강한 가중"],
        help=(
            "선택한 비중 방식을 한 번의 백테스트에서 모두 비교합니다. "
            "균등비중은 매회 같은 금액, 약한/강한 가중은 "
            "하락 단계가 깊어질수록 더 큰 금액을 매수합니다."
        ),
    )

    st.markdown("**필터 자동 비교**")
    use_ma_candidates = st.checkbox("200일선 필터 비교", value=True)
    use_rsi_candidates = st.checkbox("RSI 필터 비교", value=True)

    rsi_thresholds = st.multiselect(
        "비교할 RSI 상한",
        [35, 40, 45, 50, 55],
        default=[40, 45, 50],
        disabled=not use_rsi_candidates,
        help="예: RSI 45 이하면 매수 허용",
    )

    st.markdown("**⚡ 단기회전 모드**")
    short_mode = st.checkbox(
        "단기회전 전략 비교",
        value=True,
        help="짧은 익절과 최대 보유기간 제한을 함께 비교합니다.",
    )

    short_buy_steps = st.multiselect(
        "단기 매수 간격",
        [0.03, 0.05, 0.07, 0.10],
        default=[0.03, 0.05, 0.07],
        format_func=lambda x: f"{x:.0%}",
        disabled=not short_mode,
    )

    short_take_profits = st.multiselect(
        "단기 익절률",
        [0.03, 0.05, 0.07, 0.10, 0.15],
        default=[0.03, 0.05, 0.07],
        format_func=lambda x: f"{x:.0%}",
        disabled=not short_mode,
    )

    max_hold_candidates = st.multiselect(
        "최대 보유기간",
        [30, 60, 90, 120, 180],
        default=[30, 60, 90, 180],
        format_func=lambda x: f"{x}일",
        disabled=not short_mode,
        help="해당 기간 안에 익절하지 못하면 그날 종가에 전량 청산하는 조건입니다.",
    )

run_backtest = st.button(
    "🚀 백테스트 실행",
    type="primary",
    use_container_width=True,
)

for key in ["backtest_result", "backtest_sims", "backtest_params"]:
    if key not in st.session_state:
        st.session_state[key] = None


@st.cache_data(ttl=900)
def download_close(symbols):
    raw = yf.download(
        symbols,
        period="max",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )

    if raw.empty:
        raise ValueError("가격 데이터를 받지 못했습니다.")

    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            close = raw["Close"].copy()
        elif "Close" in raw.columns.get_level_values(-1):
            close = raw.xs("Close", axis=1, level=-1).copy()
        else:
            raise ValueError("종가(Close) 데이터를 찾지 못했습니다.")
    else:
        if "Close" not in raw.columns:
            raise ValueError("종가(Close) 데이터를 찾지 못했습니다.")
        close = raw[["Close"]].copy()

    if isinstance(close, pd.Series):
        close = close.to_frame()

    return close


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def anchor_series(series, mode):
    if mode == "최근 60거래일 고점":
        return series.rolling(60, min_periods=1).max()
    if mode == "최근 120거래일 고점":
        return series.rolling(120, min_periods=1).max()
    return series.cummax()


def filter_name(use_ma, rsi_max):
    parts = []
    if use_ma:
        parts.append("200일선")
    if rsi_max is not None:
        parts.append(f"RSI≤{int(rsi_max)}")
    return "기본" if not parts else " + ".join(parts)


def entry_allowed(row, use_ma, rsi_max):
    if use_ma and not bool(row["TREND_OK"]):
        return False

    if rsi_max is not None:
        rsi = row["RSI14"]
        if not np.isfinite(rsi) or rsi > rsi_max:
            return False

    return True


def make_tranche_budgets(initial_cash, n_tranches, weight_mode):
    if weight_mode == "약한 가중":
        weights = np.linspace(1.0, 2.0, n_tranches)
    elif weight_mode == "강한 가중":
        weights = np.linspace(1.0, 3.0, n_tranches)
    else:
        weights = np.ones(n_tranches)

    weights = weights / weights.sum()
    return (float(initial_cash) * weights).tolist()


def simulate(
    df,
    initial_cash,
    step_pct,
    take_profit,
    n_tranches,
    use_ma,
    rsi_max,
    weight_mode="균등비중",
    max_hold_days=None,
    execution_mode="일반 종가모드",
    loc_buy_offset=0.0,
    loc_sell_offset=0.0,
):
    cash = float(initial_cash)
    shares = 0.0
    total_cost = 0.0
    entries = []
    trades = []
    equity_rows = []
    next_level = 1
    tranche_budgets = make_tranche_budgets(
        initial_cash, n_tranches, weight_mode
    )

    idx = df.index
    signal_arr = df["SIGNAL"].to_numpy(dtype=float)
    trade_arr = df["TRADE"].to_numpy(dtype=float)
    prev_trade_arr = df["TRADE"].shift(1).to_numpy(dtype=float)
    anchor_arr = df["ANCHOR"].to_numpy(dtype=float)
    trend_arr = df["TREND_OK"].to_numpy(dtype=bool)
    rsi_arr = df["RSI14"].to_numpy(dtype=float)

    for i in range(len(df)):
        dt = pd.Timestamp(idx[i])
        signal_price = signal_arr[i]
        trade_price = trade_arr[i]
        anchor = anchor_arr[i]

        if (
            not np.isfinite(signal_price)
            or not np.isfinite(trade_price)
            or signal_price <= 0
            or trade_price <= 0
            or anchor <= 0
        ):
            continue

        drawdown = max(0.0, 1 - signal_price / anchor)
        exited_today = False

        if shares > 0:
            avg_price = total_cost / shares
            pnl_pct = trade_price / avg_price - 1
            first_buy_date = entries[0][0] if entries else dt
            holding_days = (dt - pd.Timestamp(first_buy_date)).days

            exit_reason = None

            target_sell_price = avg_price * (1 + take_profit)
            sell_fill_ok = True
            if execution_mode == "LOC 모드":
                loc_sell_limit = target_sell_price * (1 + loc_sell_offset)
                sell_fill_ok = trade_price >= loc_sell_limit

            if pnl_pct >= take_profit and sell_fill_ok:
                exit_reason = "익절"
            elif max_hold_days is not None and holding_days >= max_hold_days:
                exit_reason = "기간청산"

            if exit_reason is not None:
                proceeds = shares * trade_price
                realized = proceeds - total_cost
                cash += proceeds

                trades.append(
                    {
                        "매도일": dt,
                        "평균매수가": avg_price,
                        "매도가": trade_price,
                        "수익률": pnl_pct,
                        "실현손익": realized,
                        "보유일수": holding_days,
                        "매수횟수": len(entries),
                        "청산사유": exit_reason,
                    }
                )

                shares = 0.0
                total_cost = 0.0
                entries = []
                next_level = 1
                exited_today = True

        allowed = True
        if use_ma and not trend_arr[i]:
            allowed = False
        if rsi_max is not None and (not np.isfinite(rsi_arr[i]) or rsi_arr[i] > rsi_max):
            allowed = False

        buy_fill_ok = True
        if execution_mode == "LOC 모드":
            prev_trade_price = prev_trade_arr[i]
            if not np.isfinite(prev_trade_price) or prev_trade_price <= 0:
                buy_fill_ok = False
            else:
                loc_buy_limit = prev_trade_price * (1 - loc_buy_offset)
                buy_fill_ok = trade_price <= loc_buy_limit

        if allowed and not exited_today and buy_fill_ok:
            while next_level <= n_tranches and drawdown >= step_pct * next_level:
                planned_budget = tranche_budgets[next_level - 1]
                budget = min(planned_budget, cash)
                if budget <= 0:
                    break

                qty = budget / trade_price
                shares += qty
                total_cost += budget
                cash -= budget
                entries.append((dt, trade_price, budget, next_level))
                next_level += 1

        equity = cash + shares * trade_price
        equity_rows.append(
            (dt, equity, cash, shares, signal_price, trade_price, drawdown)
        )

    if not equity_rows:
        raise ValueError("백테스트에 사용할 데이터가 없습니다.")

    eq = pd.DataFrame(
        equity_rows,
        columns=[
            "Date", "Equity", "Cash", "Shares",
            "SignalPrice", "TradePrice", "Drawdown",
        ],
    ).set_index("Date")

    final_value = float(eq["Equity"].iloc[-1])
    total_return = final_value / initial_cash - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    cagr = (final_value / initial_cash) ** (1 / years) - 1 if final_value > 0 else -1.0

    peak = eq["Equity"].cummax()
    mdd = float((eq["Equity"] / peak - 1).min())

    trades_df = pd.DataFrame(trades)
    trade_count = len(trades_df)
    win_rate = float((trades_df["실현손익"] > 0).mean()) if trade_count else np.nan
    avg_hold = float(trades_df["보유일수"].mean()) if trade_count else np.nan
    closed_max_hold = float(trades_df["보유일수"].max()) if trade_count else 0.0
    forced_exit_count = (
        int((trades_df["청산사유"] == "기간청산").sum())
        if trade_count and "청산사유" in trades_df.columns else 0
    )

    ongoing_hold = 0.0
    avg_entry = np.nan
    unrealized_pct = np.nan

    if shares > 0 and entries:
        avg_entry = total_cost / shares
        current_trade_price = float(df["TRADE"].iloc[-1])
        unrealized_pct = current_trade_price / avg_entry - 1
        ongoing_hold = (pd.Timestamp(df.index[-1]) - pd.Timestamp(entries[0][0])).days

    max_hold = max(closed_max_hold, ongoing_hold)

    return {
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "mdd": mdd,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_hold": avg_hold,
        "max_hold": max_hold,
        "forced_exit_count": forced_exit_count,
        "equity": eq,
        "trades": trades_df,
        "cash": cash,
        "shares": shares,
        "avg_entry": avg_entry,
        "unrealized_pct": unrealized_pct,
        "next_level": next_level,
        "tranche_budget": tranche_budgets[min(next_level - 1, n_tranches - 1)],
        "tranche_budgets": tranche_budgets,
        "weight_mode": weight_mode,
    }


try:
    # ------------------------------------------------------------
    # 데이터 / 비교할 운용 방식
    # ------------------------------------------------------------
    if market.startswith("🇺🇸"):
        symbols = ["TQQQ", "QQQ"]
        close = download_close(symbols).dropna()

        modes = [
            {
                "mode": "TQQQ 신호 → TQQQ 매수",
                "signal_symbol": "TQQQ",
                "trade_symbol": "TQQQ",
                "ref_symbol": "QQQ",
            }
        ]
    else:
        symbols = ["233740.KS", "229200.KS"]
        close = download_close(symbols).dropna()

        modes = [
            {
                "mode": "① 코코레 신호 → 코코레 매수",
                "signal_symbol": "233740.KS",
                "trade_symbol": "233740.KS",
                "ref_symbol": "229200.KS",
            },
            {
                "mode": "② 코코레 신호 → 코스닥150 본주 매수",
                "signal_symbol": "233740.KS",
                "trade_symbol": "229200.KS",
                "ref_symbol": "229200.KS",
            },
            {
                "mode": "③ 코스닥150 본주 신호 → 본주 매수",
                "signal_symbol": "229200.KS",
                "trade_symbol": "229200.KS",
                "ref_symbol": "229200.KS",
            },
        ]

    # 필터 조합
    filter_candidates = [(False, None)]

    if use_ma_candidates:
        filter_candidates.append((True, None))

    if use_rsi_candidates:
        for rsi_max in rsi_thresholds:
            filter_candidates.append((False, float(rsi_max)))
            if use_ma_candidates:
                filter_candidates.append((True, float(rsi_max)))

    # 중복 제거
    filter_candidates = list(dict.fromkeys(filter_candidates))

    if short_mode:
        effective_buy_steps = short_buy_steps
        effective_take_profits = short_take_profits
        effective_max_holds = max_hold_candidates
    else:
        effective_buy_steps = buy_steps
        effective_take_profits = take_profits
        effective_max_holds = [None]

    current_params = (
        market,
        float(investment),
        anchor_mode,
        tuple(effective_buy_steps),
        tuple(effective_take_profits),
        tuple(effective_max_holds),
        tranche_count,
        tuple(weight_modes),
        use_ma_candidates,
        use_rsi_candidates,
        tuple(rsi_thresholds),
        short_mode,
        us_execution_mode,
        float(loc_buy_offset),
        float(loc_sell_offset),
    )

    # 현재 지표 표시용: 코코레 시장에서는 코코레 낙폭 + 본주 RSI/200일선
    current_signal_symbol = modes[0]["signal_symbol"]
    current_ref_symbol = modes[0]["ref_symbol"]

    temp = pd.DataFrame(index=close.index)
    temp["SIGNAL"] = close[current_signal_symbol]
    temp["REF"] = close[current_ref_symbol]
    temp["ANCHOR"] = anchor_series(temp["SIGNAL"], anchor_mode)
    temp["DD"] = temp["SIGNAL"] / temp["ANCHOR"] - 1
    temp["MA200"] = temp["REF"].rolling(200).mean()
    temp["RSI14"] = calc_rsi(temp["REF"], 14)
    temp["TREND_OK"] = temp["REF"] > temp["MA200"]
    temp = temp.dropna()

    if temp.empty:
        raise ValueError("지표 계산 후 사용할 데이터가 없습니다.")

    current_signal_price = float(temp["SIGNAL"].iloc[-1])
    current_dd = float(temp["DD"].iloc[-1])
    current_rsi = float(temp["RSI14"].iloc[-1])
    current_trend_ok = bool(temp["TREND_OK"].iloc[-1])

    c1, c2, c3, c4 = st.columns(4)

    if market.startswith("🇺🇸"):
        c1.metric("TQQQ", f"${current_signal_price:,.2f}")
    else:
        c1.metric("코코레", f"₩{current_signal_price:,.0f}")

    c2.metric("고점 대비", f"{current_dd:.1%}")
    c3.metric("RSI", f"{current_rsi:.1f}")
    c4.metric("200일선", "위" if current_trend_ok else "아래")

    if market.startswith("🇰🇷"):
        st.caption(
            "① 레버리지 직접매수 / ② 코코레는 신호만 사용하고 본주 매수 / "
            "③ 본주 신호로 본주 매수를 같은 조건에서 비교합니다."
        )

    if (
        not effective_buy_steps
        or not effective_take_profits
        or not effective_max_holds
        or not weight_modes
    ):
        st.warning("매수 간격, 익절률, 최대 보유기간, 분할매수 비중을 각각 하나 이상 선택해 주세요.")
        st.stop()

    st.divider()
    st.subheader("🏆 전략 자동 비교")

    if run_backtest:
        results = []
        sims = {}

        total_jobs = (
            len(modes)
            * len(effective_buy_steps)
            * len(effective_take_profits)
            * len(filter_candidates)
            * len(effective_max_holds)
            * len(weight_modes)
        )
        done_jobs = 0

        progress = st.progress(0)
        status = st.empty()

        with st.spinner("백테스트 계산 중..."):
            for mode in modes:
                df = pd.DataFrame(index=close.index)
                df["SIGNAL"] = close[mode["signal_symbol"]]
                df["TRADE"] = close[mode["trade_symbol"]]
                df["REF"] = close[mode["ref_symbol"]]

                df["ANCHOR"] = anchor_series(df["SIGNAL"], anchor_mode)
                df["MA200"] = df["REF"].rolling(200).mean()
                df["RSI14"] = calc_rsi(df["REF"], 14)
                df["TREND_OK"] = df["REF"] > df["MA200"]
                df = df.dropna()

                for step_pct in effective_buy_steps:
                    for tp in effective_take_profits:
                        for use_ma, rsi_max in filter_candidates:
                            for max_hold_days in effective_max_holds:
                                for weight_mode in weight_modes:
                                    sim = simulate(
                                        df,
                                        float(investment),
                                        step_pct,
                                        tp,
                                        tranche_count,
                                        use_ma,
                                        rsi_max,
                                        weight_mode,
                                        max_hold_days,
                                        us_execution_mode,
                                        float(loc_buy_offset),
                                        float(loc_sell_offset),
                                    )

                                    fname = filter_name(use_ma, rsi_max)
                                    hold_name = "제한없음" if max_hold_days is None else f"{max_hold_days}일"
                                    exec_name = (
                                        f"LOC(-{loc_buy_offset:.2%})"
                                        if market.startswith("🇺🇸") and us_execution_mode == "LOC 모드"
                                        else "종가"
                                    )
                                    strategy_name = (
                                        f"{mode['mode']} | "
                                        f"{step_pct:.0%} 간격 / {tp:.0%} 익절 / "
                                        f"{weight_mode} / 최대 {hold_name} / {fname} / {exec_name}"
                                    )

                                    sims[strategy_name] = {
                                        "sim": sim,
                                        "mode": mode,
                                        "df": df,
                                        "step_pct": step_pct,
                                        "tp": tp,
                                        "use_ma": use_ma,
                                        "rsi_max": rsi_max,
                                        "weight_mode": weight_mode,
                                        "max_hold_days": max_hold_days,
                                        "execution_mode": us_execution_mode,
                                        "loc_buy_offset": float(loc_buy_offset),
                                        "loc_sell_offset": float(loc_sell_offset),
                                    }

                                    results.append(
                                        {
                                            "전략": strategy_name,
                                            "운용방식": mode["mode"],
                                            "매수간격": step_pct,
                                            "익절률": tp,
                                            "매수비중": weight_mode,
                                            "필터": fname,
                                            "체결방식": exec_name,
                                            "보유제한": hold_name,
                                            "최종자산": sim["final_value"],
                                            "누적수익률": sim["total_return"],
                                            "CAGR": sim["cagr"],
                                            "MDD": sim["mdd"],
                                            "최대보유일": sim["max_hold"],
                                            "기간청산": sim["forced_exit_count"],
                                            "완료매매": sim["trade_count"],
                                            "승률": sim["win_rate"],
                                        }
                                    )

                                    done_jobs += 1
                                    progress.progress(done_jobs / max(total_jobs, 1))
                                    status.caption(f"계산 중... {done_jobs}/{total_jobs}")

        result = (
            pd.DataFrame(results)
            .sort_values(["최종자산", "CAGR"], ascending=False)
            .reset_index(drop=True)
        )

        st.session_state.backtest_result = result
        st.session_state.backtest_sims = sims
        st.session_state.backtest_params = current_params

        progress.empty()
        status.empty()

    result = st.session_state.backtest_result
    sims = st.session_state.backtest_sims

    if result is None or sims is None:
        st.info("위의 **🚀 백테스트 실행** 버튼을 눌러 결과를 계산해 주세요.")
        st.stop()

    if st.session_state.backtest_params != current_params:
        st.warning("설정을 변경했습니다. 새 조건으로 보려면 **🚀 백테스트 실행**을 다시 눌러 주세요.")

    winner = result.iloc[0]
    winner_name = winner["전략"]
    winner_bundle = sims[winner_name]

    best = winner_bundle["sim"]
    best_mode = winner_bundle["mode"]
    best_df = winner_bundle["df"]
    best_step = float(winner_bundle["step_pct"])
    best_tp = float(winner_bundle["tp"])
    best_use_ma = bool(winner_bundle["use_ma"])
    best_rsi = winner_bundle["rsi_max"]
    best_weight_mode = winner_bundle.get("weight_mode", "균등비중")
    best_max_hold = winner_bundle.get("max_hold_days")
    best_execution_mode = winner_bundle.get("execution_mode", "일반 종가모드")
    best_loc_buy_offset = winner_bundle.get("loc_buy_offset", 0.0)
    best_loc_sell_offset = winner_bundle.get("loc_sell_offset", 0.0)

    st.success(f"🥇 최종자산 1위: **{winner['운용방식']}**")

    if market.startswith("🇺🇸"):
        if best_execution_mode == "LOC 모드":
            st.info(
                f"🇺🇸 LOC 적용: 전일 종가 대비 -{best_loc_buy_offset:.2%} 매수 한도 / "
                f"익절 목표가 대비 +{best_loc_sell_offset:.2%} 매도 한도"
            )
        else:
            st.info("🇺🇸 일반 종가 체결 가정")
    st.write(
        f"**{best_step:.0%} 간격 / {best_tp:.0%} 익절 / "
        f"{best_weight_mode} / {filter_name(best_use_ma, best_rsi)} / "
        f"최대보유 {'제한없음' if best_max_hold is None else str(best_max_hold) + '일'}**"
    )

    st.subheader("⚖️ 비중 방식 비교")
    weight_summary = (
        result.sort_values(["최종자산", "CAGR"], ascending=False)
        .groupby("매수비중", as_index=False)
        .first()
        .sort_values(["최종자산", "CAGR"], ascending=False)
    )

    weight_view = weight_summary[
        ["매수비중", "최종자산", "CAGR", "MDD", "최대보유일", "완료매매"]
    ].copy()
    weight_view["최종자산"] = weight_view["최종자산"].map(
        lambda x: f"{currency}{x:,.0f}"
    )
    weight_view["CAGR"] = weight_view["CAGR"].map(lambda x: f"{x:.1%}")
    weight_view["MDD"] = weight_view["MDD"].map(lambda x: f"{x:.1%}")
    weight_view["최대보유일"] = weight_view["최대보유일"].map(lambda x: f"{x:.0f}일")
    st.dataframe(weight_view, use_container_width=True, hide_index=True)

    best_weight_row = weight_summary.iloc[0]
    st.success(
        f"🏆 비중 방식 1위: **{best_weight_row['매수비중']}** · "
        f"최종자산 {currency}{best_weight_row['최종자산']:,.0f} · "
        f"CAGR {best_weight_row['CAGR']:.1%} · "
        f"MDD {best_weight_row['MDD']:.1%}"
    )

    st.subheader("🎯 목적별 추천")
    cagr_pick = result.sort_values(["CAGR", "최종자산"], ascending=False).iloc[0]

    short_pool = result[result["최대보유일"] <= 180]
    short_pick = (
        short_pool.sort_values(["CAGR", "최종자산"], ascending=False).iloc[0]
        if not short_pool.empty else cagr_pick
    )

    scored = result.copy()
    scored["균형점수"] = (
        scored["CAGR"].rank(pct=True)
        + scored["MDD"].rank(pct=True)
        + (1 - scored["최대보유일"].rank(pct=True))
    )
    balance_pick = scored.sort_values(
        ["균형점수", "CAGR"], ascending=False
    ).iloc[0]

    r1, r2, r3 = st.columns(3)
    r1.metric("🚀 CAGR 1위", f"{cagr_pick['CAGR']:.1%}")
    r1.caption(f"{cagr_pick['운용방식']} · 최대 {cagr_pick['최대보유일']:.0f}일")
    r2.metric("⚡ 단기 1위", f"{short_pick['CAGR']:.1%}")
    r2.caption(f"{short_pick['운용방식']} · 최대 {short_pick['최대보유일']:.0f}일")
    r3.metric("⚖️ 균형형", f"{balance_pick['CAGR']:.1%}")
    r3.caption(f"MDD {balance_pick['MDD']:.1%} · 최대 {balance_pick['최대보유일']:.0f}일")

    # ------------------------------------------------------------
    # 운용 방식별 최고 전략 비교
    # ------------------------------------------------------------
    if market.startswith("🇰🇷"):
        st.subheader("🥊 3가지 운용 방식 비교")

        mode_best = (
            result.sort_values("최종자산", ascending=False)
            .groupby("운용방식", as_index=False)
            .first()
        )

        mv = mode_best[
            ["운용방식", "최종자산", "CAGR", "MDD", "최대보유일", "매수간격", "익절률", "필터"]
        ].copy()

        mv["최종자산"] = mv["최종자산"].map(lambda x: f"₩{x:,.0f}")
        mv["CAGR"] = mv["CAGR"].map(lambda x: f"{x:.1%}")
        mv["MDD"] = mv["MDD"].map(lambda x: f"{x:.1%}")
        mv["최대보유일"] = mv["최대보유일"].map(lambda x: f"{x:.0f}일")
        mv["매수간격"] = mv["매수간격"].map(lambda x: f"{x:.0%}")
        mv["익절률"] = mv["익절률"].map(lambda x: f"{x:.0%}")

        st.dataframe(mv, use_container_width=True, hide_index=True)

        # ②번과 ①번 차이를 한눈에 보여주기
        m1 = mode_best[mode_best["운용방식"].str.startswith("①")]
        m2 = mode_best[mode_best["운용방식"].str.startswith("②")]

        if not m1.empty and not m2.empty:
            a = m1.iloc[0]
            b = m2.iloc[0]

            d1, d2 = st.columns(2)
            d1.metric(
                "②의 CAGR 차이",
                f"{b['CAGR'] - a['CAGR']:+.1%}",
                help="① 코코레 직접매수와 비교한 차이",
            )
            d2.metric(
                "②의 MDD 개선",
                f"{b['MDD'] - a['MDD']:+.1%}",
                help="양수일수록 최대낙폭이 덜 심한 것",
            )

    # ------------------------------------------------------------
    # 실제 운용 기록
    # ------------------------------------------------------------
    st.divider()
    st.subheader("🗂️ 실제 매매 기록")

    if market.startswith("🇰🇷"):
        actual_trade_target = st.radio(
            "실제 매수·매도 기록 종목",
            ["코스닥150 본주", "코코레"],
            index=0,
            horizontal=True,
            help="신호는 코코레를 기준으로 보되, 실제 거래 기록은 본주 또는 코코레 중 선택할 수 있습니다.",
        )
        actual_trade_symbol = (
            "229200.KS" if actual_trade_target == "코스닥150 본주" else "233740.KS"
        )
    else:
        actual_trade_target = "TQQQ"
        actual_trade_symbol = "TQQQ"

    st.caption(
        "코코레 신호를 보면서 실제 거래는 본주 또는 코코레 중 선택할 수 있습니다. "
        "선택한 종목별로 보유 차수와 평균단가를 따로 계산합니다. "
        "Streamlit Cloud 재부팅에 대비해 CSV 백업을 보관해 주세요."
    )

    if "live_trades" not in st.session_state:
        st.session_state.live_trades = []

    uploaded_log = st.file_uploader(
        "기존 매매기록 CSV 불러오기",
        type=["csv"],
        key="trade_log_upload",
    )

    if uploaded_log is not None:
        upload_key = (uploaded_log.name, uploaded_log.size)
        if st.session_state.get("loaded_trade_log") != upload_key:
            try:
                restored = pd.read_csv(uploaded_log)
                needed = {"날짜", "구분", "가격", "금액"}
                if needed.issubset(restored.columns):
                    if "종목" not in restored.columns:
                        restored["종목"] = actual_trade_target
                    st.session_state.live_trades = restored.to_dict("records")
                    st.session_state.loaded_trade_log = upload_key
                    st.success("매매기록을 불러왔습니다.")
                else:
                    st.error("CSV 형식이 맞지 않습니다.")
            except Exception as ex:
                st.error(f"매매기록을 읽지 못했습니다: {ex}")

    trade_log = pd.DataFrame(st.session_state.live_trades)

    # 현재 보유상태 자동 계산
    live_qty = 0.0
    live_cost = 0.0
    live_buys = 0
    live_first_buy_date = None

    if not trade_log.empty:
        for _, rec in trade_log.iterrows():
            rec_target = str(rec.get("종목", actual_trade_target))
            if rec_target != actual_trade_target:
                continue

            side = str(rec.get("구분", ""))
            px = float(rec.get("가격", 0) or 0)
            amount = float(rec.get("금액", 0) or 0)

            if side == "매수" and px > 0 and amount > 0:
                if live_buys == 0:
                    try:
                        live_first_buy_date = pd.Timestamp(rec.get("날짜"))
                    except Exception:
                        live_first_buy_date = None
                live_qty += amount / px
                live_cost += amount
                live_buys += 1
            elif side == "전량매도":
                live_qty = 0.0
                live_cost = 0.0
                live_buys = 0
                live_first_buy_date = None

    auto_avg_price = live_cost / live_qty if live_qty > 0 else 0.0
    auto_stage = min(live_buys, tranche_count)

    log1, log2, log3 = st.columns(3)
    log1.metric("자동 보유 차수", f"{auto_stage}차")
    log2.metric(
        "자동 평균단가",
        f"{currency}{auto_avg_price:,.2f}" if live_qty > 0 else "-",
    )
    log3.metric("투입금액", f"{currency}{live_cost:,.0f}")

    with st.expander("➕ 매수/매도 기록 입력", expanded=False):
        record_date = st.date_input("거래일", value=date.today())
        selected_live_price = float(close[actual_trade_symbol].dropna().iloc[-1])

        record_price = st.number_input(
            f"실제 체결가격 ({unit})",
            min_value=0.0,
            value=selected_live_price,
            step=1.0 if currency == "$" else 10.0,
            key="record_price",
        )
        default_amount = float(investment) / tranche_count
        record_amount = st.number_input(
            f"매수금액 ({unit})",
            min_value=0.0,
            value=default_amount,
            step=100.0 if currency == "$" else 10000.0,
            key="record_amount",
        )

        b1, b2 = st.columns(2)

        if b1.button("🟢 매수 기록", use_container_width=True):
            if auto_stage >= tranche_count:
                st.warning("설정한 분할매수 횟수를 이미 모두 사용했습니다.")
            elif record_price <= 0 or record_amount <= 0:
                st.warning("체결가격과 매수금액을 입력해 주세요.")
            else:
                st.session_state.live_trades.append(
                    {
                        "날짜": str(record_date),
                        "종목": actual_trade_target,
                        "구분": "매수",
                        "가격": float(record_price),
                        "금액": float(record_amount),
                    }
                )
                st.rerun()

        if b2.button("🔴 전량매도 기록", use_container_width=True):
            if live_qty <= 0:
                st.warning("현재 기록상 보유 수량이 없습니다.")
            elif record_price <= 0:
                st.warning("매도 체결가격을 입력해 주세요.")
            else:
                proceeds = live_qty * float(record_price)
                st.session_state.live_trades.append(
                    {
                        "날짜": str(record_date),
                        "종목": actual_trade_target,
                        "구분": "전량매도",
                        "가격": float(record_price),
                        "금액": float(proceeds),
                    }
                )
                st.rerun()

    trade_log = pd.DataFrame(st.session_state.live_trades)

    if not trade_log.empty:
        st.dataframe(trade_log, use_container_width=True, hide_index=True)

        csv_bytes = trade_log.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "💾 매매기록 CSV 백업",
            data=csv_bytes,
            file_name="quant_trade_log.csv",
            mime="text/csv",
            use_container_width=True,
        )

        if st.button("🗑️ 매매기록 전체 초기화", use_container_width=True):
            st.session_state.live_trades = []
            st.session_state.pop("loaded_trade_log", None)
            st.rerun()

    # ------------------------------------------------------------
    # 오늘의 실제 매매 신호
    # ------------------------------------------------------------
    st.divider()
    st.subheader("🚦 오늘의 매매 신호")
    st.caption(
        "1위 전략 기준입니다. 위 실제 매매 기록에서 보유 차수와 평균단가를 자동으로 읽습니다. "
        "오늘 처음 시작하면 과거 여러 차수가 이미 충족돼도 한 번에 1차수만 안내합니다."
    )

    live_stage = auto_stage

    latest = best_df.iloc[-1]
    # 신호 ETF 가격은 1위 전략의 신호 기준을 사용하고,
    # 실제 매수/매도 판단 가격은 사용자가 선택한 실제 거래 종목을 사용합니다.
    signal_price = float(latest["SIGNAL"])
    trade_price = float(close[actual_trade_symbol].dropna().iloc[-1])
    live_anchor = float(latest["ANCHOR"])
    live_dd = max(0.0, 1 - signal_price / live_anchor)

    live_rsi = float(latest["RSI14"])
    live_trend_ok = bool(latest["TREND_OK"])

    next_stage = int(live_stage) + 1

    live_tranche_budgets = make_tranche_budgets(
        float(investment), tranche_count, best_weight_mode
    )
    tranche_budget = live_tranche_budgets[
        min(max(next_stage - 1, 0), tranche_count - 1)
    ]

    avg_buy_price = auto_avg_price if live_stage > 0 else None

    filter_ok = entry_allowed(latest, best_use_ma, best_rsi)

    loc_buy_fill_ok = True
    loc_buy_limit_today = None
    if market.startswith("🇺🇸") and best_execution_mode == "LOC 모드":
        trade_series = close[actual_trade_symbol].dropna()
        if len(trade_series) >= 2:
            prev_close = float(trade_series.iloc[-2])
            loc_buy_limit_today = prev_close * (1 - best_loc_buy_offset)
            loc_buy_fill_ok = trade_price <= loc_buy_limit_today

    filter_reasons = []
    if best_use_ma and not live_trend_ok:
        filter_reasons.append("200일선 아래")
    if best_rsi is not None and live_rsi > best_rsi:
        filter_reasons.append(f"RSI {live_rsi:.1f} > {best_rsi:.0f}")

    next_signal_price = (
        live_anchor * (1 - best_step * next_stage)
        if next_stage <= tranche_count
        else None
    )

    signal = "대기"
    detail = ""

    if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
        target_sell = avg_buy_price * (1 + best_tp)

        holding_days_now = (
            (pd.Timestamp(best_df.index[-1]) - live_first_buy_date).days
            if live_first_buy_date is not None else 0
        )

        if trade_price >= target_sell:
            signal = "익절"
            detail = (
                f"실제 매수 ETF 현재가가 익절 기준 "
                f"{currency}{target_sell:,.2f} 이상입니다."
            )
        elif best_max_hold is not None and holding_days_now >= best_max_hold:
            signal = "기간청산"
            detail = (
                f"첫 매수 후 {holding_days_now}일이 지나 "
                f"최대 보유기간 {best_max_hold}일에 도달했습니다. "
                "단기회전 규칙에 따라 전량매도 신호입니다."
            )
        elif next_stage <= tranche_count and live_dd >= best_step * next_stage:
            if filter_ok and loc_buy_fill_ok:
                signal = f"{next_stage}차 매수"
                detail = "가격 조건과 필터를 모두 통과했습니다."
            elif filter_ok and not loc_buy_fill_ok:
                detail = (
                    f"가격 신호는 충족했지만 LOC 한도 "
                    f"{currency}{loc_buy_limit_today:,.2f} 이하에서만 체결로 봅니다."
                )
            else:
                detail = "가격 조건은 충족했지만 필터 때문에 대기: " + ", ".join(filter_reasons)
        elif next_stage <= tranche_count:
            detail = (
                f"다음 {next_stage}차 신호가격: "
                f"{currency}{next_signal_price:,.2f}"
            )
        else:
            detail = f"분할매수를 모두 사용했습니다. +{best_tp:.0%} 익절을 기다립니다."
    else:
        first_signal_price = live_anchor * (1 - best_step)

        if live_dd >= best_step:
            if filter_ok and loc_buy_fill_ok:
                signal = "1차 매수"
                detail = "오늘 처음 시작 기준 1차 매수 조건과 필터를 통과했습니다."
            elif filter_ok and not loc_buy_fill_ok:
                detail = (
                    f"1차 가격 신호는 충족했지만 LOC 한도 "
                    f"{currency}{loc_buy_limit_today:,.2f} 이하에서만 체결로 봅니다."
                )
            else:
                detail = "가격은 1차 구간이지만 필터 때문에 대기: " + ", ".join(filter_reasons)
        else:
            detail = (
                f"오늘은 대기. 1차 신호가격은 약 "
                f"{currency}{first_signal_price:,.2f}"
            )

    a1, a2, a3 = st.columns(3)
    a1.metric("오늘 신호", signal)
    a2.metric("신호 ETF 가격", f"{currency}{signal_price:,.2f}")
    a3.metric("실제 매수 ETF 가격", f"{currency}{trade_price:,.2f}")

    st.write(f"**신호 기준:** {best_mode['mode']}")
    st.write(f"**실제 거래 종목:** {actual_trade_target}")
    if market.startswith("🇺🇸") and best_execution_mode == "LOC 모드":
        if loc_buy_limit_today is not None:
            st.write(
                f"**오늘 LOC 매수 한도:** {currency}{loc_buy_limit_today:,.2f} "
                f"(전일 종가 대비 -{best_loc_buy_offset:.2%})"
            )

            if "매수" in signal:
                st.success(
                    f"📌 **오늘 실제 주문:** TQQQ LOC 매수 "
                    f"{currency}{loc_buy_limit_today:,.2f} / "
                    f"주문금액 {currency}{tranche_budget:,.0f}"
                )
            elif signal in ["익절", "기간청산"]:
                st.success(
                    f"📌 **오늘 실제 주문:** TQQQ LOC 전량매도"
                )
            else:
                st.info("📌 **오늘 실제 주문:** 없음 — 대기")

        st.caption(
            "LOC 백테스트는 일봉 종가를 이용한 근사치입니다. 실제 LOC는 마감 경매에서 "
            "한도가격 조건을 만족할 때 체결되므로 실제 체결 여부와 가격은 달라질 수 있습니다."
        )
    allocation_text = " → ".join(
        f"{currency}{x:,.0f}" for x in live_tranche_budgets
    )
    st.write(f"**매수 비중:** {best_weight_mode}")
    st.caption(f"단계별 예정금액: {allocation_text}")
    st.write(
        f"**{min(next_stage, tranche_count)}차 예정 매수금액:** "
        f"{currency}{tranche_budget:,.0f}"
    )

    if signal.endswith("매수"):
        st.success(f"✅ 오늘 할 일: **{currency}{tranche_budget:,.0f} 매수**")
    elif signal in ["익절", "기간청산"]:
        st.success(
            "✅ 오늘 할 일: **실제 보유 ETF 전량매도**"
            if signal == "기간청산"
            else "✅ 오늘 할 일: **실제 보유 ETF 전량 익절**"
        )
    else:
        st.info("⏳ 오늘 할 일: **매수하지 않고 대기**")

    st.write(detail)

    # ------------------------------------------------------------
    # 전체 상위 전략
    # ------------------------------------------------------------
    st.divider()
    st.subheader("📊 전체 상위 전략")

    display = result.head(20).copy()
    display["최종자산"] = display["최종자산"].map(lambda x: f"{currency}{x:,.0f}")
    display["누적수익률"] = display["누적수익률"].map(lambda x: f"{x:.1%}")
    display["CAGR"] = display["CAGR"].map(lambda x: f"{x:.1%}")
    display["MDD"] = display["MDD"].map(lambda x: f"{x:.1%}")
    display["최대보유일"] = display["최대보유일"].map(lambda x: f"{x:.0f}일")
    display["매수간격"] = display["매수간격"].map(lambda x: f"{x:.0%}")
    display["익절률"] = display["익절률"].map(lambda x: f"{x:.0%}")
    display["승률"] = display["승률"].map(
        lambda x: f"{x:.1%}" if pd.notna(x) else "-"
    )

    st.dataframe(
        display[
            [
                "운용방식",
                "매수간격",
                "익절률",
                "매수비중",
                "필터",
                "체결방식",
                "최종자산",
                "CAGR",
                "MDD",
                "최대보유일",
                "기간청산",
                "완료매매",
                "승률",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("💰 1위 전략 성과")

    k1, k2 = st.columns(2)
    k1.metric("최종자산", f"{currency}{best['final_value']:,.0f}")
    k2.metric("누적수익률", f"{best['total_return']:.1%}")

    k3, k4 = st.columns(2)
    k3.metric("CAGR", f"{best['cagr']:.1%}")
    k4.metric("MDD", f"{best['mdd']:.1%}")

    k5, k6 = st.columns(2)
    k5.metric("최대 보유기간", f"{best['max_hold']:.0f}일")
    k6.metric("완료 매매", f"{best['trade_count']}회")

    st.line_chart(best["equity"][["Equity"]])

    st.caption(
        "교육·백테스트용입니다. 세금, 수수료, 슬리피지, 환율, "
        "추적오차와 레버리지 ETF의 일일 재조정 효과는 별도 반영하지 않습니다."
    )

except Exception as e:
    st.error("데이터를 불러오거나 백테스트하는 중 오류가 발생했습니다.")
    st.exception(e)
