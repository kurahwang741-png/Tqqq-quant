import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="TQQQ / 코코레 QUANT", page_icon="📈")

st.title("📈 TQQQ / 코코레 QUANT")
st.caption("낙폭 + 일봉 + 분봉 전략 비교 / 실제 매수금액 계산")

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
def get_data(ticker, period="max", interval=None):
    kwargs = dict(auto_adjust=False, progress=False)
    if interval:
        kwargs["interval"] = interval
        kwargs["period"] = period
    else:
        kwargs["period"] = period
    df = yf.download(ticker, **kwargs)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def calc_rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    down = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def metrics(equity):
    equity = equity.dropna()
    if len(equity) < 2:
        return np.nan, np.nan, np.nan
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    dd = equity / equity.cummax() - 1
    mdd = dd.min()
    daily_ret = equity.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else np.nan
    return cagr, mdd, sharpe

def drawdown_target(d, step):
    stage = max(0, int(np.floor(abs(d) / step + 1e-9)))
    return min(stage * 0.20, 1.0)

def daily_backtest(lev, ref, step, mode):
    x = lev[["Close"]].copy()
    r = ref[["Close"]].copy().rename(columns={"Close":"REF"})
    df = x.join(r, how="inner").dropna()
    df["PEAK"] = df["Close"].cummax()
    df["DD"] = df["Close"] / df["PEAK"] - 1
    df["MA200"] = df["REF"].rolling(200).mean()
    df["RSI"] = calc_rsi(df["REF"])

    target = df["DD"].apply(lambda z: drawdown_target(z, step))
    if mode in ["B", "C"]:
        target = target.where(df["REF"] >= df["MA200"], 0.0)
        target = target.where(df["RSI"] < 75, target * 0.5)

    # A: 낙폭만 / B: 낙폭+일봉 필터
    # C는 최근 분봉 데이터의 평균 확인 결과를 일봉에 적용하는 방식이 아니므로
    # 장기 전체 기간의 공정한 비교를 위해 C는 별도 최근 구간 테스트로 계산.
    ret = df["Close"].pct_change().fillna(0)
    equity = (1 + target.shift(1).fillna(0) * ret).cumprod()
    return equity, target, df

