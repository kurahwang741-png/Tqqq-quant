import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="TQQQ / 코코레 QUANT V5", page_icon="📈", layout="centered")

st.title("📈 TQQQ / 코코레 QUANT V5")
st.caption("실제 매매 흐름에 가깝게: 현금 대기 → 낙폭 분할매수 → 목표수익 익절 → 재대기")

market = st.radio("시장", ["🇺🇸 미국장 — TQQQ", "🇰🇷 한국장 — 코코레"], horizontal=True)

if market.startswith("🇺🇸"):
    asset = "TQQQ"
    ref = "QQQ"
    currency = "$"
    default_investment = 10000
    step_unit = "달러"
else:
    asset = "233740.KS"
    ref = "229200.KS"
    currency = "₩"
    default_investment = 10000000
    step_unit = "원"

investment = st.number_input(
    f"💰 초기 투자금액 ({step_unit})",
    min_value=100000 if currency == "₩" else 1000,
    value=default_investment,
    step=100000 if currency == "₩" else 1000,
)

st.info(
    "V5는 매일 목표비중을 재계산하는 방식이 아니라, 실제처럼 현금을 보유하다가 "
    "하락 구간에서 분할매수하고 목표수익률에 도달하면 전량 익절하는 방식으로 계산합니다."
)

with st.expander("⚙️ 백테스트 설정", expanded=False):
    anchor_mode = st.selectbox(
        "하락 기준점",
        ["최근 60거래일 고점", "최근 120거래일 고점", "역대 고점"],
        index=0,
        help="이 기준점에서 얼마나 하락했는지를 보고 분할매수합니다.",
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
    tranche_count = st.slider("분할매수 횟수", min_value=2, max_value=10, value=5, step=1)


@st.cache_data(ttl=900)
def get_data(asset, ref):
    x = yf.download([asset, ref], period="max", auto_adjust=True, progress=False)["Close"]
    if isinstance(x, pd.Series):
        x = x.to_frame()
    return x.dropna()


def anchor_series(s: pd.Series, mode: str) -> pd.Series:
    if mode == "최근 60거래일 고점":
        return s.rolling(60, min_periods=1).max()
    if mode == "최근 120거래일 고점":
        return s.rolling(120, min_periods=1).max()
    return s.cummax()


def simulate(prices: pd.Series, anchors: pd.Series, initial_cash: float, step_pct: float,
             take_profit: float, n_tranches: int):
    cash = float(initial_cash)
    shares = 0.0
    total_cost = 0.0
    entries = []
    trades = []
    equity_rows = []
    next_level = 1

    tranche_budget = initial_cash / n_tranches

    for dt, price in prices.items():
        if not np.isfinite(price) or price <= 0:
            continue

        anchor = float(anchors.loc[dt])
        drawdown = max(0.0, 1 - price / anchor) if anchor > 0 else 0.0

        # 포지션 보유 중 목표수익 도달 시 전량 익절
        if shares > 0:
            avg_price = total_cost / shares
            pnl_pct = price / avg_price - 1
            if pnl_pct >= take_profit:
                proceeds = shares * price
                realized = proceeds - total_cost
                cash += proceeds
                trades.append({
                    "매도일": dt,
                    "평균매수가": avg_price,
                    "매도가": price,
                    "수익률": pnl_pct,
                    "실현손익": realized,
                    "보유일수": (dt - entries[0][0]).days if entries else 0,
                    "매수횟수": len(entries),
                })
                shares = 0.0
                total_cost = 0.0
                entries = []
                next_level = 1

        # 현재 기준점 대비 step_pct, 2*step_pct... 하락 시 순차 매수
        # 같은 날 여러 단계가 동시에 충족되면 가능한 단계까지 한 번에 집행
        while shares >= 0 and next_level <= n_tranches and drawdown >= step_pct * next_level:
            budget = min(tranche_budget, cash)
            if budget <= 0:
                break
            qty = budget / price
            shares += qty
            total_cost += budget
            cash -= budget
            entries.append((dt, price, budget, next_level))
            next_level += 1

        equity = cash + shares * price
        equity_rows.append((dt, equity, cash, shares, price, drawdown))

    eq = pd.DataFrame(
        equity_rows,
        columns=["Date", "Equity", "Cash", "Shares", "Price", "Drawdown"],
    ).set_index("Date")

    final_value = float(eq["Equity"].iloc[-1]) if not eq.empty else initial_cash
    total_return = final_value / initial_cash - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25) if len(eq) > 1 else 1 / 365.25
    cagr = (final_value / initial_cash) ** (1 / years) - 1 if final_value > 0 else -1.0
    peak = eq["Equity"].cummax() if not eq.empty else pd.Series(dtype=float)
    mdd = float((eq["Equity"] / peak - 1).min()) if not eq.empty else 0.0

    trades_df = pd.DataFrame(trades)
    trade_count = len(trades_df)
    win_rate = float((trades_df["실현손익"] > 0).mean()) if trade_count else np.nan
    avg_hold = float(trades_df["보유일수"].mean()) if trade_count else np.nan

    # 미청산 포지션의 평가손익
    unrealized_pct = np.nan
    avg_entry = np.nan
    if shares > 0:
        avg_entry = total_cost / shares
        unrealized_pct = prices.iloc[-1] / avg_entry - 1

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


