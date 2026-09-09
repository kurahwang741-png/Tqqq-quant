import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

st.set_page_config(page_title="TQQQ QUANT", page_icon="📊", layout="centered")

st.title("📊 TQQQ QUANT")
st.caption("TQQQ 분할매수 전략을 계산·검증하는 교육용 도구")

investment = st.number_input("💰 투자금액 (원)", min_value=100000, value=10000000, step=100000)

strategy = st.selectbox(
    "📌 매매 전략",
    ["5% 낙폭 분할매수", "7% 낙폭 분할매수", "10% 낙폭 분할매수"]
)

@st.cache_data(ttl=900)
def get_data():
    d = yf.download(["TQQQ","QQQ"], period="max", auto_adjust=True, progress=False)["Close"]
    return d.dropna()

def calc_rsi(s, n=14):
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100/(1+rs)

try:
    data = get_data()
    data["MA200"] = data["QQQ"].rolling(200).mean()
    data["RSI"] = calc_rsi(data["QQQ"])
    data["PEAK"] = data["TQQQ"].cummax()
    data["DD"] = data["TQQQ"] / data["PEAK"] - 1

    x = data.iloc[-1]
    tqqq = float(x["TQQQ"])
    qqq = float(x["QQQ"])
    ma200 = float(x["MA200"])
    rsi = float(x["RSI"])
    dd = float(x["DD"])

    c1, c2 = st.columns(2)
    c1.metric("TQQQ", f"${tqqq:,.2f}")
    c2.metric("고점 대비", f"{dd:.1%}")

    c3, c4 = st.columns(2)
    c3.metric("QQQ", f"${qqq:,.2f}")
    c4.metric("QQQ RSI", f"{rsi:.1f}")

    trend = qqq > ma200
    st.info("🟢 QQQ 200일선 위 — 상승 추세" if trend else "🔴 QQQ 200일선 아래 — 매수 중단 구간")

    step = {"5% 낙폭 분할매수": 0.05, "7% 낙폭 분할매수": 0.07, "10% 낙폭 분할매수": 0.10}[strategy]
    level = int(max(0, np.floor(abs(dd) / step)))
    # 단계별 목표 비중: 1단계 20%, 2단계 40% ... 최대 100%
    weight = min(level * 0.20, 1.0) if dd <= -step else 0.0
    if not trend:
        weight = 0.0
    if rsi >= 75:
        weight *= 0.5

    buy_amount = investment * weight
    next_level = -(level + 1) * step
    next_price = tqqq * (1 + (next_level - dd)) if level >= 0 else tqqq * (1-step)

    st.divider()
    st.subheader("🎯 현재 판단")

    if not trend:
        st.error("🔴 매수 중단 — QQQ가 200일선 아래입니다.")
    elif weight == 0:
        st.warning(f"🔵 관망 — 다음 매수 기준은 고점 대비 {step:.0%} 이상 하락입니다.")
    elif weight <= 0.4:
        st.success(f"🟢 1차/초기 분할매수 구간")
    else:
        st.success(f"🟢 추가 분할매수 구간")

    a,b = st.columns(2)
    a.metric("현재 목표 투자비중", f"{weight:.0%}")
    b.metric("계산상 매수금액", f"{buy_amount:,.0f}원")

    st.write(f"**다음 단계 기준:** 고점 대비 약 {next_level:.0%}")
    st.caption(f"다음 기준 가격은 단순 계산값이며 실제 주문가격을 의미하지 않습니다.")

    st.divider()
    st.subheader("📈 간단 백테스트")

    # 선택 전략의 낙폭 단계 전략을 과거에 적용
    d = data.copy()
    d["trend"] = d["QQQ"] > d["MA200"]
    d["level"] = np.floor(np.maximum(0, -d["DD"]) / step)
    d["weight"] = np.minimum(d["level"] * 0.20, 1.0)
    d.loc[~d["trend"], "weight"] = 0
    d.loc[d["RSI"] >= 75, "weight"] *= 0.5
    d.loc[d["MA200"].isna(), "weight"] = 0

    ret = d["TQQQ"].pct_change().fillna(0)
    d["strategy_ret"] = d["weight"].shift(1).fillna(0) * ret
    d["strategy_value"] = investment * (1 + d["strategy_ret"]).cumprod()
    d["hold_value"] = investment * (1 + ret).cumprod()

    peak = d["strategy_value"].cummax()
    mdd = (d["strategy_value"] / peak - 1).min()
    years = max((d.index[-1]-d.index[0]).days / 365.25, 1/365.25)
    cagr = (d["strategy_value"].iloc[-1] / investment) ** (1/years) - 1
    hold_cagr = (d["hold_value"].iloc[-1] / investment) ** (1/years) - 1

    a,b,c = st.columns(3)
    a.metric("퀀트 CAGR", f"{cagr:.1%}")
    b.metric("TQQQ 보유 CAGR", f"{hold_cagr:.1%}")
    c.metric("퀀트 MDD", f"{mdd:.1%}")

    st.line_chart(d[["strategy_value","hold_value"]].rename(columns={
        "strategy_value":"퀀트 전략", "hold_value":"TQQQ 단순보유"
    }))

    with st.expander("⚠️ 꼭 읽어주세요"):
        st.write(
            "이 도구는 교육·백테스트용입니다. 레버리지 ETF인 TQQQ는 큰 손실과 높은 변동성이 발생할 수 있습니다. "
            "백테스트 결과가 미래 수익을 보장하지 않으며, 세금·환율·수수료·슬리피지 등은 충분히 반영되지 않습니다. "
            "자동주문 기능은 포함하지 않습니다."
        )

except Exception as e:
    st.error("데이터를 불러오지 못했습니다. 잠시 후 다시 실행해 주세요.")
    st.exception(e)
