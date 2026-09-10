import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title='TQQQ / 코코레 QUANT', page_icon='📈')
st.title('📈 TQQQ / 코코레 QUANT')
st.caption('낙폭 단계 + 현재 보유금액 기준 추가매수 계산')
market = st.radio('시장 선택',['🇺🇸 미국장 — TQQQ','🇰🇷 한국장 — 코코레'],horizontal=True)
if '🇺🇸' in market:
    symbol, reference, currency = 'TQQQ','QQQ','$'
    amount = st.number_input('총 투자금 (USD)',min_value=0.0,value=10000.0,step=500.0)
else:
    symbol, reference, currency = '233740.KS','229200.KS','원'
    amount = st.number_input('총 투자금 (KRW)',min_value=0.0,value=10000000.0,step=100000.0)
strategy=st.selectbox('낙폭 단계',['5% 간격','7% 간격','10% 간격'])
step={'5% 간격':.05,'7% 간격':.07,'10% 간격':.10}[strategy]
@st.cache_data(ttl=1800)
def load_data(ticker):
    df=yf.download(ticker,period='max',auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    return df.dropna()
try:
    lev,ref=load_data(symbol),load_data(reference)
    if lev.empty or ref.empty: st.error('데이터를 불러오지 못했습니다.'); st.stop()
    lev['PEAK']=lev['Close'].cummax(); lev['DD']=lev['Close']/lev['PEAK']-1
    ref['MA200']=ref['Close'].rolling(200).mean()
    d=ref['Close'].diff(); gain=d.clip(lower=0).rolling(14).mean(); loss=(-d.clip(upper=0)).rolling(14).mean()
    rs=gain/loss.replace(0,np.nan); ref['RSI']=100-(100/(1+rs))
    latest,r=lev.iloc[-1],ref.iloc[-1]
    price,peak,dd=float(latest['Close']),float(latest['PEAK']),float(latest['DD'])
    ma200,rsi=float(r['MA200']),float(r['RSI'])
    stage=max(0,int(np.floor(abs(dd)/step+1e-9))); target_weight=min(stage*.20,1.0)
    if not np.isnan(ma200) and float(r['Close'])<ma200: target_weight=0.0
    if not np.isnan(rsi) and rsi>=75: target_weight*=.5
    target_amount=amount*target_weight
    st.subheader('현재 상태'); c1,c2=st.columns(2); c1.metric('현재 가격',f'{price:,.2f} {currency}'); c2.metric('고점 대비 낙폭',f'{dd*100:.2f}%')
    c3,c4=st.columns(2); c3.metric('RSI(14)',f'{rsi:.1f}'); c4.metric('200일선',f'{ma200:,.2f}')
    st.divider(); st.subheader('💰 실제 매수 계산')
    held=st.number_input(f'현재 보유금액 ({currency})',min_value=0.0,max_value=float(amount),value=0.0,step=500.0 if currency=='$' else 100000.0)
    additional=max(0.0,target_amount-held)
    a,b=st.columns(2); a.metric('목표 투자금',f'{target_amount:,.0f} {currency}'); b.metric('추가 매수금액',f'{additional:,.0f} {currency}')
    if additional>0: st.success(f'🟢 현재 기준 추가 매수: {additional:,.0f} {currency}')
    else: st.warning('🟡 현재 목표 비중을 이미 충족했습니다. 추가 매수 없음')
    st.divider(); st.subheader('📊 낙폭별 매수 단계')
    rows=[]
    for i in range(1,6):
        level=-step*i; weight=min(i*.20,1.0); threshold=peak*(1+level)
        rows.append({'단계':i,'낙폭':f'{level*100:.0f}%','목표비중':f'{weight*100:.0f}%','목표금액':f'{amount*weight:,.0f} {currency}','해당 가격':f'{threshold:,.2f} {currency}'})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.info('같은 낙폭에 머무르는 날에는 현재 보유금액을 기준으로 중복 매수하지 않습니다. 더 큰 낙폭 단계로 내려가 목표금액이 증가할 때만 추가 매수금액이 생깁니다.')
    st.subheader('📌 오늘의 판단')
    if target_weight==0: st.error('⛔ 목표 투자비중 0% — 매수 대기')
    elif additional>0: st.success(f'🟢 목표 투자비중 {target_weight*100:.0f}% — {additional:,.0f} {currency} 추가 매수 가능')
    else: st.warning('🟡 목표 투자비중은 충족 — 추가 매수 없음')
    st.caption('교육/백테스트용입니다. 실제 주문을 자동 실행하지 않습니다. 세금·수수료·환율·슬리피지는 별도입니다.')
except Exception as e:
    st.error('데이터 처리 중 오류가 발생했습니다.'); st.caption(str(e))
