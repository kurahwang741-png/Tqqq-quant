import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="TQQQ / 코코레 QUANT V4", page_icon="📈", layout="centered")

st.title("📈 TQQQ / 코코레 QUANT V4")
st.caption("목표: 위험 최소화보다 '최종 수익률/CAGR이 가장 높은 전략' 찾기")

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
    step=100000 if currency == "₩" else 1000
)

st.info("이번 버전은 '어떤 전략이 가장 많이 벌었나?'를 최우선으로 비교합니다.")

@st.cache_data(ttl=900)
def get_data(asset, ref):
    x = yf.download([asset, ref], period="max", auto_adjust=True, progress=False)["Close"]
    if isinstance(x, pd.Series):
        x = x.to_frame()
    return x.dropna()

def calc_rsi(s, n=14):
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100/(1+rs)

try:
    data = get_data(asset, ref)
    data = data.rename(columns={asset: "ASSET", ref: "REF"})
    data["MA200"] = data["REF"].rolling(200).mean()
    data["RSI14"] = calc_rsi(data["REF"])
    data["PEAK"] = data["ASSET"].cummax()
    data["DD"] = data["ASSET"] / data["PEAK"] - 1
    data = data.dropna(subset=["MA200", "RSI14"])

    price = float(data["ASSET"].iloc[-1])
    ref_price = float(data["REF"].iloc[-1])
    dd_now = float(data["DD"].iloc[-1])
    rsi_now = float(data["RSI14"].iloc[-1])

    c1,c2,c3 = st.columns(3)
    c1.metric(asset, f"{currency}{price:,.2f}")
    c2.metric("고점 대비", f"{dd_now:.1%}")
    c3.metric(f"{ref} RSI", f"{rsi_now:.1f}")

    st.divider()
    st.subheader("🏆 전략 비교")

    # 같은 기간, 같은 초기자금으로 공정하게 비교
    # 신호는 다음 거래일에 적용하여 look-ahead bias를 줄임
    ret = data["ASSET"].pct_change().fillna(0)

    strategies = {}

    # 0. 단순 보유
    strategies["① 단순보유"] = pd.Series(1.0, index=data.index)

    # 낙폭 분할매수: 5/7/10%마다 목표비중 20%씩 증가
    for pct, label in [(0.05,"② 5% 낙폭"), (0.07,"③ 7% 낙폭"), (0.10,"④ 10% 낙폭")]:
        level = np.floor(np.maximum(0, -data["DD"]) / pct)
        strategies[label] = np.minimum(level * 0.20, 1.0)

    # 낙폭 + 추세
    level = np.floor(np.maximum(0, -data["DD"]) / 0.05)
    w = np.minimum(level * 0.20, 1.0)
    w[data["REF"] <= data["MA200"]] = 0
    strategies["⑤ 5% + 200일선"] = w

    # 낙폭 + RSI
    w = np.minimum(level * 0.20, 1.0)
    w[data["RSI14"] >= 75] *= 0.5
    strategies["⑥ 5% + RSI"] = w

    # 낙폭 + 200MA + RSI
    w = np.minimum(level * 0.20, 1.0)
    w[data["REF"] <= data["MA200"]] = 0
    w[data["RSI14"] >= 75] *= 0.5
    strategies["⑦ 5% + 200일선 + RSI"] = w

    rows = []
    curves = {}

    for name, weight in strategies.items():
        strat_ret = weight.shift(1).fillna(0) * ret
        value = investment * (1 + strat_ret).cumprod()
        years = max((value.index[-1] - value.index[0]).days / 365.25, 1/365.25)
        final_value = float(value.iloc[-1])
        total_return = final_value / investment - 1
        cagr = (final_value / investment) ** (1/years) - 1
        peak = value.cummax()
        mdd = (value / peak - 1).min()
        daily_std = strat_ret.std()
        sharpe = (strat_ret.mean() / daily_std * np.sqrt(252)) if daily_std > 0 else np.nan

        rows.append({
            "전략": name,
            "최종금액": final_value,
            "누적수익률": total_return,
            "CAGR": cagr,
            "MDD": mdd,
            "Sharpe": sharpe
        })
        curves[name] = value

    result = pd.DataFrame(rows).sort_values("CAGR", ascending=False).reset_index(drop=True)

    st.success(f"🥇 현재 데이터 기준 CAGR 1위: **{result.iloc[0]['전략']}**")

    display = result.copy()
    display["최종금액"] = display["최종금액"].map(lambda x: f"{currency}{x:,.0f}")
    display["누적수익률"] = display["누적수익률"].map(lambda x: f"{x:.1%}")
    display["CAGR"] = display["CAGR"].map(lambda x: f"{x:.1%}")
    display["MDD"] = display["MDD"].map(lambda x: f"{x:.1%}")
    display["Sharpe"] = display["Sharpe"].map(lambda x: f"{x:.2f}" if pd.notna(x) else "-")

    st.dataframe(display, use_container_width=True, hide_index=True)

    st.caption("※ 1순위는 CAGR/최종금액입니다. MDD·Sharpe는 참고 지표입니다.")

    st.divider()
    st.subheader("💰 최종금액 비교")

    top_names = result["전략"].tolist()
    chart_df = pd.DataFrame({name: curves[name] for name in top_names})
    st.line_chart(chart_df)

    st.divider()
    st.subheader("🔎 수익률 1위 전략 상세")

    winner = result.iloc[0]
    st.write(f"**전략:** {winner['전략']}")
    st.write(f"**최종금액:** {currency}{winner['최종금액']:,.0f}")
    st.write(f"**누적수익률:** {winner['누적수익률']:.1%}")
    st.write(f"**CAGR:** {winner['CAGR']:.1%}")
    st.write(f"**MDD:** {winner['MDD']:.1%}")

    st.divider()
    st.subheader("📌 현재 매수 신호")

    current_weight = float(strategies[winner["전략"]].iloc[-1])
    target = investment * current_weight

    st.metric("현재 목표 투자비중", f"{current_weight:.0%}")
    st.metric("현재 목표 투자금액", f"{currency}{target:,.0f}")

    st.caption(
        "중요: 백테스트는 과거 데이터에 대한 검증입니다. 가장 수익률이 높았던 전략이 "
        "미래에도 가장 높다는 보장은 없습니다. 실제 주문은 자동 실행하지 않습니다."
    )

except Exception as e:
    st.error("데이터를 불러오지 못했습니다. 잠시 후 다시 실행해 주세요.")
    st.exception(e)
