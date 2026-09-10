import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(
    page_title="TQQQ / 코코레 QUANT V6",
    page_icon="📈",
    layout="centered",
)

st.title("📈 TQQQ / 코코레 QUANT V6")
st.caption("일봉 전용 · 낙폭 분할매수 + 추세/RSI 필터 자동 비교 + 오늘의 매매 신호")

market = st.radio(
    "시장",
    ["🇺🇸 미국장 — TQQQ", "🇰🇷 한국장 — 코코레"],
    horizontal=True,
)

if market.startswith("🇺🇸"):
    asset = "TQQQ"
    ref = "QQQ"
    currency = "$"
    default_investment = 10000.0
    min_investment = 1000.0
    step_investment = 1000.0
    unit = "달러"
else:
    asset = "233740.KS"
    ref = "229200.KS"
    currency = "₩"
    default_investment = 10_000_000.0
    min_investment = 100_000.0
    step_investment = 100_000.0
    unit = "원"

investment = st.number_input(
    f"💰 초기 투자금액 ({unit})",
    min_value=min_investment,
    value=default_investment,
    step=step_investment,
)

st.info(
    "분봉은 사용하지 않습니다. 모든 백테스트와 매수·익절 판단은 일봉 종가 기준입니다."
)

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
        [0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
        default=[0.10, 0.15, 0.20],
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
    use_ma_candidates = st.checkbox("QQQ/코스닥150 200일선 필터 비교", value=True)
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

if "backtest_result" not in st.session_state:
    st.session_state.backtest_result = None
    st.session_state.backtest_sims = None
    st.session_state.backtest_params = None


@st.cache_data(ttl=900)
def get_data(asset_symbol: str, ref_symbol: str) -> pd.DataFrame:
    raw = yf.download(
        [asset_symbol, ref_symbol],
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
        close.columns = [asset_symbol]

    if isinstance(close, pd.Series):
        close = close.to_frame()

    if asset_symbol not in close.columns or ref_symbol not in close.columns:
        if len(close.columns) >= 2:
            close = close.iloc[:, :2]
            close.columns = [asset_symbol, ref_symbol]
        else:
            raise ValueError("두 종목의 가격 데이터를 모두 받지 못했습니다.")

    return close[[asset_symbol, ref_symbol]].dropna()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def anchor_series(s: pd.Series, mode: str) -> pd.Series:
    if mode == "최근 60거래일 고점":
        return s.rolling(60, min_periods=1).max()
    if mode == "최근 120거래일 고점":
        return s.rolling(120, min_periods=1).max()
    return s.cummax()


def entry_allowed(row, use_ma: bool, rsi_max):
    if use_ma and not bool(row["TREND_OK"]):
        return False

    if rsi_max is not None:
        rsi = row["RSI14"]
        if not np.isfinite(rsi) or rsi > rsi_max:
            return False

    return True


def simulate(
    df: pd.DataFrame,
    initial_cash: float,
    step_pct: float,
    take_profit: float,
    n_tranches: int,
    use_ma: bool,
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
        price = float(row["ASSET"])
        anchor = float(row["ANCHOR"])

        if not np.isfinite(price) or price <= 0:
            continue

        drawdown = max(0.0, 1 - price / anchor) if anchor > 0 else 0.0

        # 목표수익 도달 시 전량 익절
        if shares > 0:
            avg_price = total_cost / shares
            pnl_pct = price / avg_price - 1

            if pnl_pct >= take_profit:
                proceeds = shares * price
                realized = proceeds - total_cost
                cash += proceeds

                trades.append(
                    {
                        "매도일": pd.Timestamp(dt),
                        "평균매수가": avg_price,
                        "매도가": price,
                        "수익률": pnl_pct,
                        "실현손익": realized,
                        "보유일수": (
                            (pd.Timestamp(dt) - pd.Timestamp(entries[0][0])).days
                            if entries else 0
                        ),
                        "매수횟수": len(entries),
                    }
                )

                shares = 0.0
                total_cost = 0.0
                entries = []
                next_level = 1

        # 진입 필터를 통과할 때만 매수
        allowed = entry_allowed(row, use_ma, rsi_max)

        # 과거 백테스트는 충족된 단계까지 매수하되,
        # 실제 '오늘 신호'는 아래에서 한 번에 1차수만 안내
        if allowed:
            while next_level <= n_tranches and drawdown >= step_pct * next_level:
                budget = min(tranche_budget, cash)

                if budget <= 0:
                    break

                qty = budget / price
                shares += qty
                total_cost += budget
                cash -= budget
                entries.append((pd.Timestamp(dt), price, budget, next_level))
                next_level += 1

        equity = cash + shares * price
        equity_rows.append(
            (pd.Timestamp(dt), equity, cash, shares, price, drawdown)
        )

    if not equity_rows:
        raise ValueError("백테스트에 사용할 데이터가 없습니다.")

    eq = pd.DataFrame(
        equity_rows,
        columns=["Date", "Equity", "Cash", "Shares", "Price", "Drawdown"],
    ).set_index("Date")

    final_value = float(eq["Equity"].iloc[-1])
    total_return = final_value / initial_cash - 1

    years = max(
        (eq.index[-1] - eq.index[0]).days / 365.25,
        1 / 365.25,
    )

    cagr = (
        (final_value / initial_cash) ** (1 / years) - 1
        if final_value > 0 else -1.0
    )

    peak = eq["Equity"].cummax()
    mdd = float((eq["Equity"] / peak - 1).min())

    trades_df = pd.DataFrame(trades)
    trade_count = len(trades_df)

    win_rate = (
        float((trades_df["실현손익"] > 0).mean())
        if trade_count else np.nan
    )

    avg_hold = (
        float(trades_df["보유일수"].mean())
        if trade_count else np.nan
    )

    avg_entry = np.nan
    unrealized_pct = np.nan

    if shares > 0:
        avg_entry = total_cost / shares
        unrealized_pct = float(df["ASSET"].iloc[-1]) / avg_entry - 1

    return {
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "mdd": mdd,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_hold": avg_hold,
        "equity": eq,
        "trades": trades_df,
        "cash": cash,
        "shares": shares,
        "avg_entry": avg_entry,
        "unrealized_pct": unrealized_pct,
        "next_level": next_level,
        "tranche_budget": tranche_budget,
    }


def filter_name(use_ma: bool, rsi_max):
    parts = []

    if use_ma:
        parts.append("200일선")
    if rsi_max is not None:
        parts.append(f"RSI≤{int(rsi_max)}")

    return "기본" if not parts else " + ".join(parts)


try:
    with st.spinner("가격 데이터를 불러오는 중..."):
        data = get_data(asset, ref)

    data = data.rename(columns={asset: "ASSET", ref: "REF"})
    data = data.dropna(subset=["ASSET", "REF"])

    data["ANCHOR"] = anchor_series(data["ASSET"], anchor_mode)
    data["DD"] = data["ASSET"] / data["ANCHOR"] - 1
    data["MA200"] = data["REF"].rolling(200).mean()
    data["RSI14"] = calc_rsi(data["REF"], 14)
    data["TREND_OK"] = data["REF"] > data["MA200"]

    # 200일선이 계산 가능한 시점부터 비교
    data = data.dropna(subset=["MA200", "RSI14"]).copy()

    if data.empty:
        raise ValueError("지표 계산 후 사용할 데이터가 없습니다.")

    price = float(data["ASSET"].iloc[-1])
    dd_now = float(data["DD"].iloc[-1])
    rsi_now = float(data["RSI14"].iloc[-1])
    trend_ok_now = bool(data["TREND_OK"].iloc[-1])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(asset, f"{currency}{price:,.2f}")
    c2.metric("고점 대비", f"{dd_now:.1%}")
    c3.metric(f"{ref} RSI", f"{rsi_now:.1f}")
    c4.metric("200일선", "위" if trend_ok_now else "아래")

    if not buy_steps or not take_profits:
        st.warning("매수 간격과 익절률을 각각 하나 이상 선택해 주세요.")
        st.stop()

    filter_candidates = [(False, None)]

    if use_ma_candidates:
        filter_candidates.append((True, None))

    if use_rsi_candidates:
        for rsi_max in rsi_thresholds:
            filter_candidates.append((False, float(rsi_max)))

            if use_ma_candidates:
                filter_candidates.append((True, float(rsi_max)))

    # 중복 제거
    seen = set()
    unique_candidates = []
    for item in filter_candidates:
        if item not in seen:
            unique_candidates.append(item)
            seen.add(item)

    st.divider()
    st.subheader("🏆 전략 + 필터 자동 비교")

    current_params = (
        tuple(buy_steps),
        tuple(take_profits),
        tranche_count,
        anchor_mode,
        use_ma_candidates,
        use_rsi_candidates,
        tuple(rsi_thresholds),
        asset,
        ref,
        float(investment),
    )

    if run_backtest:
        results = []
        sims = {}

        progress = st.progress(0)
        status = st.empty()

        total_jobs = max(
            1,
            len(buy_steps) * len(take_profits) * len(unique_candidates)
        )
        done_jobs = 0

        with st.spinner("백테스트 계산 중..."):
            for step_pct in buy_steps:
                for tp in take_profits:
                    for use_ma, rsi_max in unique_candidates:
                        sim = simulate(
                            data,
                            float(investment),
                            step_pct,
                            tp,
                            tranche_count,
                            use_ma,
                            rsi_max,
                        )

                        fname = filter_name(use_ma, rsi_max)
                        name = f"{step_pct:.0%} 간격 / {tp:.0%} 익절 / {fname}"

                        sims[name] = sim
                        results.append(
                            {
                                "전략": name,
                                "매수간격": step_pct,
                                "익절률": tp,
                                "필터": fname,
                                "MA200": use_ma,
                                "RSI상한": rsi_max,
                                "최종자산": sim["final_value"],
                                "누적수익률": sim["total_return"],
                                "CAGR": sim["cagr"],
                                "MDD": sim["mdd"],
                                "완료매매": sim["trade_count"],
                                "승률": sim["win_rate"],
                                "평균보유일": sim["avg_hold"],
                            }
                        )

                        done_jobs += 1
                        progress.progress(done_jobs / total_jobs)
                        status.caption(
                            f"계산 중... {done_jobs}/{total_jobs}"
                        )

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
        st.warning(
            "설정을 변경했습니다. 새 조건으로 보려면 **🚀 백테스트 실행**을 다시 눌러 주세요."
        )

    winner = result.iloc[0]
    winner_name = winner["전략"]
    best = sims[winner_name]

    best_step = float(winner["매수간격"])
    best_tp = float(winner["익절률"])
    best_use_ma = bool(winner["MA200"])
    best_rsi = (
        None if pd.isna(winner["RSI상한"])
        else float(winner["RSI상한"])
    )

    st.success(f"🥇 최종자산 1위: **{winner_name}**")

    # ------------------------------------------------------------
    # 오늘부터 실제로 시작하는 사용자를 위한 매매 신호
    # ------------------------------------------------------------
    st.divider()
    st.subheader("🚦 오늘의 매매 신호")
    st.caption(
        "과거 백테스트 포지션을 이어받지 않습니다. "
        "오늘부터 새로 시작하는 기준으로 한 번에 1차수씩만 안내합니다."
    )

    live_anchor = float(data["ANCHOR"].iloc[-1])
    live_drawdown = max(0.0, 1 - price / live_anchor)
    live_tranche_budget = float(investment) / tranche_count

    live_stage = st.number_input(
        "현재 실제 보유 차수",
        min_value=0,
        max_value=tranche_count,
        value=0,
        step=1,
        help="오늘 처음 시작이면 0. 이미 1번 매수했다면 1, 두 번 매수했다면 2를 선택하세요.",
    )

    avg_buy_price = None

    if live_stage > 0:
        avg_buy_price = st.number_input(
            f"현재 평균 매수가 ({unit})",
            min_value=0.0,
            value=float(price),
            step=1.0 if currency == "$" else 100.0,
            help="실제 계좌 평균 매수가를 입력하면 익절 신호까지 계산합니다.",
        )

    latest_row = data.iloc[-1]
    live_filter_ok = entry_allowed(
        latest_row,
        best_use_ma,
        best_rsi,
    )

    next_stage = int(live_stage) + 1

    next_buy_price = (
        live_anchor * (1 - best_step * next_stage)
        if next_stage <= tranche_count else None
    )

    signal = "대기"
    signal_detail = ""
    signal_amount = 0.0

    filter_reasons = []

    if best_use_ma and not trend_ok_now:
        filter_reasons.append("기준지수가 200일선 아래")

    if best_rsi is not None and rsi_now > best_rsi:
        filter_reasons.append(f"RSI {rsi_now:.1f} > {best_rsi:.0f}")

    if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
        target_sell_price = avg_buy_price * (1 + best_tp)

        if price >= target_sell_price:
            signal = "익절"
            signal_detail = (
                f"현재가가 익절 기준 {currency}{target_sell_price:,.2f} 이상입니다. "
                "보유분 전량 익절 신호입니다."
            )

        elif next_stage <= tranche_count and live_drawdown >= best_step * next_stage:
            if live_filter_ok:
                signal = f"{next_stage}차 매수"
                signal_amount = live_tranche_budget
                signal_detail = (
                    f"낙폭 {live_drawdown:.1%}가 {next_stage}차 기준 "
                    f"{best_step * next_stage:.1%}에 도달했고 필터도 통과했습니다."
                )
            else:
                signal_detail = (
                    f"가격 조건은 {next_stage}차 매수 구간이지만 "
                    f"필터 때문에 대기합니다: {', '.join(filter_reasons)}"
                )

        elif next_stage <= tranche_count:
            signal_detail = (
                f"다음 {next_stage}차 매수 기준은 약 "
                f"{currency}{next_buy_price:,.2f}입니다."
            )

        else:
            signal_detail = (
                "계획한 분할매수를 모두 사용한 상태입니다. "
                f"평균매수가 대비 +{best_tp:.0%} 익절을 기다립니다."
            )

    else:
        first_buy_price = live_anchor * (1 - best_step)

        if live_drawdown >= best_step:
            if live_filter_ok:
                signal = "1차 매수"
                signal_amount = live_tranche_budget
                signal_detail = (
                    "오늘 처음 시작 기준으로 1차 매수 조건에 도달했고 "
                    "필터도 통과했습니다. 과거에 여러 단계가 이미 충족됐더라도 "
                    "오늘은 1차 금액만 매수합니다."
                )
            else:
                signal_detail = (
                    "가격은 1차 매수 구간이지만 필터 때문에 대기합니다: "
                    + ", ".join(filter_reasons)
                )
        else:
            signal_detail = (
                f"오늘은 대기입니다. 1차 매수 기준 가격은 약 "
                f"{currency}{first_buy_price:,.2f}입니다."
            )

    s1, s2, s3 = st.columns(3)
    s1.metric("오늘 신호", signal)
    s2.metric("현재가", f"{currency}{price:,.2f}")
    s3.metric("1회 매수금액", f"{currency}{live_tranche_budget:,.0f}")

    if signal_amount > 0:
        st.success(
            f"✅ 오늘 할 일: **{signal} — 약 {currency}{signal_amount:,.0f} 매수**"
        )
    elif signal == "익절":
        st.success("✅ 오늘 할 일: **보유분 전량 익절**")
    else:
        st.info("⏳ 오늘 할 일: **매수하지 않고 대기**")

    st.write(signal_detail)

    st.write(
        f"**적용 전략:** {best_step:.0%} 간격 / {best_tp:.0%} 익절 / "
        f"{tranche_count}분할 / {filter_name(best_use_ma, best_rsi)}"
    )

    st.caption(
        "장중 실시간 신호가 아니라 최근 일봉 종가 기준입니다. "
        "실제 주문 전에는 현재 시장가격을 다시 확인하세요."
    )

    st.divider()
    st.subheader("📊 상위 전략 비교")

    top_n = min(20, len(result))
    display = result.head(top_n).copy()

    display["최종자산"] = display["최종자산"].map(
        lambda x: f"{currency}{x:,.0f}"
    )
    display["누적수익률"] = display["누적수익률"].map(
        lambda x: f"{x:.1%}"
    )
    display["CAGR"] = display["CAGR"].map(lambda x: f"{x:.1%}")
    display["MDD"] = display["MDD"].map(lambda x: f"{x:.1%}")
    display["승률"] = display["승률"].map(
        lambda x: f"{x:.1%}" if pd.notna(x) else "-"
    )
    display["평균보유일"] = display["평균보유일"].map(
        lambda x: f"{x:.0f}일" if pd.notna(x) else "-"
    )
    display["매수간격"] = display["매수간격"].map(lambda x: f"{x:.0%}")
    display["익절률"] = display["익절률"].map(lambda x: f"{x:.0%}")

    display = display[
        [
            "전략",
            "최종자산",
            "누적수익률",
            "CAGR",
            "MDD",
            "완료매매",
            "승률",
            "평균보유일",
        ]
    ]

    st.dataframe(display, use_container_width=True, hide_index=True)

    st.caption(
        "순위는 최종자산을 가장 우선해서 정렬합니다. "
        "설정을 바꿔도 자동 재계산하지 않으므로, 비교할 때만 실행 버튼을 누르면 됩니다."
    )

    st.divider()
    st.subheader("💰 1위 전략 성과")

    k1, k2 = st.columns(2)
    k1.metric("최종자산", f"{currency}{best['final_value']:,.0f}")
    k2.metric(
        "총 수익금",
        f"{currency}{best['final_value'] - float(investment):,.0f}",
    )

    k3, k4 = st.columns(2)
    k3.metric("누적수익률", f"{best['total_return']:.1%}")
    k4.metric("CAGR", f"{best['cagr']:.1%}")

    k5, k6 = st.columns(2)
    k5.metric("MDD", f"{best['mdd']:.1%}")
    k6.metric("완료 매매", f"{best['trade_count']}회")

    k7, k8 = st.columns(2)
    k7.metric(
        "승률",
        f"{best['win_rate']:.1%}" if pd.notna(best["win_rate"]) else "-",
    )
    k8.metric(
        "평균 보유기간",
        f"{best['avg_hold']:.0f}일" if pd.notna(best["avg_hold"]) else "-",
    )

    st.line_chart(best["equity"][["Equity"]])

    st.divider()
    st.subheader("🧠 현재 필터 상태")

    f1, f2 = st.columns(2)
    f1.metric(
        "200일선 필터",
        "통과" if (not best_use_ma or trend_ok_now) else "차단",
    )

    if best_rsi is None:
        f2.metric("RSI 필터", "미사용")
    else:
        f2.metric(
            "RSI 필터",
            "통과" if rsi_now <= best_rsi else "차단",
            delta=f"현재 {rsi_now:.1f} / 기준 {best_rsi:.0f}",
        )

    if not best["trades"].empty:
        st.divider()
        st.subheader("🧾 최근 완료 매매")

        trades_view = best["trades"].tail(10).copy()
        trades_view["매도일"] = pd.to_datetime(
            trades_view["매도일"]
        ).dt.strftime("%Y-%m-%d")
        trades_view["평균매수가"] = trades_view["평균매수가"].map(
            lambda x: f"{currency}{x:,.2f}"
        )
        trades_view["매도가"] = trades_view["매도가"].map(
            lambda x: f"{currency}{x:,.2f}"
        )
        trades_view["수익률"] = trades_view["수익률"].map(
            lambda x: f"{x:.1%}"
        )
        trades_view["실현손익"] = trades_view["실현손익"].map(
            lambda x: f"{currency}{x:,.0f}"
        )

        st.dataframe(
            trades_view,
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.caption(
        "교육·백테스트용입니다. 세금, 수수료, 슬리피지, 환율, "
        "장중 체결 차이와 레버리지 ETF의 구조적 특성은 별도로 반영하지 않습니다."
    )

except Exception as e:
    st.error("데이터를 불러오거나 백테스트하는 중 오류가 발생했습니다.")
    st.exception(e)