try:
    data = get_data(asset, ref)
    data = data.rename(columns={asset: "ASSET", ref: "REF"})
    data = data.dropna(subset=["ASSET", "REF"])
    data["ANCHOR"] = anchor_series(data["ASSET"], anchor_mode)
    data["DD"] = data["ASSET"] / data["ANCHOR"] - 1

    price = float(data["ASSET"].iloc[-1])
    dd_now = float(data["DD"].iloc[-1])

    c1, c2, c3 = st.columns(3)
    c1.metric(asset, f"{currency}{price:,.2f}")
    c2.metric("기준 고점 대비", f"{dd_now:.1%}")
    c3.metric("테스트 조합", f"{len(buy_steps) * len(take_profits)}개")

    if not buy_steps or not take_profits:
        st.warning("매수 간격과 익절률을 각각 하나 이상 선택해 주세요.")
        st.stop()

    st.divider()
    st.subheader("🏆 전략 조합 비교")

    results = []
    sims = {}

    for step_pct in buy_steps:
        for tp in take_profits:
            sim = simulate(
                data["ASSET"], data["ANCHOR"], float(investment),
                step_pct, tp, tranche_count
            )
            name = f"{step_pct:.0%} 간격 / {tp:.0%} 익절"
            sims[name] = sim
            results.append({
                "전략": name,
                "매수간격": step_pct,
                "익절률": tp,
                "최종자산": sim["final_value"],
                "누적수익률": sim["total_return"],
                "CAGR": sim["cagr"],
                "MDD": sim["mdd"],
                "완료매매": sim["trade_count"],
                "승률": sim["win_rate"],
                "평균보유일": sim["avg_hold"],
            })

    result = pd.DataFrame(results).sort_values(
        ["최종자산", "CAGR"], ascending=False
    ).reset_index(drop=True)

    winner = result.iloc[0]
    winner_name = winner["전략"]
    best = sims[winner_name]

    st.success(f"🥇 최종자산 1위: **{winner_name}**")

    display = result.copy()
    display["최종자산"] = display["최종자산"].map(lambda x: f"{currency}{x:,.0f}")
    display["누적수익률"] = display["누적수익률"].map(lambda x: f"{x:.1%}")
    display["CAGR"] = display["CAGR"].map(lambda x: f"{x:.1%}")
    display["MDD"] = display["MDD"].map(lambda x: f"{x:.1%}")
    display["승률"] = display["승률"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
    display["평균보유일"] = display["평균보유일"].map(lambda x: f"{x:.0f}일" if pd.notna(x) else "-")
    display["매수간격"] = display["매수간격"].map(lambda x: f"{x:.0%}")
    display["익절률"] = display["익절률"].map(lambda x: f"{x:.0%}")

    st.dataframe(display, use_container_width=True, hide_index=True)

    st.caption("※ 순위는 최종자산을 최우선으로 정렬합니다. MDD·승률은 참고용입니다.")

    st.divider()
    st.subheader("💰 1위 전략 성과")

    k1, k2 = st.columns(2)
    k1.metric("최종자산", f"{currency}{best['final_value']:,.0f}")
    k2.metric("총 수익금", f"{currency}{best['final_value'] - investment:,.0f}")

    k3, k4 = st.columns(2)
    k3.metric("누적수익률", f"{best['total_return']:.1%}")
    k4.metric("CAGR", f"{best['cagr']:.1%}")

    k5, k6 = st.columns(2)
    k5.metric("MDD", f"{best['mdd']:.1%}")
    k6.metric("완료 매매", f"{best['trade_count']}회")

    k7, k8 = st.columns(2)
    k7.metric("승률", f"{best['win_rate']:.1%}" if pd.notna(best['win_rate']) else "-")
    k8.metric("평균 보유기간", f"{best['avg_hold']:.0f}일" if pd.notna(best['avg_hold']) else "-")

    st.line_chart(best["equity"][["Equity"]])

    st.divider()
    st.subheader("📌 현재 매수 상태 / 다음 행동")

    best_step = float(winner["매수간격"])
    best_tp = float(winner["익절률"])
    current_anchor = float(data["ANCHOR"].iloc[-1])

    if best["shares"] > 0:
        position_value = best["shares"] * price
        target_sell_price = best["avg_entry"] * (1 + best_tp)

        st.write(f"**현재 포지션:** 보유 중")
        st.write(f"**평균 매수가:** {currency}{best['avg_entry']:,.2f}")
        st.write(f"**현재 평가액:** {currency}{position_value:,.0f}")
        st.write(f"**현재 평가수익률:** {best['unrealized_pct']:.1%}")
        st.write(f"**익절 목표가격:** {currency}{target_sell_price:,.2f}")

        if best["next_level"] <= tranche_count:
            next_buy_price = current_anchor * (1 - best_step * best["next_level"])
            next_budget = min(best["tranche_budget"], best["cash"])
            st.write(f"**다음 {best['next_level']}차 매수가격:** 약 {currency}{next_buy_price:,.2f}")
            st.write(f"**다음 매수금액:** 약 {currency}{next_budget:,.0f}")
        else:
            st.write("**추가 매수:** 계획된 분할매수를 모두 사용한 상태")
    else:
        first_buy_price = current_anchor * (1 - best_step)
        st.write("**현재 포지션:** 현금 대기")
        st.write(f"**1차 매수가격:** 약 {currency}{first_buy_price:,.2f}")
        st.write(f"**1회 매수금액:** 약 {currency}{best['tranche_budget']:,.0f}")
        st.write(f"**익절 기준:** 평균매수가 대비 +{best_tp:.0%}")

    st.caption(
        "현재 행동 값은 마지막 종가와 선택된 고점 기준으로 계산한 참고값입니다. "
        "실시간 장중 가격이나 주문 체결을 반영하지 않습니다."
    )

    if not best["trades"].empty:
        st.divider()
        st.subheader("🧾 최근 완료 매매")
        trades_view = best["trades"].tail(10).copy()
        trades_view["매도일"] = trades_view["매도일"].dt.strftime("%Y-%m-%d")
        trades_view["평균매수가"] = trades_view["평균매수가"].map(lambda x: f"{currency}{x:,.2f}")
        trades_view["매도가"] = trades_view["매도가"].map(lambda x: f"{currency}{x:,.2f}")
        trades_view["수익률"] = trades_view["수익률"].map(lambda x: f"{x:.1%}")
        trades_view["실현손익"] = trades_view["실현손익"].map(lambda x: f"{currency}{x:,.0f}")
        st.dataframe(trades_view, use_container_width=True, hide_index=True)

    st.divider()
    st.caption(
        "백테스트는 과거 종가 기준 단순 시뮬레이션입니다. 세금, 수수료, 슬리피지, 환율, "
        "장중 체결 차이와 레버리지 ETF의 구조적 특성은 별도로 반영하지 않았습니다."
    )

except Exception as e:
    st.error("데이터를 불러오거나 백테스트하는 중 오류가 발생했습니다.")
    st.exception(e)
