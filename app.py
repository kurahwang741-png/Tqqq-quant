import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="TQQQ / 코코레 QUANT", page_icon="📈")

st.title("📈 TQQQ / 코코레 QUANT")
st.caption("낙폭으로 금액을 정하고, 30분봉·5분봉으로 매수 타이밍을 확인합니다.")

market = st.radio(
    "시장 선택",
    ["🇺🇸 미국장 — TQQQ", "🇰🇷 한국장 — 코코레"],
    horizontal=True
)

if "🇺🇸" in market:
    symbol, reference, currency = "TQQQ", "QQQ", "$"
    amount = st.number_input("총 투자금 (USD)", min_value=0.0, value=10000.0, step=500.0)
else:
    symbol, reference, currency = "233740.KS", "229200.KS", "원"
    amount = st.number_input("총 투자금 (KRW)", min_value=0.0, value=10000000.0, step=100000.0)

strategy = st.selectbox("낙폭 단계", ["5% 간격", "7% 간격", "10% 간격"])
step = {"5% 간격": 0.05, "7% 간격": 0.07, "10% 간격": 0.10}[strategy]

@st.cache_data(ttl=1800)
def daily_data(ticker):
    df = yf.download(ticker, period="max", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

@st.cache_data(ttl=300)
def intraday_data(ticker, interval):
    # Yahoo Finance intraday data is limited to recent history.
    df = yf.download(ticker, period="60d", interval=interval,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

try:
    lev = daily_data(symbol)
    ref = daily_data(reference)

    if lev.empty or ref.empty:
        st.error("일봉 데이터를 불러오지 못했습니다.")
        st.stop()

    lev["PEAK"] = lev["Close"].cummax()
    lev["DD"] = lev["Close"] / lev["PEAK"] - 1
    ref["MA200"] = ref["Close"].rolling(200).mean()
    ref["RSI"] = rsi(ref["Close"])

    latest = lev.iloc[-1]
    rlatest = ref.iloc[-1]

    price = float(latest["Close"])
    peak = float(latest["PEAK"])
    dd = float(latest["DD"])
    ma200 = float(rlatest["MA200"])
    rsi_daily = float(rlatest["RSI"])
    ref_close = float(rlatest["Close"])

    stage = max(0, int(np.floor(abs(dd) / step + 1e-9)))
    raw_weight = min(stage * 0.20, 1.0)
    target_weight = raw_weight

    trend_ok = True if np.isnan(ma200) else ref_close >= ma200
    if not trend_ok:
        target_weight = 0.0

    rsi_hot = not np.isnan(rsi_daily) and rsi_daily >= 75
    if rsi_hot:
        target_weight *= 0.5

    target_amount = amount * target_weight

    st.subheader("① 일봉 — 얼마까지 살지 결정")
    c1, c2 = st.columns(2)
    c1.metric("현재 가격", f"{price:,.2f} {currency}")
    c2.metric("고점 대비 낙폭", f"{dd*100:.2f}%")
    c3, c4 = st.columns(2)
    c3.metric("기준 RSI(14)", f"{rsi_daily:.1f}")
    c4.metric("200일선", f"{ma200:,.2f}")

    held = st.number_input(
        f"현재 보유금액 ({currency})",
        min_value=0.0,
        max_value=float(amount),
        value=0.0,
        step=500.0 if currency == "$" else 100000.0
    )
    additional = max(0.0, target_amount - held)

    a, b, c = st.columns(3)
    a.metric("목표 비중", f"{target_weight*100:.0f}%")
    b.metric("목표 투자금", f"{target_amount:,.0f} {currency}")
    c.metric("추가 매수 한도", f"{additional:,.0f} {currency}")

    if not trend_ok:
        st.warning("기준 ETF가 200일선 아래라 신규 목표비중을 0%로 설정했습니다.")
    if rsi_hot:
        st.info("기준 RSI가 75 이상이라 목표비중을 절반으로 줄였습니다.")

    st.divider()
    st.subheader("② 30분봉 — 중간 추세 확인")

    try:
        m30 = intraday_data(symbol, "30m")
        m5 = intraday_data(symbol, "5m")

        if m30.empty or m5.empty:
            raise ValueError("분봉 데이터가 없습니다.")

        m30["EMA20"] = m30["Close"].ewm(span=20, adjust=False).mean()
        m30["RSI"] = rsi(m30["Close"])
        m5["EMA20"] = m5["Close"].ewm(span=20, adjust=False).mean()
        m5["RSI"] = rsi(m5["Close"])
        m5["VOL_AVG20"] = m5["Volume"].rolling(20).mean()

        x30 = m30.iloc[-1]
        x5 = m5.iloc[-1]

        m30_trend = float(x30["Close"]) > float(x30["EMA20"])
        m30_rsi_ok = float(x30["RSI"]) >= 50 if not np.isnan(float(x30["RSI"])) else False
        m5_trend = float(x5["Close"]) > float(x5["EMA20"])
        m5_rsi_ok = float(x5["RSI"]) >= 50 if not np.isnan(float(x5["RSI"])) else False
        volume_ok = (
            float(x5["Volume"]) >= float(x5["VOL_AVG20"])
            if not np.isnan(float(x5["VOL_AVG20"])) else False
        )

        i1, i2, i3 = st.columns(3)
        i1.metric("30분봉", "상승 확인" if m30_trend and m30_rsi_ok else "대기")
        i2.metric("5분봉", "상승 확인" if m5_trend and m5_rsi_ok else "대기")
        i3.metric("거래량", "평균 이상" if volume_ok else "평균 이하")

        st.caption(
            "가설적 타이밍 필터: 30분봉 가격>EMA20 및 RSI≥50, "
            "5분봉 가격>EMA20 및 RSI≥50, 거래량은 20개 평균 이상을 확인합니다."
        )

        timing_ok = m30_trend and m30_rsi_ok and m5_trend and m5_rsi_ok
        if timing_ok:
            timing_text = "🟢 분봉 매수 타이밍 확인"
        else:
            timing_text = "🟡 분봉상 아직 대기"

        st.subheader("③ 최종 판단")
        if additional <= 0:
            st.warning("🟡 추가 매수금액 0 — 현재 목표 비중을 이미 충족했습니다.")
        elif not trend_ok:
            st.error("🔴 매수 대기 — 200일선 추세 필터가 꺼져 있습니다.")
        elif timing_ok:
            st.success(f"{timing_text} → **{additional:,.0f} {currency}**까지 추가 매수 가능")
        else:
            st.warning(f"{timing_text} → 추가 매수 한도는 **{additional:,.0f} {currency}**")

    except Exception as intraday_error:
        st.warning("분봉 데이터를 가져오지 못했습니다. 일봉 기준 계산은 계속 사용할 수 있습니다.")
        st.caption(f"분봉 상태: {intraday_error}")

    st.divider()
    st.subheader("📊 낙폭별 매수 단계")
    rows = []
    for i in range(1, 6):
        level = -step * i
        weight = min(i * 0.20, 1.0)
        threshold = peak * (1 + level)
        rows.append({
            "단계": i,
            "낙폭": f"{level*100:.0f}%",
            "기본 목표비중": f"{weight*100:.0f}%",
            "목표금액": f"{amount*weight:,.0f} {currency}",
            "가격 기준": f"{threshold:,.2f} {currency}"
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.info(
        "중복매수 방지: 같은 낙폭 단계에서 목표금액이 그대로라면 추가 매수금액은 "
        "목표금액 - 현재 보유금액으로만 계산합니다. 다음 단계로 내려가 목표금액이 "
        "늘어날 때만 추가 매수가 생깁니다."
    )

    st.caption(
        "분봉 데이터는 Yahoo Finance 제공 범위에 따라 최근 기간만 조회됩니다. "
        "분봉 필터는 예시 전략이며 수익을 보장하지 않습니다. 실제 주문은 실행하지 않습니다."
    )

except Exception as e:
    st.error("데이터 처리 중 오류가 발생했습니다.")
    st.caption(str(e))
