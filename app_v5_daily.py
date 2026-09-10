import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(
    page_title="TQQQ / 코코레 QUANT V7",
    page_icon="📈",
    layout="centered",
)

st.title("📈 TQQQ / 코코레 QUANT V7")
st.caption("일봉 전용 · 신호 ETF와 실제 매수 ETF 분리 비교 + 추세/RSI 필터 + 오늘의 신호")

market = st.radio(
    "시장",
    ["🇺🇸 미국장 — TQQQ", "🇰🇷 한국장 — 코코레"],
    horizontal=True,
)

if market.startswith("🇺🇸"):
    currency = "$"
    unit = "달러"
    default_investment = 10000.0
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


def simulate(
    df,
    initial_cash,
    step_pct,
    take_profit,
    n_tranches,
    use_ma,
    rsi_max,
):
    cash = float(initial_cash)
    shares = 0.0
    total_cost = 0.0
    entries = []
    trades = []
    equity_rows = []
    next_level = 1
    tranche_budget = initial_cash / n_tranches

    for dt, row in df.iterrows():
        signal_price = float(row["SIGNAL"])
        trade_price = float(row["TRADE"])
        anchor = float(row["ANCHOR"])

        if (
            not np.isfinite(signal_price)
            or not np.isfinite(trade_price)
            or signal_price <= 0
            or trade_price <= 0
            or anchor <= 0
        ):
            continue

        drawdown = max(0.0, 1 - signal_price / anchor)

        # 익절은 실제 매수한 ETF의 평균단가 기준
        if shares > 0:
            avg_price = total_cost / shares
            pnl_pct = trade_price / avg_price - 1

            if pnl_pct >= take_profit:
                proceeds = shares * trade_price
                realized = proceeds - total_cost
                first_buy_date = entries[0][0] if entries else dt

                cash += proceeds

                trades.append(
                    {
                        "매도일": pd.Timestamp(dt),
                        "평균매수가": avg_price,
                        "매도가": trade_price,
                        "수익률": pnl_pct,
                        "실현손익": realized,
                        "보유일수": (pd.Timestamp(dt) - pd.Timestamp(first_buy_date)).days,
                        "매수횟수": len(entries),
                    }
                )

                shares = 0.0
                total_cost = 0.0
                entries = []
                next_level = 1

        allowed = entry_allowed(row, use_ma, rsi_max)

        if allowed:
            while next_level <= n_tranches and drawdown >= step_pct * next_level:
                budget = min(tranche_budget, cash)
                if budget <= 0:
                    break

                qty = budget / trade_price
                shares += qty
                total_cost += budget
                cash -= budget
                entries.append(
                    (pd.Timestamp(dt), trade_price, budget, next_level)
                )
                next_level += 1

        equity = cash + shares * trade_price
        equity_rows.append(
            (
                pd.Timestamp(dt),
                equity,
                cash,
                shares,
                signal_price,
                trade_price,
                drawdown,
            )
        )

    if not equity_rows:
        raise ValueError("백테스트에 사용할 데이터가 없습니다.")

    eq = pd.DataFrame(
        equity_rows,
        columns=[
            "Date",
            "Equity",
            "Cash",
            "Shares",
            "SignalPrice",
            "TradePrice",
            "Drawdown",
        ],
    ).set_index("Date")

    final_value = float(eq["Equity"].iloc[-1])
    total_return = final_value / initial_cash - 1

    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    cagr = (
        (final_value / initial_cash) ** (1 / years) - 1
        if final_value > 0
        else -1.0
    )

    peak = eq["Equity"].cummax()
    mdd = float((eq["Equity"] / peak - 1).min())

    trades_df = pd.DataFrame(trades)
    trade_count = len(trades_df)

    win_rate = (
        float((trades_df["실현손익"] > 0).mean())
        if trade_count
        else np.nan
    )

    avg_hold = (
        float(trades_df["보유일수"].mean())
        if trade_count
        else np.nan
    )

    closed_max_hold = (
        float(trades_df["보유일수"].max())
        if trade_count
        else 0.0
    )

    ongoing_hold = 0.0
    avg_entry = np.nan
    unrealized_pct = np.nan

    if shares > 0 and entries:
        avg_entry = total_cost / shares
        current_trade_price = float(df["TRADE"].iloc[-1])
        unrealized_pct = current_trade_price / avg_entry - 1
        ongoing_hold = (
            pd.Timestamp(df.index[-1]) - pd.Timestamp(entries[0][0])
        ).days

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
        "equity": eq,
        "trades": trades_df,
        "cash": cash,
        "shares": shares,
        "avg_entry": avg_entry,
        "unrealized_pct": unrealized_pct,
        "next_level": next_level,
        "tranche_budget": tranche_budget,
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

    current_params = (
        market,
        float(investment),
        anchor_mode,
        tuple(buy_steps),
        tuple(take_profits),
        tranche_count,
        use_ma_candidates,
        use_rsi_candidates,
        tuple(rsi_thresholds),
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

    if not buy_steps or not take_profits:
        st.warning("매수 간격과 익절률을 각각 하나 이상 선택해 주세요.")
        st.stop()

    st.divider()
    st.subheader("🏆 전략 자동 비교")

    if run_backtest:
        results = []
        sims = {}

        total_jobs = (
            len(modes)
            * len(buy_steps)
            * len(take_profits)
            * len(filter_candidates)
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

                for step_pct in buy_steps:
                    for tp in take_profits:
                        for use_ma, rsi_max in filter_candidates:
                            sim = simulate(
                                df,
                                float(investment),
                                step_pct,
                                tp,
                                tranche_count,
                                use_ma,
                                rsi_max,
                            )

                            fname = filter_name(use_ma, rsi_max)
                            strategy_name = (
                                f"{mode['mode']} | "
                                f"{step_pct:.0%} 간격 / {tp:.0%} 익절 / {fname}"
                            )

                            sims[strategy_name] = {
                                "sim": sim,
                                "mode": mode,
                                "df": df,
                                "step_pct": step_pct,
                                "tp": tp,
                                "use_ma": use_ma,
                                "rsi_max": rsi_max,
                            }

                            results.append(
                                {
                                    "전략": strategy_name,
                                    "운용방식": mode["mode"],
                                    "매수간격": step_pct,
                                    "익절률": tp,
                                    "필터": fname,
                                    "최종자산": sim["final_value"],
                                    "누적수익률": sim["total_return"],
                                    "CAGR": sim["cagr"],
                                    "MDD": sim["mdd"],
                                    "최대보유일": sim["max_hold"],
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

    st.success(f"🥇 최종자산 1위: **{winner['운용방식']}**")
    st.write(
        f"**{best_step:.0%} 간격 / {best_tp:.0%} 익절 / "
        f"{filter_name(best_use_ma, best_rsi)}**"
    )

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
    # 오늘의 실제 매매 신호
    # ------------------------------------------------------------
    st.divider()
    st.subheader("🚦 오늘의 매매 신호")
    st.caption(
        "1위 전략 기준입니다. 오늘 처음 시작하면 과거 여러 차수가 이미 충족돼도 "
        "한 번에 1차수만 안내합니다."
    )

    live_stage = st.number_input(
        "현재 실제 보유 차수",
        min_value=0,
        max_value=tranche_count,
        value=0,
        step=1,
        help="오늘 처음 시작이면 0",
    )

    latest = best_df.iloc[-1]
    signal_price = float(latest["SIGNAL"])
    trade_price = float(latest["TRADE"])
    live_anchor = float(latest["ANCHOR"])
    live_dd = max(0.0, 1 - signal_price / live_anchor)

    live_rsi = float(latest["RSI14"])
    live_trend_ok = bool(latest["TREND_OK"])
    tranche_budget = float(investment) / tranche_count

    avg_buy_price = None
    if live_stage > 0:
        avg_buy_price = st.number_input(
            f"현재 실제 매수 ETF 평균단가 ({unit})",
            min_value=0.0,
            value=float(trade_price),
            step=1.0 if currency == "$" else 10.0,
        )

    filter_ok = entry_allowed(latest, best_use_ma, best_rsi)

    filter_reasons = []
    if best_use_ma and not live_trend_ok:
        filter_reasons.append("200일선 아래")
    if best_rsi is not None and live_rsi > best_rsi:
        filter_reasons.append(f"RSI {live_rsi:.1f} > {best_rsi:.0f}")

    next_stage = int(live_stage) + 1
    next_signal_price = (
        live_anchor * (1 - best_step * next_stage)
        if next_stage <= tranche_count
        else None
    )

    signal = "대기"
    detail = ""

    if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
        target_sell = avg_buy_price * (1 + best_tp)

        if trade_price >= target_sell:
            signal = "익절"
            detail = (
                f"실제 매수 ETF 현재가가 익절 기준 "
                f"{currency}{target_sell:,.2f} 이상입니다."
            )
        elif next_stage <= tranche_count and live_dd >= best_step * next_stage:
            if filter_ok:
                signal = f"{next_stage}차 매수"
                detail = "가격 조건과 필터를 모두 통과했습니다."
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
            if filter_ok:
                signal = "1차 매수"
                detail = "오늘 처음 시작 기준 1차 매수 조건과 필터를 통과했습니다."
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
    st.write(f"**1회 매수금액:** {currency}{tranche_budget:,.0f}")

    if signal.endswith("매수"):
        st.success(f"✅ 오늘 할 일: **{currency}{tranche_budget:,.0f} 매수**")
    elif signal == "익절":
        st.success("✅ 오늘 할 일: **실제 보유 ETF 전량 익절**")
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
    display["승률"] = display["승률"].map(
        lambda x: f"{x:.1%}" if pd.notna(x) else "-"
    )

    st.dataframe(
        display[
            [
                "운용방식",
                "매수간격",
                "익절률",
                "필터",
                "최종자산",
                "CAGR",
                "MDD",
                "최대보유일",
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