try:
    lev = get_data(symbol)
    ref = get_data(reference)

    if lev.empty or ref.empty:
        st.error("데이터를 불러오지 못했습니다.")
        st.stop()

    lev["PEAK"] = lev["Close"].cummax()
    lev["DD"] = lev["Close"] / lev["PEAK"] - 1
    ref["MA200"] = ref["Close"].rolling(200).mean()
    ref["RSI"] = calc_rsi(ref["Close"])

    latest = lev.iloc[-1]
    rlatest = ref.iloc[-1]
    price = float(latest["Close"])
    peak = float(latest["PEAK"])
    dd = float(latest["DD"])
    ma200 = float(rlatest["MA200"])
    rsi_daily = float(rlatest["RSI"])
    ref_close = float(rlatest["Close"])

    stage = max(0, int(np.floor(abs(dd) / step + 1e-9)))
    target_weight = min(stage * 0.20, 1.0)
    trend_ok = np.isnan(ma200) or ref_close >= ma200
    if not trend_ok:
        target_weight = 0.0
    if not np.isnan(rsi_daily) and rsi_daily >= 75:
        target_weight *= 0.5

    target_amount = amount * target_weight

    st.subheader("① 현재 상태")
    c1,c2 = st.columns(2)
    c1.metric("현재 가격", f"{price:,.2f} {currency}")
    c2.metric("고점 대비 낙폭", f"{dd*100:.2f}%")
    c3,c4 = st.columns(2)
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

    a,b,c = st.columns(3)
    a.metric("목표 비중", f"{target_weight*100:.0f}%")
    b.metric("목표 투자금", f"{target_amount:,.0f} {currency}")
    c.metric("추가 매수금액", f"{additional:,.0f} {currency}")

    st.divider()
    st.subheader("② 30분봉 + 5분봉 타이밍")

    timing_ok = False
    intraday_available = False
    try:
        m30 = get_data(symbol, "60d", "30m")
        m5 = get_data(symbol, "60d", "5m")
        if not m30.empty and not m5.empty:
            intraday_available = True
            for x in (m30, m5):
                x["EMA20"] = x["Close"].ewm(span=20, adjust=False).mean()
                x["RSI"] = calc_rsi(x["Close"])
            m5["VOLAVG20"] = m5["Volume"].rolling(20).mean()
            a30,a5 = m30.iloc[-1],m5.iloc[-1]
            ok30 = float(a30["Close"]) > float(a30["EMA20"]) and float(a30["RSI"]) >= 50
            ok5 = float(a5["Close"]) > float(a5["EMA20"]) and float(a5["RSI"]) >= 50
            timing_ok = ok30 and ok5
            i1,i2,i3 = st.columns(3)
            i1.metric("30분봉", "✅ 확인" if ok30 else "⏸️ 대기")
            i2.metric("5분봉", "✅ 확인" if ok5 else "⏸️ 대기")
            i3.metric("최종", "🟢 매수 가능" if timing_ok else "🟡 대기")
        else:
            st.warning("분봉 데이터가 없습니다.")
    except Exception as e:
        st.warning("현재 분봉 데이터를 가져오지 못했습니다.")

    if additional <= 0:
        st.warning("🟡 추가 매수 없음 — 현재 목표 투자금에 도달")
    elif not trend_ok:
        st.error(f"🔴 매수 대기 — 200일선 필터 / 추가매수 한도 {additional:,.0f} {currency}")
    elif intraday_available and timing_ok:
        st.success(f"🟢 분봉 확인 — 최대 {additional:,.0f} {currency} 추가 매수")
    elif intraday_available:
        st.warning(f"🟡 분봉 대기 — 추가매수 한도 {additional:,.0f} {currency}")
    else:
        st.info(f"ℹ️ 일봉 기준 추가매수 한도: {additional:,.0f} {currency}")

    st.divider()
    st.subheader("③ 전략 백테스트 비교")
    st.caption("A=낙폭만 / B=낙폭+200일선·RSI. C=분봉 전략은 최근 분봉 데이터 한계 때문에 아래 별도 테스트에서 확인합니다.")

    eq_a, _, df_bt = daily_backtest(lev, ref, step, "A")
    eq_b, _, _ = daily_backtest(lev, ref, step, "B")
    cagr_a,mdd_a,sh_a = metrics(eq_a)
    cagr_b,mdd_b,sh_b = metrics(eq_b)

    table = pd.DataFrame({
        "전략": ["A. 낙폭만", "B. 낙폭 + 일봉 필터"],
        "CAGR": [f"{cagr_a*100:.2f}%", f"{cagr_b*100:.2f}%"],
        "MDD": [f"{mdd_a*100:.2f}%", f"{mdd_b*100:.2f}%"],
        "Sharpe": [f"{sh_a:.2f}", f"{sh_b:.2f}"]
    })
    st.dataframe(table, use_container_width=True, hide_index=True)

    chart = pd.DataFrame({"A. 낙폭만": eq_a, "B. 낙폭+일봉": eq_b})
    st.line_chart(chart)

    st.divider()
    st.subheader("④ 최근 분봉 필터 검증")
    st.caption("분봉은 Yahoo Finance의 최근 데이터만 사용할 수 있어 장기 백테스트와 분리합니다.")

    try:
        m5 = get_data(symbol, "60d", "5m")
        if len(m5) > 100:
            m5["EMA20"] = m5["Close"].ewm(span=20, adjust=False).mean()
            m5["RSI"] = calc_rsi(m5["Close"])
            m5["VOLAVG20"] = m5["Volume"].rolling(20).mean()

            # 단순한 최근 5분봉 시뮬레이션:
            # 기준: 신호가 켜지면 다음 봉 수익을 취하고, 꺼지면 현금.
            signal = (
                (m5["Close"] > m5["EMA20"]) &
                (m5["RSI"] >= 50)
            ).astype(float)
            ret5 = m5["Close"].pct_change().fillna(0)
            eq_c = (1 + signal.shift(1).fillna(0) * ret5).cumprod()

            cagr_c,mdd_c,sh_c = metrics(eq_c.resample("1D").last().dropna())

            cagr_base,mdd_base,sh_base = metrics(
                m5["Close"].resample("1D").last().dropna() /
                m5["Close"].resample("1D").last().dropna().iloc[0]
            )

            t = pd.DataFrame({
                "최근 60일 5분봉": ["C. 5분봉 필터", "단순 보유"],
                "CAGR": [f"{cagr_c*100:.2f}%", f"{cagr_base*100:.2f}%"],
                "MDD": [f"{mdd_c*100:.2f}%", f"{mdd_base*100:.2f}%"],
                "Sharpe": [f"{sh_c:.2f}", f"{sh_base:.2f}"]
            })
            st.dataframe(t, use_container_width=True, hide_index=True)
            st.line_chart(pd.DataFrame({"C. 5분봉 필터": eq_c.resample("1D").last()}))
        else:
            st.info("최근 5분봉 데이터가 충분하지 않습니다.")
    except Exception:
        st.info("최근 분봉 검증 데이터를 가져오지 못했습니다.")

    st.divider()
    st.subheader("⑤ 낙폭별 단계")
    rows=[]
    for i in range(1,6):
        level=-step*i
        weight=min(i*0.20,1.0)
        threshold=peak*(1+level)
        rows.append({
            "단계":i,
            "낙폭":f"{level*100:.0f}%",
            "기본 목표비중":f"{weight*100:.0f}%",
            "목표금액":f"{amount*weight:,.0f} {currency}",
            "가격기준":f"{threshold:,.2f} {currency}"
        })
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

    st.info("중복매수 방지: 같은 단계에서는 목표금액-현재보유금액만 추가 매수합니다. 다음 단계로 내려가 목표금액이 증가할 때만 추가 매수가 생깁니다.")
    st.caption("주의: 백테스트는 교육용 가정입니다. 세금·수수료·슬리피지·환전비용을 반영하지 않으며 미래 수익을 보장하지 않습니다.")

except Exception as e:
    st.error("데이터 처리 중 오류가 발생했습니다.")
    st.caption(str(e))
