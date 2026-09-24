import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, datetime, timezone
import time
import json
import hashlib
import gzip
import pickle
import urllib.error
import urllib.parse
import urllib.request
from io import StringIO
from pathlib import Path

st.set_page_config(
    page_title="SOXL QUANT V32 DUAL",
    page_icon="📈",
    layout="centered",
)


page = st.sidebar.radio(
    "메뉴",
    ["📈 SOXL 퀀트", "🇰🇷 반복 지지구간 V1", "🇰🇷 반복 지지구간 V2 · 대장주"],
    index=0,
    key="main_page_support_v1",
)

if page == "🇰🇷 반복 지지구간 V2 · 대장주":
    st.title("🇰🇷 반복 지지구간 V2 · 대장주")
    st.caption("V1 지지선 공식은 고정 · 이번에는 '누구의 지지선인가'만 검증")
    st.info("중요: 이 V2의 '대장'은 과거 테마명이 아니라 같은 날 강세 후보들 사이의 상대강도·거래대금·신고가 근접도로 만든 동적 대장 프록시입니다. 결과가 살아남아야 실제 섹터/테마 데이터를 붙입니다.")

    try:
        import FinanceDataReader as fdr
    except Exception as e:
        st.error("FinanceDataReader가 필요합니다. requirements.txt에 finance-datareader를 추가해 주세요.")
        st.exception(e); st.stop()

    V2_START, V2_END = "2015-01-01", "2023-12-31"
    V1_CKPT = Path("kr_support_v1_checkpoint.pkl.gz")
    V2_CKPT = Path("kr_support_v2_leader_checkpoint.pkl.gz")

    def _v2_save(path, obj):
        tmp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=3) as f: pickle.dump(obj, f, pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)

    def _v2_load(path, default):
        try:
            with gzip.open(path, "rb") as f: return pickle.load(f)
        except Exception: return default

    @st.cache_data(ttl=86400, show_spinner=False)
    def _v2_universe():
        def norm(d, src):
            if d is None or d.empty: return pd.DataFrame(columns=["Code","Name","Source"])
            cc=next((c for c in ["Code","Symbol"] if c in d.columns),None)
            nn=next((c for c in ["Name","Company"] if c in d.columns),None)
            if not cc or not nn: return pd.DataFrame(columns=["Code","Name","Source"])
            q=pd.DataFrame({"Code":d[cc].astype(str).str.zfill(6),"Name":d[nn].astype(str),"Source":src})
            return q[q.Code.str.fullmatch(r"\d{6}",na=False)].drop_duplicates("Code")
        fs=[norm(fdr.StockListing("KRX"),"현재상장")]
        try: fs.append(norm(fdr.StockListing("KRX-DELISTING"),"상장폐지"))
        except Exception: pass
        return pd.concat(fs,ignore_index=True).drop_duplicates("Code",keep="first").reset_index(drop=True)

    def _v2_price(code):
        d=fdr.DataReader(str(code),V2_START,V2_END)
        if d is None or d.empty: return pd.DataFrame()
        d=d.copy(); d.index=pd.to_datetime(d.index).tz_localize(None)
        need=["Open","High","Low","Close","Volume"]
        if any(c not in d.columns for c in need): return pd.DataFrame()
        for c in need: d[c]=pd.to_numeric(d[c],errors="coerce")
        return d.dropna(subset=need).sort_index()

    def _v2_lead_events(code,name,source,d):
        if len(d)<130: return []
        x=d.copy(); x["R1"]=x.Close.pct_change(); x["R20"]=x.Close.pct_change(20)
        x["Amount"]=x.Close*x.Volume; x["H120"]=x.High.rolling(120,min_periods=60).max()
        x["HighProx"]=x.Close/x.H120
        # 넓게 수집: 당일 강세 또는 최근 20일 강한 상승. 순위는 전체 구축 후 날짜별로 계산.
        m=((x.R1>=.08)|(x.R20>=.30)) & (x.index.year>=2015) & (x.index.year<=2023)
        out=[]
        for dt,r in x.loc[m,["R1","R20","Amount","HighProx"]].iterrows():
            out.append({"Code":str(code),"Name":str(name),"Source":str(source),"Date":dt,
                        "R1":float(r.R1) if np.isfinite(r.R1) else np.nan,
                        "R20":float(r.R20) if np.isfinite(r.R20) else np.nan,
                        "Amount":float(r.Amount) if np.isfinite(r.Amount) else np.nan,
                        "HighProx":float(r.HighProx) if np.isfinite(r.HighProx) else np.nan})
        return out

    v1_restore=st.file_uploader("① 완성된 V1 체크포인트 복원",type=["gz"],key="sv2_v1_restore")
    if v1_restore is not None and st.button("V1 체크포인트 복원",use_container_width=True,key="sv2_v1_btn"):
        V1_CKPT.write_bytes(v1_restore.getvalue()); st.success("V1 체크포인트를 복원했습니다."); st.rerun()
    v1=_v2_load(V1_CKPT,{})
    v1rows=v1.get("rows",[]) if isinstance(v1,dict) else []
    if not v1rows:
        st.warning("먼저 완성된 kr_support_v1_checkpoint.pkl.gz를 복원해 주세요."); st.stop()

    universe=_v2_universe()
    batch=st.select_slider("한 번에 처리할 종목 수",options=[50,100,200,300,400,500],value=300,key="sv2_batch")
    v2_restore=st.file_uploader("② V2 구축 체크포인트 복원",type=["gz"],key="sv2_restore")
    if v2_restore is not None and st.button("V2 체크포인트 복원",use_container_width=True,key="sv2_restore_btn"):
        V2_CKPT.write_bytes(v2_restore.getvalue()); st.success("V2 체크포인트를 복원했습니다."); st.rerun()
    ck=_v2_load(V2_CKPT,{"done":{},"events":[]})
    done=ck.get("done",{}); events=ck.get("events",[])
    total=len(universe); processed=len(done)
    a,b,c=st.columns(3); a.metric("전체 종목",f"{total:,}"); b.metric("완료",f"{processed:,}"); c.metric("강세 이벤트",f"{len(events):,}")
    st.progress(min(processed/max(total,1),1.0),text=f"{processed:,}/{total:,}")

    if st.button(f"▶ 다음 {batch}종목 대장 데이터 구축",type="primary",use_container_width=True,disabled=(processed>=total),key="sv2_build"):
        pending=universe[~universe.Code.astype(str).isin(done)].head(batch); bar=st.progress(0.0); status=st.empty(); t0=time.time()
        for k,(_,r) in enumerate(pending.iterrows(),1):
            code=str(r.Code)
            try:
                d=_v2_price(code); add=_v2_lead_events(code,str(r.Name),str(r.Source),d) if not d.empty else []
                events.extend(add); done[code]=len(add)
            except Exception as e: done[code]=f"ERR:{type(e).__name__}"
            if k%10==0 or k==len(pending): _v2_save(V2_CKPT,{"done":done,"events":events,"rule":"support_v2_dynamic_leader_proxy_locked"})
            elapsed=time.time()-t0; rate=k/elapsed if elapsed else 0; eta=(len(pending)-k)/rate if rate else 0
            bar.progress(k/max(len(pending),1),text=f"이번 묶음 {k}/{len(pending)}"); status.caption(f"{r.Name} ({code}) · {rate:.2f}종목/초 · 약 {eta/60:.1f}분")
        st.success("이번 묶음 완료"); st.rerun()

    if V2_CKPT.exists():
        st.download_button("💾 V2 체크포인트 내려받기",data=V2_CKPT.read_bytes(),file_name="kr_support_v2_leader_checkpoint.pkl.gz",mime="application/gzip",use_container_width=True)

    if processed>=total and events:
        ev=pd.DataFrame(events); ev["Date"]=pd.to_datetime(ev.Date)
        # 날짜별 상대 순위. 절대 거래대금 컷 대신 같은 날 후보군 내 순위를 사용한다.
        ev["R1Rank"]=ev.groupby("Date")["R1"].rank(pct=True)
        ev["AmtRank"]=ev.groupby("Date")["Amount"].rank(pct=True)
        ev["HighRank"]=ev.groupby("Date")["HighProx"].rank(pct=True)
        ev["LeaderScore"]=(ev.R1Rank.fillna(0)*.40+ev.AmtRank.fillna(0)*.40+ev.HighRank.fillna(0)*.20)
        ev["LeaderTier"]=np.select([ev.LeaderScore>=.90,ev.LeaderScore>=.70],["대장 후보","2~3등 후보"],default="일반 강세주")
        bycode={k:g.sort_values("Date") for k,g in ev.groupby("Code")}
        matched=[]
        for r in v1rows:
            q=dict(r); code=str(q.get("Code","")); sd=pd.Timestamp(q.get("SignalDate"))
            g=bycode.get(code)
            tier="대장 이력 없음"; score=np.nan; lead_date=pd.NaT; days=np.nan
            if g is not None and not g.empty:
                h=g[(g.Date<sd)&(g.Date>=sd-pd.Timedelta(days=120))]
                if not h.empty:
                    z=h.sort_values(["LeaderScore","Date"],ascending=[False,False]).iloc[0]
                    tier=str(z.LeaderTier); score=float(z.LeaderScore); lead_date=pd.Timestamp(z.Date); days=int((sd-lead_date).days)
            q.update({"LeaderTier":tier,"DynamicLeaderScore":score,"LeaderEventDate":lead_date,"DaysFromLeaderEvent":days})
            matched.append(q)
        md=pd.DataFrame(matched)
        st.subheader("V2 결과 · 누구의 지지선인가")
        out=[]
        for (period,tier),g in md.groupby(["Period","LeaderTier"]):
            z=pd.to_numeric(g.R20,errors="coerce").dropna()
            out.append({"구간":period,"분류":tier,"신호수":len(g),"R20 승률":float((z>0).mean()) if len(z) else np.nan,"R20 평균":float(z.mean()) if len(z) else np.nan,"R20 중앙값":float(z.median()) if len(z) else np.nan})
        sm=pd.DataFrame(out).sort_values(["구간","분류"])
        st.dataframe(sm.style.format({"R20 승률":"{:.2%}","R20 평균":"{:.2%}","R20 중앙값":"{:.2%}"},na_rep="-"),use_container_width=True,hide_index=True)
        payload={"format":"kr_support_v2_leader_portable_v1","definition":"same-day dynamic leader proxy: R1 40% + Amount rank 40% + 120d-high proximity 20%","development":"2016-2021","validation":"2022-2023","signals":[]}
        for r in matched:
            q=dict(r)
            for key in ["SignalDate","LeaderEventDate"]:
                if key in q and pd.notna(q[key]): q[key]=pd.Timestamp(q[key]).strftime("%Y-%m-%d")
                elif key in q: q[key]=None
            for key,val in list(q.items()):
                if isinstance(val,np.integer): q[key]=int(val)
                elif isinstance(val,np.floating): q[key]=None if pd.isna(val) else float(val)
            payload["signals"].append(q)
        portable=gzip.compress(pickle.dumps(payload,protocol=4),compresslevel=3)
        st.download_button("📦 V2 분석용 파일 내려받기",data=portable,file_name="kr_support_v2_leader_portable.pkl.gz",mime="application/gzip",use_container_width=True)
        st.caption("V1 지지선 규칙은 변경하지 않았습니다. V2는 대장 이력 분류만 추가합니다. 이 단계에서는 실제 테마명을 사용하지 않습니다.")
    else:
        st.info("전체 종목 구축이 끝나면 날짜별 상대순위를 계산해 V1 신호를 대장/2~3등/일반으로 자동 분류합니다.")
    st.stop()

if page == "🇰🇷 반복 지지구간 V1":
    st.title("🇰🇷 반복 지지구간 V1")
    st.caption("강했던 종목이 같은 가격대를 반복 테스트하는 자리부터 검증 · 일봉 1차 연구")
    st.info("개발 2016~2021 / 검증 2022~2023 고정. 2024년 이후는 이번 V1 구축에서 사용하지 않습니다.")

    try:
        import FinanceDataReader as fdr
    except Exception as e:
        st.error("FinanceDataReader가 필요합니다. requirements.txt에 finance-datareader를 추가해 주세요.")
        st.exception(e)
        st.stop()

    SUPPORT_START, SUPPORT_END = "2015-01-01", "2023-12-31"
    SUPPORT_CKPT = Path("kr_support_v1_checkpoint.pkl.gz")
    SUPPORT_RESULT = Path("kr_support_v1_signals.pkl.gz")

    def _sv1_save(path, obj):
        tmp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=3) as f:
            pickle.dump(obj, f, pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)

    def _sv1_load(path, default):
        try:
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return default

    @st.cache_data(ttl=86400, show_spinner=False)
    def _sv1_universe(include_delisted=True):
        def norm(d, source):
            if d is None or d.empty:
                return pd.DataFrame(columns=["Code", "Name", "Source"])
            cc = next((c for c in ["Code", "Symbol"] if c in d.columns), None)
            nn = next((c for c in ["Name", "Company"] if c in d.columns), None)
            if not cc or not nn:
                return pd.DataFrame(columns=["Code", "Name", "Source"])
            q = pd.DataFrame({"Code": d[cc].astype(str).str.zfill(6), "Name": d[nn].astype(str), "Source": source})
            return q[q.Code.str.fullmatch(r"\d{6}", na=False)].drop_duplicates("Code")
        frames = [norm(fdr.StockListing("KRX"), "현재상장")]
        if include_delisted:
            try:
                frames.append(norm(fdr.StockListing("KRX-DELISTING"), "상장폐지"))
            except Exception:
                pass
        return pd.concat(frames, ignore_index=True).drop_duplicates("Code", keep="first").reset_index(drop=True)

    def _sv1_price(code):
        d = fdr.DataReader(str(code), SUPPORT_START, SUPPORT_END)
        if d is None or d.empty:
            return pd.DataFrame()
        d = d.copy()
        d.index = pd.to_datetime(d.index).tz_localize(None)
        need = ["Open", "High", "Low", "Close", "Volume"]
        if any(c not in d.columns for c in need):
            return pd.DataFrame()
        for c in need:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        return d.dropna(subset=need).sort_index()

    def _sv1_scan(code, name, source, d):
        """V1: 강한 상승 뒤 ±2% 지지존을 2~4번째 재시험하는 일봉 신호를 수집한다."""
        if len(d) < 100:
            return []
        x = d.copy()
        x["R20"] = x.Close.pct_change(20)
        x["R60"] = x.Close.pct_change(60)
        x["Amount"] = x.Close * x.Volume
        x["Amt20"] = x.Amount.rolling(20).mean()
        rows = []
        last_signal = None
        for i in range(80, len(x) - 21):
            dt = x.index[i]
            if not 2016 <= dt.year <= 2023:
                continue
            # 최근 60거래일 안에 '강했던 종목' 흔적: 20일 +30% 또는 60일 +60%
            pre = x.iloc[max(0, i-60):i+1]
            strong = bool((pre.R20 >= .30).any() or (pre.R60 >= .60).any())
            if not strong:
                continue
            # 오늘 이전 60거래일의 저점 후보. 오늘 가격과 ±2%인 과거 저점들을 지지존 접촉으로 본다.
            cur_low = float(x.Low.iloc[i]); cur_close = float(x.Close.iloc[i])
            if cur_low <= 0:
                continue
            hist = x.iloc[max(0, i-60):i]
            lows = hist.Low.astype(float)
            near_idx = [j for j, v in enumerate(lows.values) if v > 0 and abs(v / cur_low - 1) <= .02]
            if not near_idx:
                continue
            # 서로 5거래일 이상 떨어진 접촉만 독립 테스트로 계산하고, 접촉 후 5일 내 +5% 반등이 있었는지 확인
            touches = []
            last_j = -99
            for j in near_idx:
                if j - last_j < 5:
                    continue
                abs_j = max(0, i-60) + j
                end_j = min(i, abs_j + 6)
                base = float(x.Low.iloc[abs_j])
                bounced = end_j > abs_j + 1 and float(x.High.iloc[abs_j+1:end_j].max()) / base - 1 >= .05
                if bounced:
                    touches.append(abs_j)
                    last_j = j
            test_no = len(touches) + 1
            if test_no not in (2, 3, 4):
                continue
            support = float(np.median([float(x.Low.iloc[j]) for j in touches] + [cur_low]))
            # 현재도 지지존 근처에서 끝났는지. 완전 붕괴 종목은 제외하되 언더컷은 기록한다.
            if not (support * .95 <= cur_close <= support * 1.08):
                continue
            if last_signal is not None and (dt - last_signal).days < 10:
                continue
            entry = float(x.Open.iloc[i+1])
            if entry <= 0:
                continue
            undercut = cur_low / support - 1
            amount20 = float(x.Amt20.iloc[i]) if np.isfinite(x.Amt20.iloc[i]) else np.nan
            def rr(n):
                return float(x.Close.iloc[i+n] / entry - 1) if i+n < len(x) else np.nan
            rows.append({
                "Code": str(code), "Name": str(name), "Source": str(source), "SignalDate": dt,
                "TestNo": int(test_no), "Support": support, "Undercut": undercut,
                "Entry": entry, "StrongR20Max": float(pre.R20.max()), "StrongR60Max": float(pre.R60.max()),
                "Amount20": amount20, "R5": rr(5), "R10": rr(10), "R20": rr(20),
                "Period": "개발" if dt.year <= 2021 else "검증",
            })
            last_signal = dt
        return rows

    def _sv1_summary(df):
        if df is None or df.empty:
            return pd.DataFrame()
        out = []
        for (period, testno), g in df.groupby(["Period", "TestNo"]):
            r = {"구간": period, "지지테스트": f"{int(testno)}번째", "신호수": len(g)}
            for n in (5, 10, 20):
                z = pd.to_numeric(g[f"R{n}"], errors="coerce").dropna()
                r[f"R{n} 승률"] = float((z > 0).mean()) if len(z) else np.nan
                r[f"R{n} 평균"] = float(z.mean()) if len(z) else np.nan
                r[f"R{n} 중앙값"] = float(z.median()) if len(z) else np.nan
            out.append(r)
        return pd.DataFrame(out).sort_values(["구간", "지지테스트"])

    include_delisted = st.checkbox("상장폐지 종목 포함", value=True, key="sv1_delisted")
    batch = st.select_slider("한 번에 처리할 종목 수", options=[50,100,200,300,400,500], value=300, key="sv1_batch")
    restore = st.file_uploader("V1 체크포인트 복원", type=["gz"], key="sv1_restore")
    if restore is not None and st.button("체크포인트 복원", use_container_width=True, key="sv1_restore_btn"):
        SUPPORT_CKPT.write_bytes(restore.getvalue()); st.success("복원했습니다."); st.rerun()

    try:
        universe = _sv1_universe(include_delisted)
    except Exception as e:
        st.error(f"종목 목록 로드 실패: {e}"); universe = pd.DataFrame(columns=["Code","Name","Source"])
    ck = _sv1_load(SUPPORT_CKPT, {"done": {}, "rows": [], "include_delisted": include_delisted})
    done = ck.get("done", {}) if isinstance(ck, dict) else {}
    rows = ck.get("rows", []) if isinstance(ck, dict) else []
    total = len(universe); processed = len(done)
    a,b,c = st.columns(3); a.metric("전체 종목", f"{total:,}"); b.metric("완료", f"{processed:,}"); c.metric("신호", f"{len(rows):,}")
    if total:
        st.progress(min(processed/total,1.0), text=f"{processed:,}/{total:,} ({processed/total:.1%})")

    if st.button(f"▶ 다음 {batch}종목 구축", type="primary", use_container_width=True, disabled=(not total or processed>=total), key="sv1_build"):
        pending = universe[~universe.Code.astype(str).isin(done)].head(batch)
        bar=st.progress(0.0); status=st.empty(); t0=time.time()
        for k,(_,r) in enumerate(pending.iterrows(),1):
            code=str(r.Code)
            try:
                d=_sv1_price(code); add=_sv1_scan(code,str(r.Name),str(r.Source),d) if not d.empty else []
                rows.extend(add); done[code]=len(add)
            except Exception as e:
                done[code]=f"ERR:{type(e).__name__}"
            if k%10==0 or k==len(pending):
                _sv1_save(SUPPORT_CKPT,{"done":done,"rows":rows,"include_delisted":include_delisted,"rule":"support_v1_locked"})
            elapsed=time.time()-t0; rate=k/elapsed if elapsed else 0; eta=(len(pending)-k)/rate if rate else 0
            bar.progress(k/max(len(pending),1), text=f"이번 묶음 {k}/{len(pending)}")
            status.caption(f"현재 {r.Name} ({code}) · {rate:.2f}종목/초 · 약 {eta/60:.1f}분 남음")
        if len(done)>=total:
            _sv1_save(SUPPORT_RESULT,pd.DataFrame(rows))
        st.success("이번 묶음 완료"); st.rerun()

    if SUPPORT_CKPT.exists():
        st.download_button("💾 V1 체크포인트 내려받기", data=SUPPORT_CKPT.read_bytes(), file_name="kr_support_v1_checkpoint.pkl.gz", mime="application/gzip", use_container_width=True)
    sig = pd.DataFrame(rows)
    if not sig.empty:
        sm = _sv1_summary(sig)
        fm = {c:"{:.2%}" for c in sm.columns if "승률" in c or "평균" in c or "중앙값" in c}
        st.subheader("현재 결과 · 2/3/4번째 지지 테스트")
        st.dataframe(sm.style.format(fm, na_rep="-"), use_container_width=True, hide_index=True)
        payload={"format":"kr_support_v1_portable_v1","rule":"강한상승→±2% 반복지지→2/3/4번째 테스트","development":"2016-2021","validation":"2022-2023","signals":[]}
        for r in rows:
            q=dict(r); q["SignalDate"]=pd.Timestamp(q["SignalDate"]).strftime("%Y-%m-%d")
            for key,val in list(q.items()):
                if isinstance(val,(np.integer,)): q[key]=int(val)
                elif isinstance(val,(np.floating,)): q[key]=None if pd.isna(val) else float(val)
            payload["signals"].append(q)
        portable=gzip.compress(pickle.dumps(payload,protocol=4),compresslevel=3)
        st.download_button("📦 V1 분석용 파일 내려받기", data=portable, file_name="kr_support_v1_portable.pkl.gz", mime="application/gzip", use_container_width=True)
        st.caption("먼저 반복 지지 자체의 엣지만 봅니다. 대장주·테마·분봉 조건은 V1 결과를 본 뒤 별도로 추가합니다.")
    else:
        st.info("첫 묶음을 구축하면 2·3·4번째 지지 테스트 성과가 표시됩니다.")
    st.stop()

st.title("📈 SOXL QUANT V32 DUAL")
st.caption("C-ORIGINAL 원본 역추적 / C-ALPHA 장기 CAGR 연구를 분리 · 실전 체결관리 · 기록 복구")

AUTO_LOG_PATH = Path("quant_trade_log_autosave.csv")
POSITION_STATE_PATH = Path("quant_position_state.json")
BACKTEST_CHECKPOINT_DIR = Path(".quant_backtest_checkpoints")
BACKTEST_CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
BACKTEST_CHECKPOINT_EVERY = 20

def _checkpoint_signature(params):
    return hashlib.sha256(repr(params).encode("utf-8")).hexdigest()[:20]

def _checkpoint_path(market_key):
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(market_key))
    return BACKTEST_CHECKPOINT_DIR / f"backtest_{safe}.pkl.gz"

def _last_result_path(market_key):
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(market_key))
    return BACKTEST_CHECKPOINT_DIR / f"last_result_{safe}.pkl.gz"

def _save_backtest_checkpoint(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wb", compresslevel=3) as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)

def _load_backtest_checkpoint(path):
    try:
        if not path.exists():
            return None
        with gzip.open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None

def _delete_backtest_checkpoint(path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass

# SOXL 전용
market = "🇺🇸 미국장 — SOXL"
us_product = "SOXL"
soxl_track = st.radio(
    "🧭 SOXL 연구 트랙",
    ["C-ORIGINAL · 원본 주문 역추적", "C-ALPHA · CAGR 연구"],
    horizontal=True,
)
if soxl_track.startswith("C-ORIGINAL"):
    st.info("🔎 C-ORIGINAL · 원본 주문 역추적")
else:
    st.warning("🧪 C-ALPHA · 장기 CAGR 연구")
kokore_track = "제거됨"

currency = "$"
unit = "달러"
default_investment = 4000.0
min_investment = 1000.0
step_investment = 1000.0
default_tps = [0.10, 0.15, 0.20]

investment = st.number_input(
    f"💰 초기 투자금액 ({unit})",
    min_value=min_investment,
    value=default_investment,
    step=step_investment,
)

with st.expander("🛡️ 실전 주문 안전장치", expanded=True):
    emergency_stop = st.toggle(
        "긴급 매수 중지",
        value=False,
        help="켜면 매수 신호와 주문 안내를 모두 안전대기로 바꿉니다. 매도 신호는 유지합니다.",
    )
    safe1, safe2 = st.columns(2)
    max_daily_buy_pct = safe1.number_input(
        "1일 최대 매수비중(%)", min_value=1.0, max_value=100.0, value=25.0, step=1.0
    ) / 100
    max_total_deployed_pct = safe2.number_input(
        "총투입 한도(%)", min_value=10.0, max_value=100.0, value=90.0, step=5.0
    ) / 100
    safe3, safe4 = st.columns(2)
    max_quote_age_minutes = int(safe3.number_input(
        "최신시세 허용 지연(분)", min_value=1, max_value=10080, value=1440, step=10,
        help="미국장 마감 후 다음 장 준비가 가능하도록 기본값은 24시간입니다. 장중에는 20분 등으로 줄일 수 있습니다.",
    ))
    max_price_gap_pct = safe4.number_input(
        "일봉 대비 가격차 경고(%)", min_value=1.0, max_value=50.0, value=15.0, step=1.0
    ) / 100
    st.caption("안전장치는 백테스트 수익률이 아니라 실제 주문 안내와 매매기록 입력에 적용됩니다.")

st.info(
    "백테스트와 전략 신호 계산은 일봉 기준입니다. "
    "오늘 화면의 실제 거래 종목 현재가만 프리마켓/정규장/애프터마켓 최신 시세를 별도로 사용합니다."
)

with st.expander("⚙️ 백테스트 설정", expanded=False):
    # LOC 전용: 일반 종가주문 및 종가+LOC 혼합 탐색 제거
    us_execution_mode = "LOC 모드"
    loc_buy_ratios = [1.0]
    loc_buy_offset = 0.005
    loc_sell_offset = 0.0
    st.caption("🎯 체결방식: LOC 100% 고정")

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
        max_value=20,
        value=5,
        step=1,
        help="최대 20분할까지 비교합니다. 매수 간격×차수가 100% 이상이면 깊은 차수는 실제로 도달하기 어렵습니다.",
    )

    st.info(
        "각 차수에 전체 투자금의 몇 %를 투입할지 앱이 자동으로 비교합니다. "
        "선택된 차수별 비중의 합계는 100%를 넘지 않습니다."
    )
    deployment_ratios = [0.70, 0.80, 0.90, 1.00]
    st.caption(
        "총자금의 70%·80%·90%·100%만 운용하는 경우도 자동 비교하고, "
        "나머지는 현금으로 유지합니다."
    )

    st.markdown("**백테스트 기간**")
    backtest_period_mode = st.radio(
        "기간 선택",
        ["전체기간", "연도 범위", "직접 날짜 선택"],
        horizontal=True,
        help="전체기간 또는 원하는 특정 기간만 선택해서 백테스트할 수 있습니다.",
    )

    selected_start_year = None
    selected_end_year = None
    selected_start_date = None
    selected_end_date = None

    if backtest_period_mode == "연도 범위":
        y1, y2 = st.columns(2)
        selected_start_year = int(y1.number_input(
            "시작연도",
            min_value=2010,
            max_value=date.today().year,
            value=max(2016, date.today().year - 10),
            step=1,
        ))
        selected_end_year = int(y2.number_input(
            "종료연도",
            min_value=2010,
            max_value=date.today().year,
            value=date.today().year,
            step=1,
        ))
    elif backtest_period_mode == "직접 날짜 선택":
        d1, d2 = st.columns(2)
        selected_start_date = d1.date_input(
            "시작일",
            value=date(max(2016, date.today().year - 10), 1, 1),
        )
        selected_end_date = d2.date_input(
            "종료일",
            value=date.today(),
        )

    blind_validation_2026 = st.checkbox(
        "🧪 2026년 블라인드 검증",
        value=True,
        help=(
            "켜면 전략 최적화에는 2026년 데이터를 전혀 사용하지 않습니다. "
            "2025-12-31까지의 데이터로 1위 파라미터를 고른 뒤, 그 파라미터를 고정해 "
            "2026년 성과를 별도로 검증합니다."
        ),
    )
    if blind_validation_2026:
        st.info(
            "블라인드 모드: 최적화 데이터는 2025-12-31까지만 사용합니다. "
            "선정된 1위 전략은 파라미터 변경 없이 2026년 구간에 다시 적용합니다."
        )

    st.caption(
        "💡 실전 전략 선정 권장: 전체기간 결과를 기본으로 보고, 최근 3년·5년·10년에서도 "
        "비슷한 전략이 반복해서 상위권인지 확인하세요."
    )

    st.markdown("**필터 자동 비교**")
    ma_periods = st.multiselect(
        "비교할 이동평균선",
        [100, 150, 200],
        default=[100, 150, 200],
        format_func=lambda x: f"{x}일선",
        help="이평선 필터 없음도 자동으로 함께 비교합니다. 기준은 레버리지 ETF가 아니라 기초 ETF/본주입니다.",
    )
    use_rsi_candidates = st.checkbox("RSI 필터 비교", value=True)

    rsi_thresholds = st.multiselect(
        "비교할 RSI 상한",
        [30, 35, 40, 45, 50],
        default=[30, 35, 40, 45, 50],
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

    st.markdown("**💸 실전 거래비용**")
    cost1, cost2, cost3 = st.columns(3)
    fee_pct = cost1.number_input(
        "편도 수수료(%)", min_value=0.0, max_value=2.0, value=0.05, step=0.01
    ) / 100
    slippage_pct = cost2.number_input(
        "편도 슬리피지(%)", min_value=0.0, max_value=2.0, value=0.05, step=0.01
    ) / 100
    fx_cost_pct = cost3.number_input(
        "편도 환전비용(%)", min_value=0.0, max_value=2.0,
        value=0.10 if market.startswith("🇺🇸") else 0.0, step=0.01,
        disabled=not market.startswith("🇺🇸"),
    ) / 100

    st.markdown("**🛡️ 실전 전략 선정 기준**")
    risk1, risk2 = st.columns(2)
    min_completed_trades = int(risk1.number_input(
        "최소 완료매매 횟수", min_value=1, max_value=100, value=5, step=1
    ))
    max_allowed_mdd = risk2.number_input(
        "MDD 참고 기준(%)", min_value=10.0, max_value=99.0, value=60.0, step=5.0,
        help="CAGR 우선 모드에서는 탈락 조건으로 쓰지 않고 참고값으로만 표시합니다."
    ) / 100

if backtest_period_mode == "전체기간":
    st.caption("선택 기간: 전체 데이터 · 워크포워드 검증은 전체기간에서 실행하는 것을 권장합니다.")
elif backtest_period_mode == "연도 범위":
    st.caption(f"선택 기간: {selected_start_year}년 ~ {selected_end_year}년")
else:
    st.caption(f"선택 기간: {selected_start_date} ~ {selected_end_date}")

# TQQQ와 코코레의 실제 운용 사이클은 서로 독립적으로 잠급니다.
def _trade_market_key(row):
    market_value = str(row.get("시장", "")).strip()
    symbol_value = str(row.get("종목", "")).strip()

    if symbol_value.upper() in ("TQQQ", "SOXL"):
        return symbol_value.upper()
    if market_value in ("TQQQ", "SOXL"):
        return market_value
    if market_value in ("한국", "코코레") or symbol_value in ("233740.KS", "229200.KS"):
        return "코코레"
    return ""

def _is_market_cycle_locked(records, market_key):
    if not records:
        return False

    df = pd.DataFrame(records).copy()
    if df.empty or "구분" not in df.columns:
        return False

    df["_시장키"] = df.apply(_trade_market_key, axis=1)
    df = df[df["_시장키"] == market_key].copy()
    if df.empty:
        return False

    sell_mask = df["구분"].astype(str).eq("전량매도")
    sell_rows = df.index[sell_mask].tolist()
    cycle_df = df.loc[sell_rows[-1] + 1:] if sell_rows else df

    return bool(cycle_df["구분"].astype(str).str.contains("매수", na=False).any())

active_market_key = us_product if market.startswith("🇺🇸") else "코코레"
cycle_locked_for_ui = _is_market_cycle_locked(
    st.session_state.get("live_trades", []),
    active_market_key,
)

if cycle_locked_for_ui:
    st.info(
        f"🔒 {active_market_key} 현재 사이클 운용 중 — 전량매도까지 이 시장의 전략만 고정합니다. "
        "TQQQ와 코코레는 서로 독립적으로 운용됩니다."
    )
else:
    other_market_key = ("SOXL" if active_market_key == "TQQQ" else "TQQQ") if market.startswith("🇺🇸") else "TQQQ"
    if _is_market_cycle_locked(st.session_state.get("live_trades", []), other_market_key):
        st.caption(
            f"ℹ️ {other_market_key}는 현재 운용 중이지만, "
            f"{active_market_key}는 별도로 최적화할 수 있습니다."
        )

run_backtest = st.button(
    "🚀 다음 사이클 최적화" if not cycle_locked_for_ui else f"🔒 {active_market_key} 전략 운용 중",
    type="primary",
    use_container_width=True,
    disabled=cycle_locked_for_ui,
)

for key in ["backtest_result", "backtest_sims", "backtest_params"]:
    if key not in st.session_state:
        st.session_state[key] = None


@st.cache_data(ttl=900)
def download_close(symbols, market_day_key=None):
    """장기 일봉 + 최근 14일 별도 재조회. 최근 데이터가 있으면 장기 데이터의 끝부분을 덮어쓴다."""
    common = dict(
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )

    raw_long = yf.download(symbols, period="max", **common)
    # period=max 응답의 최신 구간이 늦는 경우가 있어 최근 구간을 별도로 재조회한다.
    raw_recent = yf.download(
        symbols,
        period="14d",
        **common,
    )

    if (raw_long is None or raw_long.empty) and (raw_recent is None or raw_recent.empty):
        raise ValueError("가격 데이터를 받지 못했습니다.")

    if raw_long is None or raw_long.empty:
        raw = raw_recent.copy()
    elif raw_recent is None or raw_recent.empty:
        raw = raw_long.copy()
    else:
        # 같은 날짜는 recent가 우선하도록 합친다.
        raw = pd.concat([raw_long, raw_recent])
        raw = raw[~raw.index.duplicated(keep="last")].sort_index()

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

    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.sort_index()


@st.cache_data(ttl=300)
def download_recent_intraday_daily_closes(symbols, market_day_key=None):
    """최근 7일 1시간봉을 미국 거래일별 마지막 정규장 가격로 집계해 일봉 종가 보완용으로 사용."""
    frames = {}
    for symbol in symbols:
        try:
            h = yf.Ticker(symbol).history(
                period="7d", interval="1h", prepost=False, auto_adjust=True
            )
            if h is None or h.empty or "Close" not in h.columns:
                continue
            s = h["Close"].dropna()
            if s.empty:
                continue
            idx = pd.DatetimeIndex(s.index)
            # yfinance US symbols normally return America/New_York-aware timestamps.
            try:
                if idx.tz is None:
                    idx = idx.tz_localize("America/New_York")
                else:
                    idx = idx.tz_convert("America/New_York")
            except Exception:
                pass
            tmp = pd.DataFrame({"Close": s.to_numpy()}, index=idx)
            tmp["TradeDate"] = pd.DatetimeIndex(tmp.index).date
            daily = tmp.groupby("TradeDate")["Close"].last()
            daily.index = pd.to_datetime(daily.index)
            frames[symbol] = daily
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).sort_index()

def merge_recent_fallback(close, symbols, market_day_key=None):
    """일봉이 늦으면 최근 1시간봉 집계값으로 누락 거래일만 보완한다."""
    base = close.copy()
    recent = download_recent_intraday_daily_closes(symbols, market_day_key)
    if recent is None or recent.empty:
        return base, False, None
    common = [s for s in symbols if s in recent.columns]
    if len(common) != len(symbols):
        return base, False, None
    complete = recent[common].dropna()
    if complete.empty:
        return base, False, None

    # 진행 중인 오늘 ET 거래일은 절대 보완하지 않는다. 전일까지의 완결된 거래일만 사용.
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        complete = complete[pd.DatetimeIndex(complete.index).date < now_et.date()]
    except Exception:
        pass
    if complete.empty:
        return base, False, None

    before = pd.Timestamp(base.dropna().index[-1]).date() if len(base.dropna()) else None
    for dt, row in complete.iterrows():
        for s in symbols:
            base.loc[pd.Timestamp(dt), s] = float(row[s])
    base = base.sort_index()
    after = pd.Timestamp(base[common].dropna().index[-1]).date()
    return base, (after != before), after



def confirmed_us_daily_series(series):
    """미국 정규장 종료(ET 16:00) 전에는 오늘 진행 중인 일봉을 절대 신호에 사용하지 않는다."""
    s = pd.Series(series).dropna().copy()
    if s.empty:
        return s

    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        last_date = pd.Timestamp(s.index[-1]).date()

        # yfinance가 장중 '오늘' 일봉을 반환하면 제거한다.
        # ET 16:00 이후에만 오늘 일봉을 확정 데이터로 허용한다.
        if last_date == now_et.date() and now_et.time() < datetime.strptime("16:00", "%H:%M").time():
            s = s.iloc[:-1]

        # 미래 날짜/비정상 날짜가 섞여도 현재 ET 날짜 이후 데이터는 사용하지 않는다.
        if not s.empty:
            idx_dates = pd.DatetimeIndex(s.index).date
            s = s.loc[idx_dates <= now_et.date()]
    except Exception:
        # 시간대 판정 실패 시 원본을 훼손하지 않는다.
        pass

    return s


@st.cache_data(ttl=900)
def download_soxl_ohlc_research():
    """SOXL 지정가 체결 검증용 일봉 OHLC + QQQ/SMH 종가 데이터."""
    raw = yf.download(
        ["SOXL", "QQQ", "SMH"],
        period="max",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    if raw is None or raw.empty:
        raise ValueError("SOXL OHLC 데이터를 받지 못했습니다.")

    def _field(field, symbol):
        if isinstance(raw.columns, pd.MultiIndex):
            lv0 = raw.columns.get_level_values(0)
            lvl = raw.columns.get_level_values(-1)
            if field in lv0:
                obj = raw[field]
                return obj[symbol] if isinstance(obj, pd.DataFrame) else obj
            if field in lvl:
                obj = raw.xs(field, axis=1, level=-1)
                return obj[symbol] if isinstance(obj, pd.DataFrame) else obj
        elif field in raw.columns and symbol == "SOXL":
            return raw[field]
        raise ValueError(f"{symbol} {field} 데이터를 찾지 못했습니다.")

    df = pd.DataFrame(index=raw.index)
    df["SOXL_Open"] = _field("Open", "SOXL")
    df["SOXL_High"] = _field("High", "SOXL")
    df["SOXL_Low"] = _field("Low", "SOXL")
    df["SOXL_Close"] = _field("Close", "SOXL")
    df["QQQ"] = _field("Close", "QQQ")
    df["SMH"] = _field("Close", "SMH")
    df = df.dropna().copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df


@st.cache_data(ttl=30)
def get_latest_market_price(symbol):
    """오늘 화면용 최신 시세. 프리/애프터마켓 포함 1분봉 우선."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            period="1d",
            interval="1m",
            prepost=True,
            auto_adjust=True,
        )
        if hist is not None and not hist.empty:
            close_s = hist["Close"].dropna()
            if not close_s.empty:
                return float(close_s.iloc[-1]), "1분봉(프리/애프터 포함)", close_s.index[-1]
    except Exception:
        pass

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            period="5d",
            interval="5m",
            prepost=True,
            auto_adjust=True,
        )
        if hist is not None and not hist.empty:
            close_s = hist["Close"].dropna()
            if not close_s.empty:
                return float(close_s.iloc[-1]), "5분봉(프리/애프터 포함)", close_s.index[-1]
    except Exception:
        pass

    return None, "최근 일봉", None


def format_quote_time(ts):
    if ts is None:
        return None
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("America/New_York")
        ny = t.tz_convert("America/New_York")
        kr = t.tz_convert("Asia/Seoul")
        return f"미국 동부 {ny:%Y-%m-%d %H:%M} / 한국 {kr:%Y-%m-%d %H:%M}"
    except Exception:
        return str(ts)


def apply_backtest_period(df, mode, start_year=None, end_year=None, start_date=None, end_date=None):
    out = df.copy()

    if mode == "연도 범위":
        if start_year is None or end_year is None:
            return out
        if int(start_year) > int(end_year):
            raise ValueError("시작연도는 종료연도보다 늦을 수 없습니다.")
        start_ts = pd.Timestamp(f"{int(start_year)}-01-01")
        end_ts = pd.Timestamp(f"{int(end_year)}-12-31")
        out = out.loc[(out.index >= start_ts) & (out.index <= end_ts)]

    elif mode == "직접 날짜 선택":
        if start_date is None or end_date is None:
            return out
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        if start_ts > end_ts:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        out = out.loc[(out.index >= start_ts) & (out.index <= end_ts)]

    return out


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


def filter_name(ma_period, rsi_max):
    parts = []
    if ma_period is not None:
        parts.append(f"{int(ma_period)}일선")
    if rsi_max is not None:
        parts.append(f"RSI≤{int(rsi_max)}")
    return "기본" if not parts else " + ".join(parts)


def entry_allowed(row, ma_period, rsi_max):
    if ma_period is not None:
        trend_col = f"TREND_OK_{int(ma_period)}"
        if trend_col not in row.index or not bool(row[trend_col]):
            return False

    if rsi_max is not None:
        rsi = row["RSI14"]
        if not np.isfinite(rsi) or rsi > rsi_max:
            return False

    return True


def make_allocation_candidates(n_tranches):
    """총투자금 대비 차수별 매수비중 후보를 자동 생성합니다."""
    if int(n_tranches) < 1:
        raise ValueError("분할매수 횟수는 1 이상이어야 합니다.")

    positions = np.linspace(-1.0, 1.0, int(n_tranches))
    # 음수는 초반 투입 확대, 양수는 후반 투입 확대입니다. 화면에는 이름 대신
    # 백테스트가 실제 선택한 총자산 대비 %만 표시합니다.
    slopes = [-1.50, -1.00, -0.50, 0.0, 0.50, 1.00, 1.50]
    candidates = []
    for slope in slopes:
        raw = np.exp(slope * positions)
        weights = raw / raw.sum()
        candidates.append(tuple(float(x) for x in weights))
    return candidates


def normalize_allocation_weights(n_tranches, allocation_weights=None):
    if allocation_weights is None:
        return np.ones(int(n_tranches), dtype=float) / int(n_tranches)
    weights = np.asarray(allocation_weights, dtype=float)
    if len(weights) != int(n_tranches) or not np.all(np.isfinite(weights)):
        raise ValueError("차수별 매수비중 데이터가 올바르지 않습니다.")
    weights = np.clip(weights, 0.0, None)
    if weights.sum() <= 0:
        raise ValueError("차수별 매수비중 합계는 0보다 커야 합니다.")
    return weights / weights.sum()


def allocation_text(allocation_weights):
    return " / ".join(f"{float(x):.1%}" for x in allocation_weights)


def serialize_allocation(allocation_weights):
    return "|".join(f"{float(x):.10f}" for x in allocation_weights)


def parse_saved_allocation(raw_value, n_tranches):
    """새 CSV의 실제 %를 읽고, 예전 CSV도 현재 사이클에 한해 호환합니다."""
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        values = [float(x) for x in text.split("|")]
        return normalize_allocation_weights(n_tranches, values)
    except (TypeError, ValueError):
        pass

    legacy = {
        "균등비중": np.ones(int(n_tranches)),
        "약한 가중": np.linspace(1.0, 2.0, int(n_tranches)),
        "강한 가중": np.linspace(1.0, 3.0, int(n_tranches)),
    }
    if text in legacy:
        return normalize_allocation_weights(n_tranches, legacy[text])
    return None


def make_tranche_budgets(initial_cash, n_tranches, allocation_weights=None):
    weights = normalize_allocation_weights(n_tranches, allocation_weights)
    return (float(initial_cash) * weights).tolist()


def _remote_store_config():
    """선택형 Supabase 저장소 설정을 읽습니다. 설정이 없으면 로컬 저장만 사용합니다."""
    try:
        cfg = st.secrets.get("trade_store", {})
        url = str(cfg.get("url", "")).rstrip("/")
        key = str(cfg.get("key", ""))
        user_key = str(cfg.get("user_key", ""))
        if url and key and user_key:
            return url, key, user_key
    except Exception:
        pass
    return None


def _remote_request(method, records=None):
    cfg = _remote_store_config()
    if cfg is None:
        return None
    url, key, user_key = cfg
    encoded_key = urllib.parse.quote(user_key, safe="")
    endpoint = f"{url}/rest/v1/quant_trade_logs"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if method == "GET":
        request = urllib.request.Request(
            f"{endpoint}?user_key=eq.{encoded_key}&select=records",
            headers=headers,
            method="GET",
        )
    else:
        payload = json.dumps({
            "user_key": user_key,
            "records": records or [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False).encode("utf-8")
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        request = urllib.request.Request(
            f"{endpoint}?on_conflict=user_key", data=payload, headers=headers, method="POST"
        )
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else True


def save_remote_trades(records):
    try:
        return _remote_request("POST", records) is not None
    except Exception:
        return False


def load_remote_trades():
    try:
        result = _remote_request("GET")
        if isinstance(result, list) and result:
            records = result[0].get("records", [])
            return records if isinstance(records, list) else []
    except Exception:
        pass
    return []


def trade_log_checksum(records):
    raw = json.dumps(records or [], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]



BLIND_TEST_PATH = Path("soxl_blind_20d.csv")

def load_blind_test():
    cols=["date","our1","our2","original1","original2","original_weight1","original_weight2","match1","match2","err1_pct","err2_pct","any_exact"]
    try:
        if BLIND_TEST_PATH.exists() and BLIND_TEST_PATH.stat().st_size:
            df=pd.read_csv(BLIND_TEST_PATH)
            for c in cols:
                if c not in df.columns: df[c]=np.nan
            return df[cols]
    except Exception:
        pass
    return pd.DataFrame(columns=cols)

def lock_blind_prices(trade_date, our1, our2, sell1=np.nan, sell2=np.nan, our_weight1=np.nan, our_weight2=np.nan):
    d=str(trade_date); df=load_blind_test()
    _new_cols={"our_sell1":np.nan,"our_sell2":np.nan,"our_weight1":np.nan,"our_weight2":np.nan,
               "original_sell1":np.nan,"original_sell2":np.nan,"original_sell_weight1":np.nan,
               "original_sell_weight2":np.nan,"sell_err1_pct":np.nan,"sell_err2_pct":np.nan,"sell_any_exact":False}
    for _c,_v in _new_cols.items():
        if _c not in df.columns: df[_c]=_v
    if len(df) and (df["date"].astype(str)==d).any():
        _i=df.index[df["date"].astype(str)==d][-1]
        df.at[_i,"our_sell1"]=round(float(sell1),2) if pd.notna(sell1) else np.nan
        df.at[_i,"our_sell2"]=round(float(sell2),2) if pd.notna(sell2) else np.nan
        df.at[_i,"our_weight1"]=float(our_weight1) if pd.notna(our_weight1) else np.nan
        df.at[_i,"our_weight2"]=float(our_weight2) if pd.notna(our_weight2) else np.nan
        df.to_csv(BLIND_TEST_PATH,index=False)
        return df
    row={"date":d,"our1":round(float(our1),2),"our2":round(float(our2),2),
         "our_sell1":round(float(sell1),2) if pd.notna(sell1) else np.nan,
         "our_sell2":round(float(sell2),2) if pd.notna(sell2) else np.nan,
         "our_weight1":float(our_weight1) if pd.notna(our_weight1) else np.nan,
         "our_weight2":float(our_weight2) if pd.notna(our_weight2) else np.nan,
         "original1":np.nan,"original2":np.nan,"original_weight1":np.nan,"original_weight2":np.nan,
         "original_sell1":np.nan,"original_sell2":np.nan,"original_sell_weight1":np.nan,"original_sell_weight2":np.nan,
         "match1":np.nan,"match2":np.nan,"err1_pct":np.nan,"err2_pct":np.nan,
         "sell_err1_pct":np.nan,"sell_err2_pct":np.nan,"any_exact":False,"sell_any_exact":False}
    df=pd.concat([df,pd.DataFrame([row])],ignore_index=True)
    df.to_csv(BLIND_TEST_PATH,index=False); return df

def save_blind_original(trade_date, original1, original2, original_weight1=np.nan, original_weight2=np.nan,
                        original_sell1=np.nan, original_sell2=np.nan,
                        original_sell_weight1=np.nan, original_sell_weight2=np.nan):
    d=str(trade_date); df=load_blind_test(); mask=df["date"].astype(str)==d
    if not mask.any(): return df
    i=df.index[mask][-1]
    a,b=float(df.at[i,"our1"]),float(df.at[i,"our2"])
    x,y=float(original1),float(original2)
    m1,m2=(y,x) if abs(a-y)+abs(b-x)<abs(a-x)+abs(b-y) else (x,y)
    e1=abs(a-m1)/m1 if m1 else np.nan; e2=abs(b-m2)/m2 if m2 else np.nan
    df.at[i,"original1"],df.at[i,"original2"]=x,y
    df.at[i,"original_weight1"],df.at[i,"original_weight2"]=float(original_weight1),float(original_weight2)
    df.at[i,"match1"],df.at[i,"match2"]=m1,m2
    df.at[i,"err1_pct"],df.at[i,"err2_pct"]=e1,e2
    df.at[i,"any_exact"]=(round(a,2)==round(m1,2)) or (round(b,2)==round(m2,2))

    if pd.notna(original_sell1) and pd.notna(original_sell2) and float(original_sell1)>0 and float(original_sell2)>0:
        sa,sb=float(df.at[i,"our_sell1"]),float(df.at[i,"our_sell2"])
        sx,sy=float(original_sell1),float(original_sell2)
        sm1,sm2=(sy,sx) if abs(sa-sy)+abs(sb-sx)<abs(sa-sx)+abs(sb-sy) else (sx,sy)
        df.at[i,"original_sell1"],df.at[i,"original_sell2"]=sx,sy
        df.at[i,"original_sell_weight1"],df.at[i,"original_sell_weight2"]=float(original_sell_weight1),float(original_sell_weight2)
        df.at[i,"sell_err1_pct"]=abs(sa-sm1)/sm1 if sm1 else np.nan
        df.at[i,"sell_err2_pct"]=abs(sb-sm2)/sm2 if sm2 else np.nan
        df.at[i,"sell_any_exact"]=(round(sa,2)==round(sm1,2)) or (round(sb,2)==round(sm2,2))
    df.to_csv(BLIND_TEST_PATH,index=False); return df

def load_position_state():
    try:
        if POSITION_STATE_PATH.exists() and POSITION_STATE_PATH.stat().st_size:
            data = json.loads(POSITION_STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def save_position_state(symbol, qty, avg_price, lots=None):
    data = load_position_state()
    old = data.get(str(symbol), {}) if isinstance(data, dict) else {}
    data[str(symbol)] = {
        "qty": float(qty or 0),
        "avg_price": float(avg_price or 0),
        "lots": list(lots if lots is not None else old.get("lots", [])),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = POSITION_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POSITION_STATE_PATH)
    return True

def add_position_lot(symbol, price, qty, label=""):
    data = load_position_state()
    pos = data.get(symbol, {})
    lots = list(pos.get("lots", []))
    lots.append({"price": float(price), "qty": float(qty), "label": str(label),
                 "filled_at": datetime.now(timezone.utc).isoformat()})
    total_qty = sum(float(x.get("qty", 0)) for x in lots)
    total_cost = sum(float(x.get("price", 0))*float(x.get("qty", 0)) for x in lots)
    avg = total_cost/total_qty if total_qty > 0 else 0.0
    save_position_state(symbol, total_qty, avg, lots)
    return total_qty, avg

def close_position_lot(symbol, lot_index):
    data = load_position_state()
    pos = data.get(symbol, {})
    lots = list(pos.get("lots", []))
    if 0 <= int(lot_index) < len(lots):
        lots.pop(int(lot_index))
    total_qty = sum(float(x.get("qty", 0)) for x in lots)
    total_cost = sum(float(x.get("price", 0))*float(x.get("qty", 0)) for x in lots)
    avg = total_cost/total_qty if total_qty > 0 else 0.0
    save_position_state(symbol, total_qty, avg, lots)
    return total_qty, avg

def autosave_live_trades(records):
    """앱 재실행 시 복구할 수 있도록 매매기록을 즉시 저장합니다."""
    local_ok = False
    try:
        pd.DataFrame(records).to_csv(AUTO_LOG_PATH, index=False, encoding="utf-8-sig")
        local_ok = True
    except Exception:
        pass
    remote_ok = save_remote_trades(records) if _remote_store_config() else None
    st.session_state["last_save_status"] = {
        "local": local_ok,
        "remote": remote_ok,
        "checksum": trade_log_checksum(records),
        "time": datetime.now(timezone.utc).isoformat(),
    }
    return bool(local_ok or remote_ok)


def load_autosaved_trades():
    remote = load_remote_trades() if _remote_store_config() else []
    if remote:
        return remote
    if not AUTO_LOG_PATH.exists() or AUTO_LOG_PATH.stat().st_size == 0:
        return []
    try:
        return pd.read_csv(AUTO_LOG_PATH).to_dict("records")
    except Exception:
        return []





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
            if hit_tp or forced or alpha_cash:
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
        if i > 0:
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

def simulate(
    df,
    initial_cash,
    step_pct,
    take_profit,
    n_tranches,
    ma_period,
    rsi_max,
    allocation_weights=None,
    max_hold_days=None,
    execution_mode="일반 종가모드",
    loc_buy_offset=0.0,
    loc_sell_offset=0.0,
    loc_buy_ratio=0.0,
    fee_pct=0.0,
    slippage_pct=0.0,
    fx_cost_pct=0.0,
    deployment_ratio=1.0,
):
    if market.startswith("🇺🇸") and us_product == "SOXL":
        return simulate_soxl_reverse(
            df=df,
            initial_cash=initial_cash,
            base_unit=float(step_pct),
            take_profit=float(take_profit),
            max_hold_days=max_hold_days,
            fee_pct=float(fee_pct),
            slippage_pct=float(slippage_pct),
            fx_cost_pct=float(fx_cost_pct),
            deployment_ratio=float(deployment_ratio),
        )

    cash = float(initial_cash)
    shares = 0.0
    total_cost = 0.0
    entries = []
    trades = []
    equity_rows = []
    next_level = 1
    pending_buy = None
    pending_exit = None
    tranche_budgets = make_tranche_budgets(
        initial_cash * deployment_ratio, n_tranches, allocation_weights
    )

    idx = df.index
    signal_arr = df["SIGNAL"].to_numpy(dtype=float)
    trade_arr = df["TRADE"].to_numpy(dtype=float)
    prev_trade_arr = df["TRADE"].shift(1).to_numpy(dtype=float)
    anchor_arr = df["ANCHOR"].to_numpy(dtype=float)
    trend_arr = (
        df[f"TREND_OK_{int(ma_period)}"].to_numpy(dtype=bool)
        if ma_period is not None
        else None
    )
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

        # 전일 종가로 확정된 신호만 오늘 종가에 체결합니다.
        if pending_exit is not None and shares > 0:
            sell_fill_ok = (
                pending_exit["reason"] == "기간청산"
                or execution_mode != "LOC 모드"
                or trade_price >= pending_exit["limit"]
            )
            if sell_fill_ok:
                avg_price = total_cost / shares
                holding_days = (dt - pd.Timestamp(entries[0][0])).days if entries else 0
                effective_sell_price = trade_price * (1 - slippage_pct)
                gross = shares * effective_sell_price
                proceeds = gross * (1 - fee_pct - fx_cost_pct)
                realized = proceeds - total_cost
                pnl_pct = proceeds / total_cost - 1 if total_cost > 0 else 0.0
                cash += proceeds
                trades.append({
                    "신호일": pending_exit["signal_date"], "매도일": dt,
                    "평균매수가": avg_price, "매도가": effective_sell_price,
                    "수익률": pnl_pct, "실현손익": realized,
                    "보유일수": holding_days, "매수횟수": len(entries),
                    "청산사유": pending_exit["reason"],
                })
                shares, total_cost, entries, next_level = 0.0, 0.0, [], 1
                pending_exit = None
                pending_buy = None
                exited_today = True

        if pending_buy is not None and not exited_today and pending_exit is None:
            level = int(pending_buy["level"])
            if level == next_level and level <= n_tranches:
                planned_budget = tranche_budgets[level - 1]
                close_budget = planned_budget * (1 - loc_buy_ratio)
                loc_fill = False
                prev_trade_price = prev_trade_arr[i]
                if loc_buy_ratio > 0 and np.isfinite(prev_trade_price) and prev_trade_price > 0:
                    loc_limit = prev_trade_price * (1 - loc_buy_offset)
                    loc_fill = trade_price <= loc_limit
                loc_budget = planned_budget * loc_buy_ratio if loc_fill else 0.0
                budget = min(close_budget + loc_budget, cash)
                if budget > 0:
                    effective_buy_price = trade_price * (1 + slippage_pct)
                    unit_cash_cost = effective_buy_price * (1 + fee_pct + fx_cost_pct)
                    qty = budget / unit_cash_cost
                    shares += qty
                    total_cost += budget
                    cash -= budget
                    entries.append((dt, effective_buy_price, budget, level))
                    next_level += 1
            pending_buy = None

        allowed = True
        if ma_period is not None and not trend_arr[i]:
            allowed = False
        if rsi_max is not None and (not np.isfinite(rsi_arr[i]) or rsi_arr[i] > rsi_max):
            allowed = False

        # 오늘 종가로 신호를 확정하고 다음 거래일 주문으로 넘깁니다.
        if shares > 0 and pending_exit is None and not exited_today:
            avg_price = total_cost / shares
            first_buy_date = entries[0][0] if entries else dt
            holding_days = (dt - pd.Timestamp(first_buy_date)).days
            target_sell_price = avg_price * (1 + take_profit)
            if trade_price >= target_sell_price:
                pending_exit = {
                    "reason": "익절", "signal_date": dt,
                    "limit": target_sell_price * (1 + loc_sell_offset),
                }
            elif max_hold_days is not None and holding_days >= max_hold_days:
                pending_exit = {"reason": "기간청산", "signal_date": dt, "limit": 0.0}

        if (
            pending_exit is None and allowed and not exited_today
            and next_level <= n_tranches
            and drawdown + 1e-12 >= step_pct * next_level
        ):
            pending_buy = {"level": next_level, "signal_date": dt}

        equity = cash + shares * trade_price * (1 - slippage_pct) * (1 - fee_pct - fx_cost_pct)
        invested_value = shares * trade_price * (1 - slippage_pct) * (1 - fee_pct - fx_cost_pct)
        exposure = invested_value / equity if equity > 0 else 0.0
        equity_rows.append(
            (dt, equity, cash, shares, signal_price, trade_price, drawdown, exposure)
        )

    if not equity_rows:
        raise ValueError("백테스트에 사용할 데이터가 없습니다.")

    eq = pd.DataFrame(
        equity_rows,
        columns=[
            "Date", "Equity", "Cash", "Shares",
            "SignalPrice", "TradePrice", "Drawdown", "Exposure",
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
    avg_exposure = float(eq["Exposure"].mean())
    max_exposure = float(eq["Exposure"].max())

    return {
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "mdd": mdd,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_hold": avg_hold,
        "max_hold": max_hold,
        "avg_exposure": avg_exposure,
        "max_exposure": max_exposure,
        "deployment_ratio": float(deployment_ratio),
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
        "allocation_weights": normalize_allocation_weights(
            n_tranches, allocation_weights
        ).tolist(),
    }


def get_kokore_limit_plan(lev_series, base_series, exposure_now=0.0):
    """K-ALPHA 연구용: 전일 확정 일봉 -> 다음 거래일 지정가 2개/비중.
    미래값은 사용하지 않는다. SOXL 함수와 완전히 독립적이다.
    """
    df = pd.DataFrame({"LEV": lev_series, "BASE": base_series}).dropna().copy()
    if len(df) < 210:
        raise ValueError("K-ALPHA 계산에는 최소 210거래일 데이터가 필요합니다.")

    df["MA20"] = df["BASE"].rolling(20).mean()
    df["MA50"] = df["BASE"].rolling(50).mean()
    df["MA200"] = df["BASE"].rolling(200).mean()
    df["RSI14"] = calc_rsi(df["BASE"], 14)
    df["DD60"] = df["LEV"] / df["LEV"].rolling(60).max() - 1.0
    df["RET5"] = df["BASE"].pct_change(5)
    df["SLOPE20_5"] = df["MA20"].pct_change(5)
    row = df.dropna().iloc[-1]

    risk = 0
    risk += int(row["BASE"] < row["MA20"])
    risk += int(row["BASE"] < row["MA50"])
    risk += int(row["BASE"] < row["MA200"]) * 2
    risk += int(row["MA20"] < row["MA50"])
    risk += int(row["RET5"] < -0.04)
    risk += int(row["DD60"] < -0.20)
    risk += int(row["SLOPE20_5"] < 0)

    rebound = (
        row["BASE"] > row["MA20"]
        and row["SLOPE20_5"] > 0
        and row["RSI14"] >= 45
    )

    # 1차 연구 파라미터. 아래 백테스트에서 검증 후에만 잠글 값이다.
    if risk >= 6 and not rebound:
        state, offsets, weights = "K-BEAR", (-0.04, -0.08), (0.03, 0.04)
    elif rebound and risk <= 4:
        state, offsets, weights = "K-REBOUND", (-0.01, -0.035), (0.10, 0.10)
    elif risk <= 2:
        state, offsets, weights = "K-STRONG", (-0.015, -0.04), (0.10, 0.10)
    else:
        state, offsets, weights = "K-NORMAL", (-0.025, -0.055), (0.07, 0.08)

    room = max(0.0, 1.0 - float(exposure_now))
    w1 = min(weights[0], room)
    room -= w1
    w2 = min(weights[1], room)

    return {
        "state": state,
        "risk_score": int(risk),
        "prev_close": float(row["LEV"]),
        "offsets": offsets,
        "weights": (w1, w2),
        "rsi14": float(row["RSI14"]),
        "base_close": float(row["BASE"]),
    }


try:
    # ------------------------------------------------------------
    # 데이터 / 비교할 운용 방식
    # ------------------------------------------------------------
    if market.startswith("🇺🇸"):
        symbols = [us_product, "QQQ"] if us_product == "TQQQ" else ["SOXL", "QQQ", "SMH"]
        try:
            from zoneinfo import ZoneInfo
            _market_day_key = datetime.now(timezone.utc).astimezone(
                ZoneInfo("America/New_York")
            ).strftime("%Y-%m-%d")
        except Exception:
            _market_day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        close = download_close(symbols, _market_day_key).dropna()
        close, _used_intraday_fallback, _fallback_last_date = merge_recent_fallback(
            close, symbols, _market_day_key
        )
        close = close.dropna()

        # SOXL 연구용 원천 일봉을 한 번 저장해두면 이후에는 ChatGPT 쪽에서
        # Streamlit 재실행 없이 같은 데이터로 C-ORIGINAL / C-ALPHA를 반복 검증할 수 있습니다.
        if us_product == "SOXL":
            export_df = close[["SOXL", "QQQ", "SMH"]].copy()
            export_df.index.name = "Date"
            export_csv = export_df.reset_index().to_csv(index=False).encode("utf-8-sig")
            with st.expander("📦 SOXL·QQQ·SMH 연구 데이터 저장", expanded=False):
                st.caption(
                    "이 CSV를 한 번 저장해두면 이후 백테스트는 같은 가격데이터로 바로 반복할 수 있습니다. "
                    "원본 일봉만 담고 전략 결과나 8월 주문 정답은 포함하지 않습니다."
                )
                st.download_button(
                    "⬇️ SOXL_QQQ_SMH 일봉 CSV 받기",
                    data=export_csv,
                    file_name="SOXL_QQQ_SMH_daily.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="download_soxl_research_csv",
                )
                st.caption(
                    f"데이터 범위: {export_df.index.min().date()} ~ {export_df.index.max().date()} · "
                    f"{len(export_df):,} 거래일"
                )

                st.divider()
                st.markdown("**🎯 LOC + 지정가 혼합 백테스트용 OHLC**")
                st.caption(
                    "SOXL의 Open/High/Low/Close와 QQQ·SMH 종가를 저장합니다. "
                    "지정가 매수는 당일 Low, 지정가 매도는 당일 High로 실제 체결 여부를 검증합니다."
                )
                try:
                    ohlc_df = download_soxl_ohlc_research()
                    ohlc_csv = ohlc_df.reset_index().to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "⬇️ SOXL OHLC 지정가 검증 CSV 받기",
                        data=ohlc_csv,
                        file_name="SOXL_OHLC_QQQ_SMH_daily.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key="download_soxl_ohlc_research_csv",
                    )
                    st.caption(
                        f"OHLC 범위: {ohlc_df.index.min().date()} ~ {ohlc_df.index.max().date()} · "
                        f"{len(ohlc_df):,} 거래일"
                    )
                except Exception as e:
                    st.warning(f"OHLC 다운로드 실패: {e}")

        modes = [
            {
                 "mode": f"{us_product} 신호 → {us_product} 매수",
                "signal_symbol": us_product,
                "trade_symbol": us_product,
                "ref_symbol": ("QQQ" if us_product == "TQQQ" else "SMH"),
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

        if kokore_track.startswith("K-ALPHA"):
            try:
                _kplan = get_kokore_limit_plan(
                    close["233740.KS"], close["229200.KS"], exposure_now=0.0
                )
                _kprev = float(_kplan["prev_close"])
                _kbuy = [_kprev * (1.0 + float(x)) for x in _kplan["offsets"]]
                # 연구 시작값: 각 LOT +6% 목표. 백테스트 결과 전에는 확정 파라미터로 취급하지 않는다.
                _ktp = 0.06
                _ksell = [x * (1.0 + _ktp) for x in _kbuy]

                st.markdown("## 🧪 K-ALPHA 코코레 다음 거래일 연구 주문")
                st.warning("아직 **연구용 1차 파라미터**입니다. 백테스트로 검증하기 전에는 실전 확정 신호가 아닙니다.")
                st.caption(
                    f"상태 **{_kplan['state']}** · 위험점수 {_kplan['risk_score']}/8 · "
                    f"코코레 확정종가 ₩{_kprev:,.0f} · 본주 RSI {_kplan['rsi14']:.1f}"
                )
                _krows = []
                for _i, (_px, _w) in enumerate(zip(_kbuy, _kplan["weights"]), 1):
                    _krows.append({
                        "주문": f"매수 {_i}", "방식": "지정가",
                        "가격": f"₩{_px:,.0f}", "총자산 비중": f"{float(_w):.1%}",
                        "주문금액": f"₩{float(investment)*float(_w):,.0f}",
                    })
                for _i, _px in enumerate(_ksell, 1):
                    _krows.append({
                        "주문": f"매도 {_i}", "방식": "지정가",
                        "가격": f"₩{_px:,.0f}", "총자산 비중": "해당 LOT",
                        "주문금액": "보유수량 기준",
                    })
                st.dataframe(pd.DataFrame(_krows), use_container_width=True, hide_index=True)
            except Exception as _ke:
                st.warning(f"K-ALPHA 연구 주문 계산 실패: {_ke}")

    # 필터 조합: 이평선 없음 + 100/150/200일선, RSI 없음 + 선택값
    ma_candidates = [None] + [int(x) for x in ma_periods]
    rsi_candidates = [None]
    if use_rsi_candidates:
        rsi_candidates += [float(x) for x in rsi_thresholds]

    filter_candidates = [
        (ma_period, rsi_max)
        for ma_period in ma_candidates
        for rsi_max in rsi_candidates
    ]

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

    allocation_candidates = make_allocation_candidates(tranche_count)

    # SOXL은 기존 고점대비 분할매수 엔진 대신 주문표 역추적 LOC 엔진을 사용합니다.
    # 화면의 "매수 간격" 값은 SOXL에서 '1개 LOC 주문의 총자산 대비 비중'으로 해석됩니다.
    if market.startswith("🇺🇸") and us_product == "SOXL":
        # C-ORIGINAL은 최적화가 아니라 원본 주문 재현이 목적이므로 단 하나의 고정 후보만 실행합니다.
        # C-ALPHA에서만 여러 조합을 탐색합니다.
        effective_buy_steps = [0.06]
        filter_candidates = [(None, None)]
        allocation_candidates = [np.ones(int(tranche_count)) / int(tranche_count)]
        loc_buy_ratios = [1.0]
        if soxl_track.startswith("C-ORIGINAL"):
            effective_take_profits = [0.06]
            effective_max_holds = [180]
            deployment_ratios = [1.00]
        else:
            effective_take_profits = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12]
            effective_max_holds = [45, 60, 90, 120, 180]
            deployment_ratios = [0.70, 0.80, 0.90, 1.00]

    current_params = (
        market,
        soxl_track,
        kokore_track,
        float(investment),
        anchor_mode,
        tuple(effective_buy_steps),
        tuple(effective_take_profits),
        tuple(effective_max_holds),
        tranche_count,
        tuple(allocation_candidates),
        tuple(deployment_ratios),
        tuple(ma_periods),
        backtest_period_mode,
        selected_start_year,
        selected_end_year,
        selected_start_date,
        selected_end_date,
        bool(blind_validation_2026),
        use_rsi_candidates,
        tuple(rsi_thresholds),
        short_mode,
        us_execution_mode,
        tuple(loc_buy_ratios),
        float(loc_buy_offset),
        float(loc_sell_offset),
        float(fee_pct),
        float(slippage_pct),
        float(fx_cost_pct),
        int(min_completed_trades),
        float(max_allowed_mdd),
    )

    # 장시간 백테스트 체크포인트: 브라우저/앱을 나갔다 돌아와도 마지막 저장 지점부터 재개
    checkpoint_sig = _checkpoint_signature(current_params)
    checkpoint_file = _checkpoint_path(active_market_key)
    checkpoint_preview = _load_backtest_checkpoint(checkpoint_file)
    last_result_file = _last_result_path(active_market_key)
    last_result_preview = _load_backtest_checkpoint(last_result_file)
    resume_checkpoint = False

    # 직전 완료 백테스트는 진행 중 체크포인트와 별도로 보관합니다.
    # 따라서 새 백테스트가 중간에 멈춰도 마지막 완료 결과를 다시 볼 수 있습니다.
    if last_result_preview and last_result_preview.get("results") and last_result_preview.get("sims"):
        saved_at = str(last_result_preview.get("saved_at", ""))[:19].replace("T", " ")
        st.caption(f"📚 직전 완료 백테스트 저장됨" + (f" · {saved_at} UTC" if saved_at else ""))
        if st.button("📂 직전 백테스트 결과 불러오기", use_container_width=True):
            try:
                loaded_result = pd.DataFrame(last_result_preview["results"])
                if loaded_result.empty:
                    raise ValueError("저장된 결과가 비어 있습니다.")
                loaded_result = loaded_result.sort_values(
                    ["CAGR", "최종자산", "MDD"], ascending=[False, False, False]
                ).reset_index(drop=True)
                st.session_state.backtest_result = loaded_result
                st.session_state.backtest_sims = last_result_preview["sims"]
                st.session_state.backtest_params = last_result_preview.get("params")
                st.success("직전 완료 백테스트 결과를 불러왔습니다.")
            except Exception as e:
                st.warning(f"직전 결과를 불러오지 못했습니다: {e}")

    if checkpoint_preview and checkpoint_preview.get("signature") == checkpoint_sig:
        cp_tested = len(checkpoint_preview.get("tested", []))
        cp_total = int(checkpoint_preview.get("total_grid", 0) or 0)
        cp_phase = checkpoint_preview.get("phase", "탐색 중")
        st.info(
            f"💾 저장된 백테스트 진행상태: {cp_tested:,}개 완료"
            + (f" / 전체 후보 {cp_total:,}개" if cp_total else "")
            + f" · {cp_phase}"
        )
        cpr1, cpr2 = st.columns(2)
        if cpr1.button("▶️ 이전 백테스트 이어하기", use_container_width=True):
            resume_checkpoint = True
            run_backtest = True
        if cpr2.button("🗑️ 진행상태 삭제", use_container_width=True):
            _delete_backtest_checkpoint(checkpoint_file)
            st.rerun()
    elif checkpoint_preview:
        st.caption("💾 이전 진행상태가 있지만 현재 설정과 달라서 자동 재개하지 않습니다.")

    # 현재 지표 표시용: 레버리지 신호 + 기초 ETF/본주 RSI 및 추세
    current_signal_symbol = modes[0]["signal_symbol"]
    current_ref_symbol = modes[0]["ref_symbol"]

    temp = pd.DataFrame(index=close.index)
    temp["SIGNAL"] = close[current_signal_symbol]
    temp["REF"] = close[current_ref_symbol]
    temp["ANCHOR"] = anchor_series(temp["SIGNAL"], anchor_mode)
    temp["DD"] = temp["SIGNAL"] / temp["ANCHOR"] - 1
    temp["RSI14"] = calc_rsi(temp["REF"], 14)
    for p in [100, 150, 200]:
        temp[f"MA{p}"] = temp["REF"].rolling(p).mean()
        temp[f"TREND_OK_{p}"] = temp["REF"] > temp[f"MA{p}"]
    temp = temp.dropna()

    if temp.empty:
        raise ValueError("지표 계산 후 사용할 데이터가 없습니다.")

    current_signal_price = float(temp["SIGNAL"].iloc[-1])
    current_dd = float(temp["DD"].iloc[-1])
    current_rsi = float(temp["RSI14"].iloc[-1])

    c1, c2, c3 = st.columns(3)

    if market.startswith("🇺🇸"):
        c1.metric(us_product, f"${current_signal_price:,.2f}")
    else:
        c1.metric("코코레", f"₩{current_signal_price:,.0f}")

    c2.metric("고점 대비", f"{current_dd:.1%}")
    c3.metric("RSI", f"{current_rsi:.1f}")

    m1, m2, m3 = st.columns(3)
    for col, p in zip([m1, m2, m3], [100, 150, 200]):
        col.metric(f"{p}일선", "위" if bool(temp[f"TREND_OK_{p}"].iloc[-1]) else "아래")

    if market.startswith("🇰🇷"):
        st.caption(
            "① 레버리지 직접매수 / ② 코코레는 신호만 사용하고 본주 매수 / "
            "③ 본주 신호로 본주 매수를 같은 조건에서 비교합니다."
        )

    if (
        not effective_buy_steps
        or not effective_take_profits
        or not effective_max_holds
        or not allocation_candidates
    ):
        st.warning("매수 간격, 익절률, 최대 보유기간을 각각 하나 이상 선택해 주세요.")
        st.stop()

    st.divider()

    # ------------------------------------------------------------
    # SOXL 오늘 주문값 FAST PATH
    # 백테스트/최적화를 기다리지 않고 확정 일봉만으로 즉시 계산한다.
    # 실제 전략 함수 get_soxl_loc_plan()을 그대로 호출하므로 표시용 임시가격이 아니다.
    # ------------------------------------------------------------
    if market.startswith("🇺🇸") and us_product == "SOXL":
        try:
            # 오늘 진행 중인 미국 일봉은 제외한다.
            # SOXL/QQQ/SMH 모두 같은 '확정 일봉' 원칙을 적용해야 지표와 LOC 가격이 장중 흔들리지 않는다.
            _soxl_fast = confirmed_us_daily_series(close["SOXL"])
            _qqq_fast = confirmed_us_daily_series(close["QQQ"])
            _smh_fast = confirmed_us_daily_series(close["SMH"])

            # --------------------------------------------------------
            # 실전 보유상태
            # 과거 매매일지를 전부 입력하지 않아도 현재 상태만 입력하면 된다.
            # 입력값은 session_state에 남아 앱 재실행 중에도 유지된다.
            # --------------------------------------------------------
            st.markdown("### 💼 현재 SOXL 보유상태")
            st.caption("과거 매매일지는 없어도 됩니다. 지금 보유 중인 수량과 평균매수가만 입력하면 오늘 추가매수 가능 비중을 계산합니다.")

            _saved_soxl = load_position_state().get("SOXL", {})
            if "soxl_manual_qty" not in st.session_state:
                st.session_state["soxl_manual_qty"] = float(_saved_soxl.get("qty", 0.0) or 0.0)
            if "soxl_manual_avg" not in st.session_state:
                st.session_state["soxl_manual_avg"] = float(_saved_soxl.get("avg_price", 0.0) or 0.0)

            def _save_soxl_position_now():
                # 수동 수정은 현재 총수량/평단을 교정하는 기능. 기존 LOT는 그대로 보존한다.
                save_position_state("SOXL", st.session_state.get("soxl_manual_qty", 0.0),
                                    st.session_state.get("soxl_manual_avg", 0.0))

            _hold1, _hold2 = st.columns(2)
            _fast_qty = _hold1.number_input("SOXL 보유수량(주)", min_value=0.0, step=1.0,
                                             key="soxl_manual_qty", on_change=_save_soxl_position_now)
            _fast_avg = _hold2.number_input("SOXL 평균매수가($)", min_value=0.0, step=0.01,
                                             key="soxl_manual_avg", on_change=_save_soxl_position_now)
            st.caption("💾 보유현황 자동저장 ON · 변경 즉시 저장되고 다음 접속 때 자동 복원됩니다.")

            _fast_open_cost = float(_fast_qty) * float(_fast_avg)
            _fast_exp = min(1.0, max(0.0, _fast_open_cost / float(investment))) if float(investment) > 0 else 0.0
            _fast_cash = max(0.0, float(investment) - _fast_open_cost)

            _h1, _h2, _h3 = st.columns(3)
            _h1.metric("현재 투입비중", f"{_fast_exp:.1%}")
            _h2.metric("보유원가", f"${_fast_open_cost:,.0f}")
            _h3.metric("남은 운용자금", f"${_fast_cash:,.0f}")

            if _fast_exp > 1.0:
                st.warning("입력한 보유원가가 설정한 총 투자원금을 초과합니다.")

            _fast_plan = get_soxl_loc_plan(
                _soxl_fast,
                exposure_now=_fast_exp,
                base_unit=0.06,
                qqq_series=_qqq_fast,
                smh_series=_smh_fast,
            )
            _fast_prev = float(_fast_plan["prev_close"])
            _fast_offsets = tuple(_fast_plan["offsets"])
            _fast_weights = tuple(_fast_plan["weights"])
            _fast_buy = [_fast_prev * (1.0 + float(x)) for x in _fast_offsets]

            # 현재 앱의 C-ORIGINAL 기본 TP 6%. C-ALPHA는 최종 연구 기본값 5%를 표시한다.
            _fast_tp = 0.06 if soxl_track.startswith("C-ORIGINAL") else 0.05
            _fast_sell = [x * (1.0 + _fast_tp) for x in _fast_buy]

            if st.session_state.get("_used_intraday_notice") is None:
                st.session_state["_used_intraday_notice"] = False
            try:
                if _used_intraday_fallback:
                    st.success(
                        f"🛟 일봉 지연 보완 성공 · 최근 1시간봉을 거래일별 종가로 집계해 "
                        f"{_fallback_last_date}까지 보완했습니다."
                    )
            except Exception:
                pass

            _refresh_col1, _refresh_col2 = st.columns([2, 1])
            with _refresh_col2:
                if st.button("🔄 오늘 데이터 강제 새로고침", use_container_width=True, key="force_daily_refresh"):
                    st.cache_data.clear()
                    st.session_state["_force_refresh_done"] = True
                    st.rerun()
            if st.session_state.pop("_force_refresh_done", False):
                st.success("최신 Yahoo 일봉 데이터를 다시 요청했습니다.")

            st.markdown("## ⚡ 오늘 SOXL 주문값 — 확정 일봉 잠금 🔒")
            st.caption("미국 정규장 종료 전에는 진행 중인 오늘 일봉을 제외합니다. 같은 확정 일봉 기준에서는 새로고침해도 주문가격이 바뀌지 않습니다.")

            # 미국 정규장 마감 후 최신 일봉이 실제로 반영됐는지 자동 확인.
            # 단순 시계가 아니라 SOXL 데이터의 마지막 거래일과 미국 동부 현재 날짜/시간을 비교한다.
            try:
                from zoneinfo import ZoneInfo
                _utc_now = datetime.now(timezone.utc)
                _ny_now = _utc_now.astimezone(ZoneInfo("America/New_York"))
                _kr_now = _utc_now.astimezone(ZoneInfo("Asia/Seoul"))
                _last_bar_date = pd.Timestamp(_soxl_fast.index[-1]).date()

                # 평일 ET 16:00 이후에는 '오늘' 일봉이 있어야 확정,
                # 장 마감 전에는 직전 거래일 일봉을 정상 데이터로 취급한다.
                _after_close = (_ny_now.weekday() < 5 and _ny_now.time() >= datetime.strptime("16:00", "%H:%M").time())
                _expected_today = _ny_now.date()

                if _after_close and _last_bar_date >= _expected_today:
                    st.success(
                        f"✅ 오늘 데이터 확정됨 · SOXL 최신 일봉 {_last_bar_date} · "
                        f"한국 {_kr_now:%H:%M} / 미국동부 {_ny_now:%H:%M}"
                    )
                elif _after_close:
                    st.warning(
                        f"⏳ 아직 오늘 일봉 업데이트 전 · 현재 최신 {_last_bar_date} · "
                        f"미국장 마감 후 데이터 반영을 기다리는 중입니다. "
                        f"(한국 {_kr_now:%H:%M} / 미국동부 {_ny_now:%H:%M})"
                    )
                else:
                    # 장 마감 전이라도 전 거래일 데이터가 아직 Yahoo 응답에 없으면 사용자가 즉시 알 수 있게 한다.
                    _days_gap = (_ny_now.date() - _last_bar_date).days
                    if _ny_now.weekday() < 5 and _days_gap >= 2:
                        st.warning(
                            f"⚠️ 최신 확정 일봉이 {_last_bar_date}에 머물러 있습니다. "
                            f"위의 '오늘 데이터 강제 새로고침'을 눌러 다시 받아보세요. "
                            f"(한국 {_kr_now:%H:%M} / 미국동부 {_ny_now:%H:%M})"
                        )
                    else:
                        st.info(
                            f"🟢 장 마감 전 · 최신 확정 일봉 {_last_bar_date} 기준 주문값 · "
                            f"미국장 마감 후 다시 확인하면 다음 거래일 주문값이 갱신됩니다. "
                            f"(한국 {_kr_now:%H:%M} / 미국동부 {_ny_now:%H:%M})"
                        )
            except Exception as _status_e:
                st.caption(f"데이터 확정상태 확인 불가: {_status_e}")

            st.caption(
                f"상태 **{_fast_plan['state']}** · 위험점수 {_fast_plan.get('risk_score','-')}/10 · "
                f"확정종가 ${_fast_prev:,.2f} · 추정 현재투입 {_fast_exp:.1%}"
            )

            # 실전 안전장치: 평일 미국 장 시작 전인데 최신 일봉이 2일 이상 뒤처지면
            # 오래된 주문가격을 정상 신호처럼 표시하지 않는다.
            _stale_daily = False
            try:
                from zoneinfo import ZoneInfo
                _fresh_now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                _fresh_last = pd.Timestamp(_soxl_fast.index[-1]).date()
                _gap = (_fresh_now_et.date() - _fresh_last).days
                if _fresh_now_et.weekday() < 5 and _gap >= 2:
                    _stale_daily = True
            except Exception:
                pass

            if _stale_daily:
                st.error(
                    f"⛔ 최신 확정 일봉이 {_fresh_last}에 머물러 있어 오늘 실전 주문값을 잠갔습니다. "
                    "일봉 재조회와 최근 1시간봉 보완까지 시도했지만 최신 거래일을 확보하지 못했습니다. "
                    "오래된 데이터로 주문하지 않도록 차단했습니다."
                )
                st.stop()

            _fast_rows = []
            _remaining_cash = _fast_cash
            for _i, (_px, _w) in enumerate(zip(_fast_buy, _fast_weights), 1):
                _desired = float(investment) * float(_w)
                _actual_amt = min(_desired, _remaining_cash)
                _actual_w = (_actual_amt / float(investment)) if float(investment) > 0 else 0.0
                _shares = int(_actual_amt // float(_px)) if float(_px) > 0 else 0
                _remaining_cash -= _actual_amt
                _fast_rows.append({
                    "주문": f"매수 {_i}", "방식": "LOC",
                    "가격": f"${_px:,.2f}", "실제 비중": f"{_actual_w:.1%}",
                    "주문금액": f"${_actual_amt:,.0f}",
                    "예상수량": f"{_shares}주",
                })

            # 기존 보유분은 과거 LOT 정보가 없으므로 평균단가 기준 매도 참고가를 별도로 표시한다.
            if float(_fast_qty) > 0 and float(_fast_avg) > 0:
                _existing_sell = float(_fast_avg) * (1.0 + _fast_tp)
                _fast_rows.append({
                    "주문": "기존 보유분 매도", "방식": "LOC",
                    "가격": f"${_existing_sell:,.2f}", "실제 비중": f"{_fast_exp:.1%}",
                    "주문금액": "보유수량 기준",
                    "예상수량": f"{float(_fast_qty):g}주",
                })

            # 포지션과 무관한 블라인드용 매도 신호도 상단 주문표에 항상 표시
            if len(_fast_buy) >= 2:
                _display_sell=[float(_fast_buy[0])*(1.0+_fast_tp), float(_fast_buy[1])*(1.0+_fast_tp)]
                for _j,_spx in enumerate(_display_sell):
                    _sw=float(_fast_weights[_j]) if _j < len(_fast_weights) else 0.0
                    _fast_rows.append({
                        "주문":f"매도 {_j+1}","방식":"LOC","가격":f"${_spx:,.2f}",
                        "실제 비중":f"{_sw:.1%}","주문금액":"블라인드","예상수량":"포지션 무관"
                    })
            st.dataframe(pd.DataFrame(_fast_rows), use_container_width=True, hide_index=True)

            if len(_fast_buy) >= 2:
                # 확정 일봉 날짜와 실제 주문일을 분리한다.
                try:
                    from zoneinfo import ZoneInfo
                    _blind_now_et=datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                    _last_signal_bar=pd.Timestamp(_soxl_fast.index[-1]).date()
                    if _blind_now_et.weekday()<5 and _blind_now_et.date()>_last_signal_bar:
                        _blind_date=_blind_now_et.date()
                    else:
                        _blind_date=_last_signal_bar
                except Exception:
                    _blind_date=pd.Timestamp(_soxl_fast.index[-1]).date()
                # 블라인드용 매도 신호는 실제 보유 LOT와 무관하게 매일 계산/잠금한다.
                _blind_sell=[float(_fast_buy[0])*(1.0+_fast_tp), float(_fast_buy[1])*(1.0+_fast_tp)]
                _blind_df=lock_blind_prices(
                    _blind_date,_fast_buy[0],_fast_buy[1],
                    _blind_sell[0],_blind_sell[1],
                    float(_fast_weights[0])*100.0,float(_fast_weights[1])*100.0
                )
                _tb=_blind_df[_blind_df["date"].astype(str)==str(_blind_date)].iloc[-1]
                with st.expander("🧪 20거래일 블라인드 검증", expanded=False):
                    st.caption(f"주문일 {_blind_date} · 계산 기준 확정 일봉 {pd.Timestamp(_soxl_fast.index[-1]).date()} · 우리 LOC는 주문일 최초값으로 잠깁니다.")
                    st.write(f"🔒 잠긴 우리 매수 LOC · **${float(_tb['our1']):,.2f} / ${float(_tb['our2']):,.2f}**")
                    st.write(f"🔒 잠긴 우리 매도 LOC · **${float(_tb['our_sell1']):,.2f} / ${float(_tb['our_sell2']):,.2f}** · 보유 포지션과 무관")
                    st.caption(f"우리 예측 매수비중 · {float(_tb['our_weight1']):.1f}% / {float(_tb['our_weight2']):.1f}%")
                    _b1,_b2=st.columns(2)
                    _o1=_b1.number_input("원본 LOC 1",min_value=0.0,step=0.01,format="%.2f",key=f"blind_o1_{_blind_date}")
                    _o2=_b2.number_input("원본 LOC 2",min_value=0.0,step=0.01,format="%.2f",key=f"blind_o2_{_blind_date}")
                    _w1c,_w2c=st.columns(2)
                    _ow1=_w1c.number_input("원본 비중 1 (%)",min_value=0.0,max_value=100.0,step=0.5,format="%.1f",key=f"blind_w1_{_blind_date}")
                    _ow2=_w2c.number_input("원본 비중 2 (%)",min_value=0.0,max_value=100.0,step=0.5,format="%.1f",key=f"blind_w2_{_blind_date}")
                    st.markdown("**원본 매도 신호**")
                    _s1c,_s2c=st.columns(2)
                    _os1=_s1c.number_input("원본 매도 LOC 1",min_value=0.0,step=0.01,format="%.2f",key=f"blind_s1_{_blind_date}")
                    _os2=_s2c.number_input("원본 매도 LOC 2",min_value=0.0,step=0.01,format="%.2f",key=f"blind_s2_{_blind_date}")
                    _sw1c,_sw2c=st.columns(2)
                    _osw1=_sw1c.number_input("원본 매도 비중 1 (%)",min_value=0.0,max_value=100.0,step=0.5,format="%.1f",key=f"blind_sw1_{_blind_date}")
                    _osw2=_sw2c.number_input("원본 매도 비중 2 (%)",min_value=0.0,max_value=100.0,step=0.5,format="%.1f",key=f"blind_sw2_{_blind_date}")
                    if st.button("원본 매수·매도 가격/비중 검증 저장",key=f"blind_save_{_blind_date}",use_container_width=True,disabled=(_o1<=0 or _o2<=0)):
                        save_blind_original(_blind_date,_o1,_o2,_ow1,_ow2,_os1,_os2,_osw1,_osw2); st.rerun()
                    _done=load_blind_test().sort_values("date").tail(20).dropna(subset=["original1","original2"]).copy()
                    if len(_done):
                        _errs=pd.concat([pd.to_numeric(_done["err1_pct"],errors="coerce"),pd.to_numeric(_done["err2_pct"],errors="coerce")]).dropna()
                        _exact=int(_done["any_exact"].astype(str).str.lower().eq("true").sum())
                        _m1,_m2,_m3,_m4=st.columns(4)
                        _m1.metric("검증일",f"{len(_done)}/20")
                        _m2.metric("최소 1개 완전일치",f"{_exact}일")
                        _m3.metric("±0.1% 이내",f"{((_errs<=.001).mean() if len(_errs) else 0):.1%}")
                        _m4.metric("평균 절대오차",f"{((_errs.mean() if len(_errs) else 0)):.3%}")
                        _show=_done.tail(20).copy()
                        _show["우리 LOC"]=_show.apply(lambda r:f"${float(r.our1):.2f} / ${float(r.our2):.2f}",axis=1)
                        _show["원본 LOC"]=_show.apply(lambda r:f"${float(r.original1):.2f} / ${float(r.original2):.2f}",axis=1)
                        _show["우리 비중"]=_show.apply(lambda r:f"{float(r.our_weight1):.1f}% / {float(r.our_weight2):.1f}%" if pd.notna(r.get("our_weight1")) and pd.notna(r.get("our_weight2")) else "-",axis=1)
                        _show["원본 비중"]=_show.apply(lambda r:f"{float(r.original_weight1):.1f}% / {float(r.original_weight2):.1f}%" if pd.notna(r.original_weight1) and pd.notna(r.original_weight2) else "-",axis=1)
                        _show["매수오차"]=_show.apply(lambda r:f"{float(r.err1_pct):.3%} / {float(r.err2_pct):.3%}",axis=1)
                        _show["우리 매도"]=_show.apply(lambda r:f"${float(r.our_sell1):.2f} / ${float(r.our_sell2):.2f}" if pd.notna(r.get("our_sell1")) and pd.notna(r.get("our_sell2")) else "-",axis=1)
                        _show["원본 매도"]=_show.apply(lambda r:f"${float(r.original_sell1):.2f} / ${float(r.original_sell2):.2f}" if pd.notna(r.get("original_sell1")) and pd.notna(r.get("original_sell2")) else "-",axis=1)
                        _show["매도오차"]=_show.apply(lambda r:f"{float(r.sell_err1_pct):.3%} / {float(r.sell_err2_pct):.3%}" if pd.notna(r.get("sell_err1_pct")) and pd.notna(r.get("sell_err2_pct")) else "-",axis=1)
                        st.dataframe(_show[["date","우리 LOC","원본 LOC","우리 비중","원본 비중","매수오차","우리 매도","원본 매도","매도오차"]],use_container_width=True,hide_index=True)
                    else:
                        st.info("원본 가격을 입력하면 통계가 자동으로 쌓입니다.")

            st.markdown("### ✅ LOC 체결 반영")
            st.caption("증권사 체결내역을 확인한 뒤 해당 버튼만 누르세요. 앱은 체결 여부를 추정하지 않습니다.")
            _fill_cols = st.columns(max(1, len(_fast_buy)))
            _fill_remaining = _fast_cash
            for _j, (_px, _w) in enumerate(zip(_fast_buy, _fast_weights)):
                _amt = min(float(investment)*float(_w), _fill_remaining)
                _qty = int(_amt // float(_px)) if float(_px) > 0 else 0
                _fill_remaining -= _amt
                if _fill_cols[_j].button(
                    f"매수 {_j+1} 체결됨\n{_qty}주 @ ${_px:,.2f}",
                    key=f"soxl_buy_fill_{pd.Timestamp(_soxl_fast.index[-1]).date()}_{_j}",
                    use_container_width=True,
                    disabled=(_qty <= 0),
                ):
                    _new_qty, _new_avg = add_position_lot("SOXL", _px, _qty, f"매수 {_j+1}")
                    st.session_state["soxl_manual_qty"] = float(_new_qty)
                    st.session_state["soxl_manual_avg"] = float(_new_avg)
                    st.success(f"매수 {_j+1} 체결 저장: {_qty}주 @ ${_px:,.2f} · 새 평단 ${_new_avg:,.2f}")
                    st.rerun()

            # 앱에서 체결 확인한 LOT는 개별 목표가로 정확히 관리한다.
            _position_now = load_position_state().get("SOXL", {})
            _lots_now = list(_position_now.get("lots", []))
            if _lots_now:
                st.markdown("### 📚 보유 LOT별 매도")
                for _li, _lot in enumerate(_lots_now):
                    _lp = float(_lot.get("price", 0))
                    _lq = float(_lot.get("qty", 0))
                    _target = _lp * (1.0 + _fast_tp)
                    _c1, _c2 = st.columns([2, 1])
                    _c1.write(f"LOT {_li+1} · {_lq:g}주 @ ${_lp:,.2f} → LOC 매도 **${_target:,.2f}**")
                    if _c2.button("매도 체결됨", key=f"soxl_sell_fill_{_li}_{_lp}_{_lq}", use_container_width=True):
                        _new_qty, _new_avg = close_position_lot("SOXL", _li)
                        st.session_state["soxl_manual_qty"] = float(_new_qty)
                        st.session_state["soxl_manual_avg"] = float(_new_avg)
                        st.success(f"LOT {_li+1} 매도 체결 저장")
                        st.rerun()
            elif float(_fast_qty) > 0:
                st.caption("※ 앱 사용 전부터 보유하던 물량은 LOT 정보가 없어서 평균매수가 기준으로만 표시됩니다. 이후 앱에서 '체결됨'을 누른 매수부터 LOT별로 자동 관리됩니다.")
            st.success("✅ 오늘 주문가격은 위에서 바로 확인할 수 있습니다. 아래 백테스트는 과거 성과를 다시 검증할 때만 실행하세요.")
        except Exception as _fast_e:
            st.warning(f"오늘 SOXL 주문값 즉시 계산 실패: {_fast_e}")

        st.info(
            "🧪 SOXL DUAL 백테스트: SOXL 이평/RSI + 실제 LOT + QQQ 이평 구조 + SMH/QQQ 5일 상대강도로 "
            "4+4 / 5+5 / 6+6 / 7+7 매수비중을 자동 전환합니다. VIX는 제외했습니다. "
            "LOC 가격 격자는 V31과 동일하게 유지해 C타입 상태판단 자체의 효과를 먼저 비교합니다."
        )
    st.subheader("🏆 과거 백테스트 / 전략 자동 비교 (오늘 주문 확인에는 불필요)")
    if market.startswith("🇺🇸") and us_product == "SOXL":
        if soxl_track.startswith("C-ORIGINAL"):
            st.caption("C-ORIGINAL은 최적화 없이 고정 로직 1개만 즉시 계산합니다. 실제 주문 재현 → 8월 블라인드 → 장기 CAGR/MDD 순으로 평가합니다.")
        else:
            st.caption("평가 우선순위: 2016~2025 CAGR → 연도별 병목 확인. ORIGINAL 로직과 점수는 별도 보존합니다.")

    if run_backtest:
        checkpoint_state = None
        if resume_checkpoint:
            checkpoint_state = _load_backtest_checkpoint(checkpoint_file)
            if not checkpoint_state or checkpoint_state.get("signature") != checkpoint_sig:
                st.warning("저장된 진행상태를 사용할 수 없어 처음부터 계산합니다.")
                checkpoint_state = None

        if checkpoint_state:
            results = checkpoint_state.get("results", [])
            sims = checkpoint_state.get("sims", {})
        else:
            results = []
            sims = {}
            _delete_backtest_checkpoint(checkpoint_file)

        # 모든 조합을 무작정 전부 계산하면 Streamlit Cloud에서 실행시간/메모리 한계로
        # 중간에 멈출 수 있습니다. 조합이 많을 때는 1차 넓은 탐색 -> 2차 상위권 정밀탐색으로 줄입니다.
        # 속도 우선 탐색: 큰 그리드는 대표 후보를 먼저 본 뒤 상위권 주변만 정밀 확인합니다.
        # SOXL 기본 그리드처럼 1,500개 안팎은 전수 계산하되, 그 이상은 2단계 탐색을 사용합니다.
        exhaustive_limit = 1500
        stage1_limit = 420
        stage2_limit = 220

        # 각 운용방식의 데이터프레임은 한 번만 계산해 재사용합니다.
        prepared = []
        required_ma = max([int(x) for x in ma_periods], default=0)
        min_rows = max(30, required_ma + 5)

        for mode in modes:
            df = pd.DataFrame(index=close.index)
            df["SIGNAL"] = close[mode["signal_symbol"]]
            df["TRADE"] = close[mode["trade_symbol"]]
            df["REF"] = close[mode["ref_symbol"]]
            if market.startswith("🇺🇸") and us_product == "SOXL":
                df["QQQ"] = close["QQQ"]
                df["SMH"] = close["SMH"]
            df = df.dropna()
            df = apply_backtest_period(
                df,
                backtest_period_mode,
                selected_start_year,
                selected_end_year,
                selected_start_date,
                selected_end_date,
            )

            # 2026 블라인드 검증: 파라미터 탐색에는 2026년 데이터를 절대 넣지 않습니다.
            if blind_validation_2026:
                df = df.loc[df.index <= pd.Timestamp("2025-12-31")].copy()

            if len(df) < min_rows:
                if required_ma > 0:
                    raise ValueError(
                        f"선택한 기간이 너무 짧습니다. {required_ma}일 이동평균을 계산하려면 "
                        f"최소 약 {required_ma}거래일 이상의 데이터가 필요합니다."
                    )
                raise ValueError("선택한 백테스트 기간이 너무 짧습니다.")

            df["ANCHOR"] = anchor_series(df["SIGNAL"], anchor_mode)
            df["RSI14"] = calc_rsi(df["REF"], 14)
            for p in ma_periods:
                p = int(p)
                df[f"MA{p}"] = df["REF"].rolling(p).mean()
                df[f"TREND_OK_{p}"] = df["REF"] > df[f"MA{p}"]

            required_cols = ["SIGNAL", "TRADE", "REF", "ANCHOR", "RSI14"]
            for p in ma_periods:
                required_cols.append(f"TREND_OK_{int(p)}")
            df = df.dropna(subset=required_cols)
            prepared.append((mode, df))

        # 조합을 인덱스로 만들어 1차/2차 탐색에서 동일한 조건을 중복 계산하지 않습니다.
        all_jobs = []
        for mi in range(len(modes)):
            for si, step_pct in enumerate(effective_buy_steps):
                for ti, tp in enumerate(effective_take_profits):
                    for fi, _ in enumerate(filter_candidates):
                        for hi, _ in enumerate(effective_max_holds):
                            for wi, _ in enumerate(allocation_candidates):
                                for xi, _ in enumerate(loc_buy_ratios):
                                    for di, _ in enumerate(deployment_ratios):
                                        all_jobs.append((mi, si, ti, fi, hi, wi, xi, di))

        total_grid = len(all_jobs)
        progress = st.progress(0)
        status = st.empty()
        detail = st.empty()
        started_at = time.time()
        if checkpoint_state:
            tested = {tuple(x) for x in checkpoint_state.get("tested", [])}
            scored = checkpoint_state.get("scored", [])
            live_best = checkpoint_state.get("live_best", {"eligible": False, "score": -float("inf"), "value": -float("inf"), "name": "-"})
            saved_refine = [tuple(x) for x in checkpoint_state.get("refine", [])]
            saved_phase = checkpoint_state.get("phase", "stage1")
        else:
            tested = set()
            scored = []
            live_best = {"eligible": False, "score": -float("inf"), "value": -float("inf"), "name": "-"}
            saved_refine = []
            saved_phase = "stage1"

        def save_checkpoint(phase, refine_jobs=None, force=False):
            if not force and (len(tested) == 0 or len(tested) % BACKTEST_CHECKPOINT_EVERY != 0):
                return
            payload = {
                "version": 1,
                "signature": checkpoint_sig,
                "market_key": active_market_key,
                "phase": phase,
                "total_grid": total_grid,
                "tested": list(tested),
                "scored": scored,
                "results": results,
                "sims": sims,
                "live_best": live_best,
                "refine": list(refine_jobs or []),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _save_backtest_checkpoint(checkpoint_file, payload)
            except Exception as e:
                detail.caption(f"⚠️ 진행상태 저장 실패: {e}")

        def run_one(job):
            mi, si, ti, fi, hi, wi, xi, di = job
            mode, df = prepared[mi]
            step_pct = effective_buy_steps[si]
            tp = effective_take_profits[ti]
            ma_period, rsi_max = filter_candidates[fi]
            max_hold_days = effective_max_holds[hi]
            allocation_weights = allocation_candidates[wi]
            loc_buy_ratio = float(loc_buy_ratios[xi])
            deployment_ratio = float(deployment_ratios[di])

            sim = simulate(
                df,
                float(investment),
                step_pct,
                tp,
                tranche_count,
                ma_period,
                rsi_max,
                allocation_weights,
                max_hold_days,
                us_execution_mode,
                float(loc_buy_offset),
                float(loc_sell_offset),
                loc_buy_ratio,
                float(fee_pct),
                float(slippage_pct),
                float(fx_cost_pct),
                deployment_ratio,
            )

            eligible = sim["trade_count"] >= int(min_completed_trades)
            # CAGR 최우선: MDD는 탈락 조건이 아니라 결과 비교 지표로만 사용합니다.
            risk_score = float(sim["cagr"])

            fname = filter_name(ma_period, rsi_max)
            hold_name = "제한없음" if max_hold_days is None else f"{max_hold_days}일"
            buy_exec_name = "LOC 100%"
            sell_exec_name = "LOC 매도"
            exec_name = "LOC"
            strategy_name = (
                f"{mode['mode']} | C타입 4/5/6/7% 자동비중 / {tp:.0%} 블록익절 / "
                f"운용 {deployment_ratio:.0%} / QQQ+SMH+SOXL+LOT / 최대 {hold_name} / {fname} / {exec_name}" if market.startswith("🇺🇸") and us_product == "SOXL" else f"운용 {deployment_ratio:.0%} / 자동비중#{wi + 1} / 최대 {hold_name} / {fname} / {exec_name}"
            )

            sims[strategy_name] = {
                "sim": sim,
                "mode": mode,
                "df": df,
                "step_pct": step_pct,
                "tp": tp,
                "ma_period": ma_period,
                "rsi_max": rsi_max,
                "allocation_weights": list(allocation_weights),
                "deployment_ratio": deployment_ratio,
                "max_hold_days": max_hold_days,
                "execution_mode": us_execution_mode,
                "loc_buy_ratio": loc_buy_ratio,
                "loc_buy_offset": float(loc_buy_offset),
                "loc_sell_offset": float(loc_sell_offset),
            }
            row = {
                "전략": strategy_name,
                "운용방식": mode["mode"],
                "매수간격": step_pct,
                "익절률": tp,
                "총자금 운용률": deployment_ratio,
                "차수별 매수%": ("상태별 자동가중" if market.startswith("🇺🇸") and us_product == "SOXL" else allocation_text(np.asarray(allocation_weights) * deployment_ratio)),
                "이평선": "없음" if ma_period is None else f"{int(ma_period)}일",
                "필터": fname,
                "체결방식": exec_name,
                "매수체결혼합": buy_exec_name,
                "매도체결": sell_exec_name,
                "보유제한": hold_name,
                "최종자산": sim["final_value"],
                "누적수익률": sim["total_return"],
                "CAGR": sim["cagr"],
                "MDD": sim["mdd"],
                "최대보유일": sim["max_hold"],
                "기간청산": sim["forced_exit_count"],
                "완료매매": sim["trade_count"],
                "승률": sim["win_rate"],
                "평균 투자금 사용률": sim["avg_exposure"],
                "최대 투자금 사용률": sim["max_exposure"],
                "실전기준통과": eligible,
                "실전점수": risk_score,
            }
            results.append(row)
            scored.append(((float(sim["cagr"]), float(sim["final_value"]), -abs(float(sim["mdd"]))), job))
            tested.add(job)
            live_rank = (float(sim["cagr"]), float(sim["final_value"]), -abs(float(sim["mdd"])))
            current_rank = (live_best["score"], live_best["value"], -999.0)
            if live_rank > current_rank:
                live_best["eligible"] = eligible
                live_best["score"] = risk_score
                live_best["value"] = float(sim["final_value"])
                live_best["name"] = strategy_name

        def show_progress(label, done, total):
            elapsed = max(time.time() - started_at, 0.001)
            rate = len(tested) / elapsed
            remaining = max(total - done, 0)
            eta = remaining / rate if rate > 0 else 0
            progress.progress(min(done / max(total, 1), 1.0))
            status.caption(f"{label}... {done}/{total} · 예상 남은 시간 약 {eta:,.0f}초")
            if live_best["value"] > -float("inf"):
                detail.caption(f"현재 최고 최종자산: {currency}{live_best['value']:,.0f} · {live_best['name']}")

        with st.spinner("백테스트 계산 중..."):
            if total_grid <= exhaustive_limit:
                status.caption(f"전체 조합 {total_grid:,}개를 정밀 계산합니다.")
                if checkpoint_state:
                    status.caption(f"💾 {len(tested):,}개 완료 지점부터 이어서 계산합니다.")
                for i, job in enumerate(all_jobs, 1):
                    if job not in tested:
                        run_one(job)
                        save_checkpoint("전체 정밀탐색")
                    if i == 1 or i % 10 == 0 or i == total_grid:
                        show_progress("전체 정밀탐색", len(tested), total_grid)
                refine = []
                save_checkpoint("완료", force=True)
            else:
                n1 = min(stage1_limit, total_grid)
                stage1_idx = np.linspace(0, total_grid - 1, n1, dtype=int)
                stage1_jobs = [all_jobs[i] for i in dict.fromkeys(stage1_idx)]
                status.caption(
                    f"전체 {total_grid:,}개 조합은 너무 커서 빠른 2단계 탐색을 사용합니다. "
                    f"1차 {len(stage1_jobs):,}개 → 상위권 주변 정밀탐색"
                )

                # 1차는 이미 끝낸 조합을 건너뜁니다.
                for i, job in enumerate(stage1_jobs, 1):
                    if job not in tested:
                        run_one(job)
                        save_checkpoint("1차 넓은 탐색")
                    done1 = sum(1 for j in stage1_jobs if j in tested)
                    if i == 1 or i % 10 == 0 or i == len(stage1_jobs):
                        show_progress("1차 넓은 탐색", done1, len(stage1_jobs))
                save_checkpoint("1차 완료", force=True)

                # 2차 후보는 중단 시점과 동일하게 유지합니다.
                if checkpoint_state and saved_phase in ("2차 상위권 정밀탐색", "완료") and saved_refine:
                    refine = list(saved_refine)
                else:
                    top_jobs = [j for _, j in sorted(scored, key=lambda x: x[0], reverse=True)[:20]]
                    refine = []
                    for mi, si, ti, fi, hi, wi, xi, di in top_jobs:
                        neighborhoods = [
                            range(max(0, si - 1), min(len(effective_buy_steps), si + 2)),
                            range(max(0, ti - 1), min(len(effective_take_profits), ti + 2)),
                            range(max(0, fi - 1), min(len(filter_candidates), fi + 2)),
                            range(max(0, hi - 1), min(len(effective_max_holds), hi + 2)),
                            range(max(0, wi - 1), min(len(allocation_candidates), wi + 2)),
                            range(max(0, xi - 1), min(len(loc_buy_ratios), xi + 2)),
                            range(max(0, di - 1), min(len(deployment_ratios), di + 2)),
                        ]
                        for nsi in neighborhoods[0]:
                            for nti in neighborhoods[1]:
                                for nfi in neighborhoods[2]:
                                    for nhi in neighborhoods[3]:
                                        for nwi in neighborhoods[4]:
                                            for nxi in neighborhoods[5]:
                                                for ndi in neighborhoods[6]:
                                                    candidate = (mi, nsi, nti, nfi, nhi, nwi, nxi, ndi)
                                                    if candidate not in tested:
                                                        refine.append(candidate)
                    refine = list(dict.fromkeys(refine))
                    if len(refine) > stage2_limit:
                        idx = np.linspace(0, len(refine) - 1, stage2_limit, dtype=int)
                        refine = [refine[i] for i in dict.fromkeys(idx)]
                    save_checkpoint("2차 상위권 정밀탐색", refine_jobs=refine, force=True)

                progress.progress(0)
                stage2_started = time.time()
                stage2_initial_done = sum(1 for j in refine if j in tested)
                for i, job in enumerate(refine, 1):
                    if job not in tested:
                        run_one(job)
                        save_checkpoint("2차 상위권 정밀탐색", refine_jobs=refine)
                    done2 = sum(1 for j in refine if j in tested)
                    elapsed2 = max(time.time() - stage2_started, 0.001)
                    new_done2 = max(done2 - stage2_initial_done, 0)
                    rate2 = new_done2 / elapsed2 if new_done2 > 0 else 0
                    eta2 = (len(refine) - done2) / rate2 if rate2 > 0 else 0
                    progress.progress(done2 / max(len(refine), 1))
                    status.caption(f"2차 상위권 정밀탐색... {done2}/{len(refine)} · 예상 남은 시간 약 {eta2:,.0f}초")
                    if i == 1 or i % 10 == 0 or i == len(refine):
                        detail.caption(f"현재 최고 최종자산: {currency}{live_best['value']:,.0f} · {live_best['name']}")
                save_checkpoint("완료", refine_jobs=refine, force=True)

        result = (
            pd.DataFrame(results)
            .sort_values(["CAGR", "최종자산", "MDD"], ascending=[False, False, False])
            .reset_index(drop=True)
        )

        st.session_state.backtest_result = result
        st.session_state.backtest_sims = sims
        st.session_state.backtest_params = current_params
        save_checkpoint("완료", refine_jobs=(refine if total_grid > exhaustive_limit else []), force=True)

        # 마지막으로 정상 완료된 결과는 별도 파일에 영구 스냅샷으로 보관합니다.
        # 다음 실행의 진행 체크포인트가 덮어써져도 이 결과는 유지됩니다.
        try:
            _save_backtest_checkpoint(last_result_file, {
                "version": 1,
                "signature": checkpoint_sig,
                "market_key": active_market_key,
                "phase": "완료",
                "params": current_params,
                "results": results,
                "sims": sims,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            st.caption(f"⚠️ 직전 완료 결과 저장 실패: {e}")

        progress.empty()
        status.empty()
        detail.empty()
        if total_grid > exhaustive_limit:
            st.success(
                f"빠른 탐색 완료: 전체 후보 {total_grid:,}개 중 {len(tested):,}개를 선별 계산했습니다. "
                "상위 전략 주변을 2차로 다시 확인해 속도와 안정성을 높였습니다."
            )

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
    best_ma_period = winner_bundle.get("ma_period")
    best_rsi = winner_bundle["rsi_max"]
    best_allocation_weights = normalize_allocation_weights(
        tranche_count, winner_bundle.get("allocation_weights")
    )
    best_deployment_ratio = float(winner_bundle.get("deployment_ratio", 1.0))
    best_max_hold = winner_bundle.get("max_hold_days")
    best_execution_mode = winner_bundle.get("execution_mode", "일반 종가모드")
    best_loc_buy_ratio = float(winner_bundle.get("loc_buy_ratio", 0.0))
    best_loc_buy_offset = winner_bundle.get("loc_buy_offset", 0.0)
    best_loc_sell_offset = winner_bundle.get("loc_sell_offset", 0.0)

    # ------------------------------------------------------------
    # 2026 완전 블라인드 검증
    # ------------------------------------------------------------
    blind_2026 = None
    if blind_validation_2026:
        validation_mode = best_mode
        vdf = pd.DataFrame(index=close.index)
        vdf["SIGNAL"] = close[validation_mode["signal_symbol"]]
        vdf["TRADE"] = close[validation_mode["trade_symbol"]]
        vdf["REF"] = close[validation_mode["ref_symbol"]]
        # SOXL C타입 블라인드 검증도 본 백테스트와 동일하게 QQQ/SMH를 전달해야 합니다.
        # 기존 코드는 validation dataframe에서 두 열을 빠뜨려 simulate_soxl_reverse()가 즉시 실패했습니다.
        if market.startswith("🇺🇸") and us_product == "SOXL":
            vdf["QQQ"] = close["QQQ"]
            vdf["SMH"] = close["SMH"]
        vdf = vdf.dropna()
        # 사용자가 선택한 시작/종료 범위는 존중하되, 최적화 때만 2025-12-31에서 잘랐습니다.
        vdf = apply_backtest_period(
            vdf,
            backtest_period_mode,
            selected_start_year,
            selected_end_year,
            selected_start_date,
            selected_end_date,
        )
        if not vdf.empty:
            # 모든 지표는 전체 선택구간으로 먼저 계산합니다. 따라서 2026-01-01의 신호는
            # 2025년까지 이미 알고 있던 과거 정보만 자연스럽게 사용합니다.
            vdf["ANCHOR"] = anchor_series(vdf["SIGNAL"], anchor_mode)
            vdf["RSI14"] = calc_rsi(vdf["REF"], 14)
            for p_ma in ma_periods:
                p_ma = int(p_ma)
                vdf[f"MA{p_ma}"] = vdf["REF"].rolling(p_ma).mean()
                vdf[f"TREND_OK_{p_ma}"] = vdf["REF"] > vdf[f"MA{p_ma}"]
            req = ["SIGNAL", "TRADE", "REF", "ANCHOR", "RSI14"]
            req += [f"TREND_OK_{int(p_ma)}" for p_ma in ma_periods]
            vdf = vdf.dropna(subset=req)

        if not vdf.empty and (vdf.index >= pd.Timestamp("2026-01-01")).any():
            fixed_sim = simulate(
                vdf, float(investment), best_step, best_tp, tranche_count,
                best_ma_period, best_rsi, best_allocation_weights, best_max_hold,
                best_execution_mode, float(best_loc_buy_offset), float(best_loc_sell_offset),
                best_loc_buy_ratio, float(fee_pct), float(slippage_pct), float(fx_cost_pct),
                best_deployment_ratio,
            )
            eq_full = fixed_sim["equity"].copy()
            test_eq = eq_full.loc[eq_full.index >= pd.Timestamp("2026-01-01")].copy()
            pre_eq = eq_full.loc[eq_full.index < pd.Timestamp("2026-01-01")]
            if not test_eq.empty:
                start_equity = float(pre_eq["Equity"].iloc[-1]) if not pre_eq.empty else float(test_eq["Equity"].iloc[0])
                end_equity = float(test_eq["Equity"].iloc[-1])
                test_return = end_equity / start_equity - 1 if start_equity > 0 else np.nan
                test_days = max((pd.Timestamp(test_eq.index[-1]) - pd.Timestamp("2026-01-01")).days, 1)
                test_years = test_days / 365.25
                test_cagr = (end_equity / start_equity) ** (1 / test_years) - 1 if start_equity > 0 and end_equity > 0 else np.nan
                # MDD는 2026 시작자산도 직전 고점 후보로 포함해 계산합니다.
                eq_for_dd = pd.concat([
                    pd.Series([start_equity], index=[pd.Timestamp("2025-12-31")]),
                    test_eq["Equity"],
                ])
                test_peak = eq_for_dd.cummax()
                test_mdd = float((eq_for_dd / test_peak - 1).min())
                test_trades = fixed_sim["trades"]
                if isinstance(test_trades, pd.DataFrame) and not test_trades.empty and "매도일" in test_trades.columns:
                    test_trade_count = int((pd.to_datetime(test_trades["매도일"]) >= pd.Timestamp("2026-01-01")).sum())
                else:
                    test_trade_count = 0
                blind_2026 = {
                    "return": float(test_return),
                    "cagr": float(test_cagr),
                    "mdd": float(test_mdd),
                    "trade_count": test_trade_count,
                    "start_equity": start_equity,
                    "end_equity": end_equity,
                    "end_date": pd.Timestamp(test_eq.index[-1]),
                    "sim": fixed_sim,
                }

    bt_start = pd.Timestamp(best_df.index.min())
    bt_end = pd.Timestamp(best_df.index.max())
    bt_years = max((bt_end - bt_start).days / 365.25, 0)
    st.caption(
        f"📅 실제 백테스트 사용기간: {bt_start:%Y-%m-%d} ~ {bt_end:%Y-%m-%d} "
        f"(약 {bt_years:.1f}년) · 선택모드: {backtest_period_mode}"
    )

    if blind_validation_2026:
        st.subheader("🧪 2026 블라인드 검증")
        if blind_2026 is None:
            st.warning(
                "현재 선택한 기간에는 2026년 검증 데이터가 충분하지 않습니다. "
                "전체기간 또는 2026년을 포함하는 기간으로 실행해 주세요."
            )
        else:
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("2026 수익률", f"{blind_2026['return']:.1%}")
            b2.metric("2026 연환산", f"{blind_2026['cagr']:.1%}")
            b3.metric("2026 MDD", f"{blind_2026['mdd']:.1%}")
            b4.metric("2026 완료매매", f"{blind_2026['trade_count']}회")
            st.caption(
                f"최적화: ~ 2025-12-31 · 검증: 2026-01-01 ~ {blind_2026['end_date']:%Y-%m-%d} · "
                "2026 데이터는 전략/파라미터 선정에 사용하지 않았습니다."
            )
            if blind_2026["return"] > 0:
                st.success(
                    "✅ 2026 미사용 데이터에서도 플러스 성과입니다. "
                    "최근 구간만 보고 맞춘 전략일 가능성을 한 단계 낮춰주는 결과입니다."
                )
            else:
                st.warning(
                    "⚠️ 2026 미사용 데이터 성과가 마이너스입니다. "
                    "학습구간 성과 대비 과최적화 가능성을 더 강하게 점검해야 합니다."
                )

    st.success(
        f"🥇 CAGR 1위: **{winner['운용방식']}** · "
        f"완료매매 {int(winner['완료매매'])}회 · MDD {winner['MDD']:.1%}"
    )
    st.caption(
        f"선정 기준: CAGR 최우선 · 완료매매 {min_completed_trades}회 이상 확인 · "
        f"MDD {max_allowed_mdd:.0%}는 참고 기준이며 탈락 조건으로 사용하지 않습니다."
    )

    if market.startswith("🇺🇸"):
        sell_desc = (
            f"LOC 매도(+{best_loc_sell_offset:.2%})"
            if best_execution_mode == "LOC 모드" else "일반 종가 매도"
        )
        st.info(
            f"🇺🇸 1위 매수 혼합: 종가 {1-best_loc_buy_ratio:.0%} + "
            f"LOC {best_loc_buy_ratio:.0%} (전일 종가 대비 -{best_loc_buy_offset:.2%}) · "
            f"익절: {sell_desc}"
        )
    allocation_desc = (
        "상태·노출별 매수비중 자동가중"
        if market.startswith("🇺🇸") and us_product == "SOXL"
        else "차수별 매수비중 자동 최적화"
    )
    st.write(
        f"**{best_step:.0%} 간격 / {best_tp:.0%} 익절 / "
        f"총자금 {best_deployment_ratio:.0%} 운용 / {allocation_desc} / "
        f"{filter_name(best_ma_period, best_rsi)} / "
        f"최대보유 {'제한없음' if best_max_hold is None else str(best_max_hold) + '일'}**"
    )

    st.subheader("🧪 기간별 전략 검증 가이드")
    st.caption(
        "실전 전략을 고를 때는 한 기간의 1위만 따라가기보다 "
        "3년·5년·10년 구간에서 반복해서 상위권인지 확인하는 것이 좋습니다."
    )

    validation_windows = st.multiselect(
        "전략 검증 기간",
        [3, 5, 10],
        default=[3, 5, 10],
        format_func=lambda x: f"최근 {x}년",
        key="validation_windows",
    )

    if validation_windows:
        current_bt_end = pd.Timestamp(best_df.index.max())
        validation_rows = []

        # 현재 실행 결과를 기준으로, 선택 기간이 충분히 길면 해당 기간의 대표 정보를 표시.
        # 정확한 기간별 재최적화는 아래 '워크포워드' 안내에 따라 별도 실행하도록 명확히 구분.
        for years in validation_windows:
            start_cut = current_bt_end - pd.DateOffset(years=int(years))
            available = best_df.loc[best_df.index >= start_cut]
            validation_rows.append({
                "검증창": f"최근 {years}년",
                "데이터 시작": available.index.min().strftime("%Y-%m-%d") if not available.empty else "-",
                "데이터 종료": available.index.max().strftime("%Y-%m-%d") if not available.empty else "-",
                "거래일": len(available),
                "현재 전체기간 1위 전략": winner["전략"],
            })

        st.dataframe(pd.DataFrame(validation_rows), use_container_width=True, hide_index=True)

    st.info(
        "📌 워크포워드 원칙: 예를 들어 2022년을 평가할 때는 2022년 데이터를 전략 선택에 쓰지 않고, "
        "직전 3년/5년/10년으로 전략을 고른 뒤 2022년 성과를 확인합니다. "
        "이 방식이 단순 전체기간 1위보다 과최적화 여부를 확인하는 데 유리합니다."
    )

    st.subheader("🔄 전체 연도 워크포워드 검증")
    st.caption(
        "데이터가 허용하는 모든 실전연도에 대해 직전 3년·5년·10년 자료만으로 "
        "전략을 선정하고 다음 1년 성과를 연결합니다. 계산 시간이 길 수 있습니다."
    )

    run_walk_forward = st.button("🔬 전체 연도 워크포워드 실행", use_container_width=True)

    if run_walk_forward:
        wf_windows = [3, 5, 10]
        all_years = sorted(pd.DatetimeIndex(close.index).year.unique())
        current_year = int(max(all_years))
        first_year = int(min(all_years))
        test_years = [int(y) for y in all_years if int(y) >= first_year + 3]
        wf_rows = []
        wf_year_rows = []

        wf_progress = st.progress(0.0)
        wf_status = st.empty()

        strategy_defs = list(sims.items())
        total_wf_jobs = len(wf_windows) * len(test_years)
        wf_done = 0

        for train_years in wf_windows:
            capital = float(investment)
            equity_points = [capital]
            applied = 0

            for test_year in test_years:
                train_start = pd.Timestamp(f"{test_year - train_years}-01-01")
                train_end = pd.Timestamp(f"{test_year - 1}-12-31")
                test_start = pd.Timestamp(f"{test_year}-01-01")
                test_end = pd.Timestamp(f"{test_year}-12-31")

                candidates = []

                for strategy_name, bundle in strategy_defs:
                    mode = bundle["mode"]

                    base = pd.DataFrame(index=close.index)
                    base["SIGNAL"] = close[mode["signal_symbol"]]
                    base["TRADE"] = close[mode["trade_symbol"]]
                    base["REF"] = close[mode["ref_symbol"]]
                    base = base.dropna()

                    base["ANCHOR"] = anchor_series(base["SIGNAL"], anchor_mode)
                    base["RSI14"] = calc_rsi(base["REF"], 14)
                    for p in [100, 150, 200]:
                        base[f"MA{p}"] = base["REF"].rolling(p).mean()
                        base[f"TREND_OK_{p}"] = base["REF"] > base[f"MA{p}"]

                    train_df = base.loc[
                        (base.index >= train_start) & (base.index <= train_end)
                    ].copy()

                    ma_period = bundle.get("ma_period")
                    required_cols = ["SIGNAL", "TRADE", "REF", "ANCHOR", "RSI14"]
                    if ma_period is not None:
                        required_cols.append(f"TREND_OK_{int(ma_period)}")
                    train_df = train_df.dropna(subset=required_cols)

                    if len(train_df) < max(100, int(train_years) * 200):
                        continue

                    train_sim = simulate(
                        train_df,
                        10000.0,
                        bundle["step_pct"],
                        bundle["tp"],
                        tranche_count,
                        ma_period,
                        bundle["rsi_max"],
                        bundle.get("allocation_weights"),
                        bundle["max_hold_days"],
                        bundle["execution_mode"],
                        bundle["loc_buy_offset"],
                        bundle["loc_sell_offset"],
                        bundle.get("loc_buy_ratio", 0.0),
                        float(fee_pct),
                        float(slippage_pct),
                        float(fx_cost_pct),
                        bundle.get("deployment_ratio", 1.0),
                    )
                    train_eligible = (
                        train_sim["trade_count"] >= int(min_completed_trades)
                        and abs(train_sim["mdd"]) <= float(max_allowed_mdd)
                    )
                    train_score = (
                        train_sim["cagr"] - 0.50 * abs(train_sim["mdd"])
                        - 0.02 * min(train_sim["max_hold"] / 365.0, 5.0)
                        - 0.05 * (1 - train_sim["avg_exposure"])
                    )
                    candidates.append(
                        (train_eligible, train_score, train_sim["final_value"], strategy_name, bundle, base)
                    )

                if candidates:
                    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
                    _, _, _, chosen_name, chosen_bundle, chosen_base = candidates[0]

                    test_df = chosen_base.loc[
                        (chosen_base.index >= test_start) & (chosen_base.index <= test_end)
                    ].copy()

                    ma_period = chosen_bundle.get("ma_period")
                    required_cols = ["SIGNAL", "TRADE", "REF", "ANCHOR", "RSI14"]
                    if ma_period is not None:
                        required_cols.append(f"TREND_OK_{int(ma_period)}")
                    test_df = test_df.dropna(subset=required_cols)

                    if len(test_df) >= 20:
                        start_capital = capital
                        test_sim = simulate(
                            test_df,
                            capital,
                            chosen_bundle["step_pct"],
                            chosen_bundle["tp"],
                            tranche_count,
                            ma_period,
                            chosen_bundle["rsi_max"],
                            chosen_bundle.get("allocation_weights"),
                            chosen_bundle["max_hold_days"],
                            chosen_bundle["execution_mode"],
                            chosen_bundle["loc_buy_offset"],
                            chosen_bundle["loc_sell_offset"],
                            chosen_bundle.get("loc_buy_ratio", 0.0),
                            float(fee_pct),
                            float(slippage_pct),
                            float(fx_cost_pct),
                            chosen_bundle.get("deployment_ratio", 1.0),
                        )
                        capital = float(test_sim["final_value"])
                        year_return = capital / start_capital - 1 if start_capital else 0.0
                        equity_points.append(capital)
                        applied += 1

                        wf_year_rows.append({
                            "학습기간": f"{train_years}년",
                            "실전연도": f"{test_year}{' YTD' if test_year == current_year else ''}",
                            "학습구간": f"{test_year-train_years}~{test_year-1}",
                            "선택전략": chosen_name,
                            "연초자산": start_capital,
                            "연말/현재자산": capital,
                            "수익률": year_return,
                        })

                wf_done += 1
                wf_progress.progress(wf_done / total_wf_jobs)
                wf_status.caption(
                    f"계산 중 · {train_years}년 학습 → {test_year}{' YTD' if test_year == current_year else ''}"
                )

            if applied:
                total_return = capital / float(investment) - 1
                years_equiv = max(applied - (0.5 if current_year in test_years else 0), 0.5)
                cagr = (capital / float(investment)) ** (1 / years_equiv) - 1
                eq = pd.Series(equity_points, dtype=float)
                mdd = float((eq / eq.cummax() - 1).min())

                wf_rows.append({
                    "학습기간": f"{train_years}년",
                    "검증": f"{current_year-1} + {current_year} YTD",
                    "최종자산": capital,
                    "누적수익률": total_return,
                    "참고 CAGR": cagr,
                    "연도단위 MDD": mdd,
                })

        wf_progress.progress(1.0)
        wf_status.caption("전체 연도 워크포워드 계산 완료")

        if wf_rows:
            wf_result = pd.DataFrame(wf_rows).sort_values(
                ["최종자산", "참고 CAGR"], ascending=False
            ).reset_index(drop=True)

            wf_view = wf_result.copy()
            wf_view["최종자산"] = wf_view["최종자산"].map(lambda x: f"{currency}{x:,.0f}")
            wf_view["누적수익률"] = wf_view["누적수익률"].map(lambda x: f"{x:.1%}")
            wf_view["참고 CAGR"] = wf_view["참고 CAGR"].map(lambda x: f"{x:.1%}")
            wf_view["연도단위 MDD"] = wf_view["연도단위 MDD"].map(lambda x: f"{x:.1%}")
            st.dataframe(wf_view, use_container_width=True, hide_index=True)

            wf_best = wf_result.iloc[0]
            st.success(
                f"🏆 현재 추천 학습기간: **{wf_best['학습기간']}** · "
                f"{current_year-1} + {current_year} YTD 누적수익률 "
                f"{wf_best['누적수익률']:.1%}"
            )

            if wf_year_rows:
                with st.expander("전체 연도별 선택 전략 보기"):
                    detail = pd.DataFrame(wf_year_rows)
                    detail["연초자산"] = detail["연초자산"].map(
                        lambda x: f"{currency}{x:,.0f}"
                    )
                    detail["연말/현재자산"] = detail["연말/현재자산"].map(
                        lambda x: f"{currency}{x:,.0f}"
                    )
                    detail["수익률"] = detail["수익률"].map(lambda x: f"{x:.1%}")
                    st.dataframe(detail, use_container_width=True, hide_index=True)
        else:
            st.warning("전체 연도 워크포워드에 사용할 데이터가 충분하지 않습니다.")

    # C-ORIGINAL은 원본 주문 역추적 전용입니다. 기존 MA/운용률/배분/혼합 최적화 화면은
    # 결과를 혼동시키므로 여기서 종료하고 고정 로직 성과만 표시합니다.
    if market.startswith("🇺🇸") and us_product == "SOXL" and soxl_track.startswith("C-ORIGINAL"):
        st.subheader("🎯 C-ORIGINAL 고정 로직 결과")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("최종자산", f"{currency}{best['final_value']:,.0f}")
        c2.metric("CAGR", f"{best['cagr']:.1%}")
        c3.metric("MDD", f"{best['mdd']:.1%}")
        c4.metric("완료매매", f"{int(best.get('trade_count', 0)):,}회")
        st.caption("고정 C-ORIGINAL 1개만 계산한 결과입니다. 이동평균선·총자금 운용률·매수비중 추가 최적화는 실행하지 않습니다.")
        st.info("다음 단계는 CAGR 최적화가 아니라 3~7월 실제 SOXL 주문과 날짜별 예측 주문을 채점하는 것입니다. 8월 데이터는 블라인드 검증용으로 유지합니다.")
        st.stop()

    st.subheader("📈 이동평균선 방식 비교")
    ma_summary = (
        result.sort_values(["최종자산", "CAGR"], ascending=False)
        .groupby("이평선", as_index=False)
        .first()
        .sort_values(["최종자산", "CAGR"], ascending=False)
    )
    ma_view = ma_summary[
        ["이평선", "최종자산", "CAGR", "MDD", "최대보유일", "완료매매"]
    ].copy()
    ma_view["최종자산"] = ma_view["최종자산"].map(lambda x: f"{currency}{x:,.0f}")
    ma_view["CAGR"] = ma_view["CAGR"].map(lambda x: f"{x:.1%}")
    ma_view["MDD"] = ma_view["MDD"].map(lambda x: f"{x:.1%}")
    ma_view["최대보유일"] = ma_view["최대보유일"].map(lambda x: f"{x:.0f}일")
    st.dataframe(ma_view, use_container_width=True, hide_index=True)

    best_ma_row = ma_summary.iloc[0]
    st.success(
        f"🏆 이평선 방식 1위: **{best_ma_row['이평선']}** · "
        f"최종자산 {currency}{best_ma_row['최종자산']:,.0f} · "
        f"CAGR {best_ma_row['CAGR']:.1%} · MDD {best_ma_row['MDD']:.1%}"
    )

    st.subheader("💵 총자금 운용률 비교")
    deployment_summary = (
        result.groupby("총자금 운용률", as_index=False).first()
        .sort_values(["실전기준통과", "실전점수", "최종자산"], ascending=False)
    )
    deployment_view = deployment_summary[
        ["총자금 운용률", "평균 투자금 사용률", "최종자산", "CAGR", "MDD", "실전점수"]
    ].copy()
    deployment_view["총자금 운용률"] = deployment_view["총자금 운용률"].map(lambda x: f"{x:.0%}")
    deployment_view["평균 투자금 사용률"] = deployment_view["평균 투자금 사용률"].map(lambda x: f"{x:.1%}")
    deployment_view["최종자산"] = deployment_view["최종자산"].map(lambda x: f"{currency}{x:,.0f}")
    deployment_view["CAGR"] = deployment_view["CAGR"].map(lambda x: f"{x:.1%}")
    deployment_view["MDD"] = deployment_view["MDD"].map(lambda x: f"{x:.1%}")
    deployment_view["실전점수"] = deployment_view["실전점수"].map(lambda x: f"{x:.2%}")
    st.dataframe(deployment_view, use_container_width=True, hide_index=True)
    best_deployment_row = deployment_summary.iloc[0]
    st.success(
        f"🏆 운용률 1위: **총자금의 {best_deployment_row['총자금 운용률']:.0%} 운용** · "
        f"평균 실제 투자 {best_deployment_row['평균 투자금 사용률']:.1%} · "
        f"CAGR {best_deployment_row['CAGR']:.1%} · MDD {best_deployment_row['MDD']:.1%}"
    )

    st.subheader("💯 총자산 대비 매수 % 자동 최적화")
    allocation_summary = (
        result.sort_values(["최종자산", "CAGR"], ascending=False)
        .groupby("차수별 매수%", as_index=False)
        .first()
        .sort_values(["최종자산", "CAGR"], ascending=False)
    )

    allocation_view_summary = allocation_summary[
        ["차수별 매수%", "최종자산", "CAGR", "MDD", "최대보유일", "완료매매"]
    ].copy()
    allocation_view_summary["최종자산"] = allocation_view_summary["최종자산"].map(
        lambda x: f"{currency}{x:,.0f}"
    )
    allocation_view_summary["CAGR"] = allocation_view_summary["CAGR"].map(lambda x: f"{x:.1%}")
    allocation_view_summary["MDD"] = allocation_view_summary["MDD"].map(lambda x: f"{x:.1%}")
    allocation_view_summary["최대보유일"] = allocation_view_summary["최대보유일"].map(lambda x: f"{x:.0f}일")
    st.dataframe(allocation_view_summary, use_container_width=True, hide_index=True)

    best_allocation_row = allocation_summary.iloc[0]
    st.success(
        f"🏆 차수별 최적 매수비중: **{best_allocation_row['차수별 매수%']}** · "
        f"최종자산 {currency}{best_allocation_row['최종자산']:,.0f} · "
        f"CAGR {best_allocation_row['CAGR']:.1%} · "
        f"MDD {best_allocation_row['MDD']:.1%}"
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
        actual_trade_target = us_product
        actual_trade_symbol = us_product

    st.caption(
        "코코레 신호를 보면서 실제 거래는 본주 또는 코코레 중 선택할 수 있습니다. "
        "선택한 종목별로 보유 차수와 평균단가를 따로 계산합니다. "
        "새 매수부터는 사이클 전략도 함께 저장됩니다. 입력 즉시 자동저장되지만, "
        "Streamlit Cloud 재배포 시 파일이 초기화될 수 있으므로 CSV 백업도 보관해 주세요."
    )

    if "live_trades" not in st.session_state:
        st.session_state.live_trades = load_autosaved_trades()

    if _remote_store_config():
        st.success("☁️ 매매기록 영구저장 연결됨")
    else:
        st.warning(
            "현재는 로컬+CSV 백업 모드입니다. Streamlit 재배포 후 자동복구가 필요하면 "
            "secrets의 [trade_store]에 url, key, user_key를 설정하세요."
        )

    save_status = st.session_state.get("last_save_status")
    if save_status:
        remote_text = (
            "영구저장 완료" if save_status.get("remote") is True
            else "영구저장 미사용" if save_status.get("remote") is None
            else "영구저장 실패"
        )
        st.caption(
            f"최근 저장: {remote_text} · 기록 확인코드 {save_status.get('checksum', '-')}"
        )

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
                    autosave_live_trades(st.session_state.live_trades)
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
    live_partial_stage = None
    cycle_strategy_name = None
    cycle_allocation_raw = None
    cycle_deployment_ratio = None

    if not trade_log.empty:
        for _, rec in trade_log.iterrows():
            rec_target = str(rec.get("종목", actual_trade_target))
            if rec_target != actual_trade_target:
                continue

            side = str(rec.get("구분", ""))
            fill_status = str(rec.get("체결상태", "전량체결") or "전량체결")
            if fill_status in ("미체결", "취소"):
                continue
            px = float(rec.get("가격", 0) or 0)
            amount = float(rec.get("금액", 0) or 0)
            raw_qty = rec.get("수량", None)
            try:
                qty = float(raw_qty) if pd.notna(raw_qty) and str(raw_qty).strip() else 0.0
            except Exception:
                qty = 0.0

            # 구버전 CSV에는 수량 열이 없으므로 금액/가격으로 자동 복원합니다.
            if side == "매수" and px > 0 and (amount > 0 or qty > 0):
                if qty <= 0 and amount > 0:
                    qty = amount / px
                if amount <= 0 and qty > 0:
                    amount = qty * px
                if live_buys == 0:
                    try:
                        live_first_buy_date = pd.Timestamp(rec.get("날짜"))
                    except Exception:
                        live_first_buy_date = None
                    raw_strategy = rec.get("사이클전략", None)
                    if pd.notna(raw_strategy) and str(raw_strategy).strip():
                        cycle_strategy_name = str(raw_strategy)
                    raw_weight = rec.get("사이클매수비중", None)
                    if pd.notna(raw_weight) and str(raw_weight).strip():
                        cycle_allocation_raw = str(raw_weight)
                    raw_deployment = rec.get("사이클운용비율", None)
                    if pd.notna(raw_deployment) and str(raw_deployment).strip():
                        try:
                            cycle_deployment_ratio = float(raw_deployment)
                        except Exception:
                            cycle_deployment_ratio = None
                live_qty += qty
                live_cost += amount
                raw_stage = rec.get("차수", None)
                try:
                    rec_stage = int(float(raw_stage)) if pd.notna(raw_stage) else live_buys + 1
                except Exception:
                    rec_stage = live_buys + 1
                if fill_status == "부분체결":
                    live_partial_stage = rec_stage
                    live_buys = max(live_buys, rec_stage - 1)
                else:
                    live_buys = max(live_buys, rec_stage)
                    if live_partial_stage == rec_stage:
                        live_partial_stage = None
            elif side in ("부분매도", "전량매도") and live_qty > 0:
                sold_qty = min(max(qty, 0.0), live_qty)
                if side == "전량매도" or sold_qty >= live_qty - 1e-8:
                    live_qty = 0.0
                    live_cost = 0.0
                    live_buys = 0
                    live_partial_stage = None
                    live_first_buy_date = None
                    cycle_strategy_name = None
                    cycle_allocation_raw = None
                    cycle_deployment_ratio = None
                elif sold_qty > 0:
                    remaining_ratio = (live_qty - sold_qty) / live_qty
                    live_cost *= remaining_ratio
                    live_qty -= sold_qty

    auto_avg_price = live_cost / live_qty if live_qty > 0 else 0.0
    auto_stage = min(live_buys, tranche_count)

    # 실제 보유 사이클이 시작되면 그때의 전략을 전량매도까지 고정합니다.
    live_strategy_name = winner_name
    live_bundle = winner_bundle
    cycle_is_locked = False
    legacy_cycle = False

    if auto_stage > 0:
        if cycle_strategy_name and cycle_strategy_name in sims:
            live_strategy_name = cycle_strategy_name
            live_bundle = sims[cycle_strategy_name]
            cycle_is_locked = True
        elif cycle_strategy_name:
            # 현재 백테스트 조합에서 예전 전략을 찾지 못한 경우에도
            # 매수비중은 CSV에 저장된 값을 우선 유지합니다.
            live_strategy_name = cycle_strategy_name
            live_bundle = winner_bundle
            cycle_is_locked = True
        else:
            legacy_cycle = True

    live_best = live_bundle["sim"]
    live_best_mode = live_bundle["mode"]
    live_best_df = live_bundle["df"]
    live_best_step = float(live_bundle["step_pct"])
    live_best_tp = float(live_bundle["tp"])
    live_best_ma_period = live_bundle.get("ma_period")
    live_best_rsi = live_bundle["rsi_max"]
    live_best_allocation_weights = normalize_allocation_weights(
        tranche_count, live_bundle.get("allocation_weights")
    )
    live_best_deployment_ratio = float(live_bundle.get("deployment_ratio", 1.0))
    saved_cycle_allocation = parse_saved_allocation(cycle_allocation_raw, tranche_count)
    if saved_cycle_allocation is not None:
        live_best_allocation_weights = saved_cycle_allocation
    if cycle_deployment_ratio is not None and 0 < cycle_deployment_ratio <= 1:
        live_best_deployment_ratio = cycle_deployment_ratio
    live_best_max_hold = live_bundle.get("max_hold_days")
    live_best_execution_mode = live_bundle.get("execution_mode", "일반 종가모드")
    live_best_loc_buy_ratio = float(live_bundle.get("loc_buy_ratio", 0.0))
    live_best_loc_buy_offset = live_bundle.get("loc_buy_offset", 0.0)
    live_best_loc_sell_offset = live_bundle.get("loc_sell_offset", 0.0)

    log1, log2, log3, log4 = st.columns(4)
    stage_label = f"{auto_stage}차"
    if live_partial_stage is not None:
        stage_label += f" · {live_partial_stage}차 부분체결"
    log1.metric("자동 보유 차수", stage_label)
    log2.metric(
        "자동 평균단가",
        f"{currency}{auto_avg_price:,.2f}" if live_qty > 0 else "-",
    )
    qty_text = f"{live_qty:,.0f}주" if market.startswith("🇰🇷") else f"{live_qty:,.4f}주"
    log3.metric("보유 수량", qty_text if live_qty > 0 else "-")
    log4.metric("투입금액", f"{currency}{live_cost:,.0f}")

    if auto_stage > 0 and cycle_is_locked:
        st.success(
            f"🔒 현재 사이클 전략 고정: **차수별 매수 % 저장됨** · "
            f"{live_best_step:.0%} 간격 / {live_best_tp:.0%} 익절 · "
            "전량매도 전까지 오늘의 1위가 바뀌어도 이 전략을 유지합니다."
        )
    elif auto_stage > 0 and legacy_cycle:
        st.warning(
            "기존 백업 CSV에는 사이클 전략 정보가 없습니다. "
            "아래 버튼을 누르면 현재 1위 전략을 이번 보유 사이클에 고정합니다."
        )
        if st.button("🔒 기존 보유 사이클을 현재 1위 전략으로 고정", use_container_width=True):
            # 마지막 전량매도 이후, 선택 종목의 매수 기록에 전략 스냅샷을 채웁니다.
            last_sell_idx = -1
            for i, rec in enumerate(st.session_state.live_trades):
                if str(rec.get("종목", actual_trade_target)) == actual_trade_target and str(rec.get("구분", "")) == "전량매도":
                    last_sell_idx = i
            for i in range(last_sell_idx + 1, len(st.session_state.live_trades)):
                rec = st.session_state.live_trades[i]
                if str(rec.get("종목", actual_trade_target)) == actual_trade_target and str(rec.get("구분", "")) == "매수":
                    rec["사이클전략"] = winner_name
                    rec["사이클매수비중"] = serialize_allocation(best_allocation_weights)
                    rec["사이클운용비율"] = best_deployment_ratio
            autosave_live_trades(st.session_state.live_trades)
            st.rerun()
    elif auto_stage == 0:
        st.caption(
            f"다음 1차 매수 시 현재 1위의 **차수별 매수 %**를 자동 저장하고, "
            "그 사이클이 끝날 때까지 고정합니다."
        )

    with st.expander("➕ 매수/매도 기록 입력", expanded=False):
        record_date = st.date_input("거래일", value=date.today())
        latest_record_price, _record_price_source, _record_price_time = get_latest_market_price(actual_trade_symbol)
        selected_live_price = (
            latest_record_price
            if latest_record_price is not None
            else float(close[actual_trade_symbol].dropna().iloc[-1])
        )

        record_price = st.number_input(
            f"실제 체결가격 ({unit})",
            min_value=0.0,
            value=selected_live_price,
            step=1.0 if currency == "$" else 10.0,
            key="record_price",
        )
        record_weights = (
            live_best_allocation_weights if auto_stage > 0 else best_allocation_weights
        )
        record_budgets = make_tranche_budgets(
            float(investment) * (
                live_best_deployment_ratio if auto_stage > 0 else best_deployment_ratio
            ), tranche_count, record_weights
        )
        record_next_stage = min(auto_stage + 1, tranche_count)
        record_weight_pct = float(record_weights[record_next_stage - 1]) * (
            live_best_deployment_ratio if auto_stage > 0 else best_deployment_ratio
        )
        default_amount = float(record_budgets[record_next_stage - 1])
        default_qty = (default_amount / record_price) if record_price > 0 else 0.0

        order_status = st.selectbox(
            "매수/매도 체결상태",
            ["전량체결", "부분체결", "미체결", "취소"],
            help="부분체결은 실제 체결된 수량만 입력하세요. 해당 차수는 전량체결 기록 전까지 완료되지 않습니다.",
        )
        order_id = st.text_input(
            "주문번호/확인번호",
            value=f"{record_date}-{actual_trade_symbol}-B{record_next_stage}",
            help="증권사 주문번호가 있으면 바꿔 입력하세요. 동일 번호의 중복기록을 차단합니다.",
        ).strip()

        plan_row = live_best_df.iloc[-1]
        planned_signal_price = float(plan_row["ANCHOR"]) * (
            1 - live_best_step * record_next_stage
        )
        default_expected_price = selected_live_price
        if market.startswith("🇺🇸") and live_best_loc_buy_ratio > 0:
            series_for_plan = close[actual_trade_symbol].dropna()
            if not series_for_plan.empty:
                default_expected_price = min(
                    planned_signal_price,
                    float(series_for_plan.iloc[-1]) * (1 - live_best_loc_buy_offset),
                )
        expected_order_price = st.number_input(
            f"앱 예상 주문가격 ({unit})",
            min_value=0.0,
            value=max(float(default_expected_price), 0.0),
            step=1.0 if currency == "$" else 10.0,
            help="실제 체결가와 비교해 체결오차를 자동 계산합니다.",
        )

        if market.startswith("🇰🇷"):
            record_qty = st.number_input(
                "실제 체결 수량 (주)",
                min_value=0,
                value=max(1, int(default_qty)) if default_qty > 0 else 0,
                step=1,
                key="record_qty_kr",
                help="실제로 체결된 주식 수를 입력하세요.",
            )
            record_qty = float(record_qty)
        else:
            record_qty = st.number_input(
                "실제 체결 수량 (주)",
                min_value=0.0,
                value=round(default_qty, 4),
                step=0.0001,
                format="%.4f",
                key="record_qty_us",
                help="실제로 체결된 주식 수를 입력하세요. 소수점 매수도 입력할 수 있습니다.",
            )

        record_amount = float(record_price) * float(record_qty)
        actual_investment_pct = record_amount / float(investment) if investment > 0 else 0.0
        fill_error_pct = (
            float(record_price) / float(expected_order_price) - 1
            if expected_order_price > 0 and record_price > 0 else np.nan
        )
        st.caption(
            f"체결금액: {currency}{record_amount:,.2f} · "
            f"초기 투자금의 {actual_investment_pct:.2%} · "
            f"이번 차수 계획비중: {record_weight_pct:.2%} "
            f"({currency}{default_amount:,.2f})"
        )
        if np.isfinite(fill_error_pct):
            st.caption(f"예상 주문가 대비 실제 체결오차: {fill_error_pct:+.3%}")

        default_sell_qty = float(live_qty) if live_qty > 0 else 0.0
        if market.startswith("🇰🇷"):
            sell_qty = float(st.number_input(
                "매도 체결 수량 (주)", min_value=0, value=int(default_sell_qty), step=1,
                help="부분체결이면 실제 체결 수량만 입력하세요.",
            ))
        else:
            sell_qty = float(st.number_input(
                "매도 체결 수량 (주)", min_value=0.0, value=round(default_sell_qty, 4),
                step=0.0001, format="%.4f", help="부분체결이면 실제 체결 수량만 입력하세요.",
            ))
        expected_sell_price = (
            auto_avg_price * (1 + live_best_tp) if auto_avg_price > 0 else record_price
        )

        b1, b2 = st.columns(2)

        if b1.button("🟢 매수 기록", use_container_width=True):
            daily_filled_buy = 0.0
            for existing in st.session_state.live_trades:
                existing_status = str(existing.get("체결상태", "전량체결") or "전량체결")
                if (
                    str(existing.get("날짜", "")) == str(record_date)
                    and str(existing.get("종목", actual_trade_target)) == actual_trade_target
                    and str(existing.get("구분", "")) == "매수"
                    and existing_status in ("전량체결", "부분체결")
                ):
                    daily_filled_buy += float(existing.get("금액", 0) or 0)
            duplicate_buy = any(
                (
                    order_id and str(x.get("주문ID", "")).strip() == order_id
                ) or (
                    not order_id
                    and str(x.get("날짜", "")) == str(record_date)
                    and str(x.get("종목", actual_trade_target)) == actual_trade_target
                    and str(x.get("구분", "")) == "매수"
                    and str(x.get("차수", "")) == str(record_next_stage)
                )
                for x in st.session_state.live_trades
            )
            if auto_stage >= tranche_count:
                st.warning("설정한 분할매수 횟수를 이미 모두 사용했습니다.")
            elif duplicate_buy:
                st.warning("동일한 주문번호 또는 같은 날짜·차수의 기록이 있어 중복 입력을 막았습니다.")
            elif order_status in ("전량체결", "부분체결") and (record_price <= 0 or record_qty <= 0):
                st.warning("체결가격과 매수 수량을 입력해 주세요.")
            elif order_status in ("전량체결", "부분체결") and daily_filled_buy + record_amount > float(investment) * max_daily_buy_pct + 1e-6:
                st.warning(f"1일 최대 매수한도 {max_daily_buy_pct:.0%}를 초과해 기록하지 않았습니다.")
            elif order_status in ("전량체결", "부분체결") and live_cost + record_amount > float(investment) * max_total_deployed_pct + 1e-6:
                st.warning(f"총투입 한도 {max_total_deployed_pct:.0%}를 초과해 기록하지 않았습니다.")
            else:
                effective_qty = float(record_qty) if order_status in ("전량체결", "부분체결") else 0.0
                effective_amount = float(record_price) * effective_qty
                st.session_state.live_trades.append(
                    {
                        "날짜": str(record_date),
                        "종목": actual_trade_target,
                        "구분": "매수",
                        "체결상태": order_status,
                        "주문ID": order_id,
                        "차수": record_next_stage,
                        "가격": float(record_price),
                        "수량": effective_qty,
                        "금액": effective_amount,
                        "예상가격": float(expected_order_price),
                        "예상금액": float(default_amount),
                        "체결오차": float(fill_error_pct) if np.isfinite(fill_error_pct) and effective_qty > 0 else None,
                        "계획비중": record_weight_pct,
                        "실제투입비중": effective_amount / float(investment) if investment > 0 else 0.0,
                        "사이클전략": live_strategy_name if auto_stage > 0 and cycle_is_locked else winner_name,
                        "사이클매수비중": serialize_allocation(
                            live_best_allocation_weights if auto_stage > 0 and cycle_is_locked else best_allocation_weights
                        ),
                        "사이클운용비율": live_best_deployment_ratio if auto_stage > 0 and cycle_is_locked else best_deployment_ratio,
                    }
                )
                autosave_live_trades(st.session_state.live_trades)
                st.rerun()

        if b2.button("🔴 매도 기록", use_container_width=True):
            sell_order_id = order_id.replace(f"-B{record_next_stage}", "-SELL") if order_id else ""
            duplicate_sell = any(
                (
                    sell_order_id and str(x.get("주문ID", "")).strip() == sell_order_id
                ) or (
                    not sell_order_id
                    and str(x.get("날짜", "")) == str(record_date)
                    and str(x.get("종목", actual_trade_target)) == actual_trade_target
                    and str(x.get("구분", "")) in ("부분매도", "전량매도")
                )
                for x in st.session_state.live_trades
            )
            if live_qty <= 0 and order_status in ("전량체결", "부분체결"):
                st.warning("현재 기록상 보유 수량이 없습니다.")
            elif duplicate_sell:
                st.warning("동일한 매도 주문번호가 이미 있어 중복 입력을 막았습니다.")
            elif order_status in ("전량체결", "부분체결") and (record_price <= 0 or sell_qty <= 0):
                st.warning("매도 체결가격을 입력해 주세요.")
            elif sell_qty > live_qty + 1e-8:
                st.warning("현재 보유수량보다 많이 매도할 수 없습니다.")
            else:
                effective_sell_qty = sell_qty if order_status in ("전량체결", "부분체결") else 0.0
                proceeds = effective_sell_qty * float(record_price)
                is_full_exit = effective_sell_qty >= live_qty - 1e-8 and order_status == "전량체결"
                sell_error = (
                    float(record_price) / float(expected_sell_price) - 1
                    if expected_sell_price > 0 and effective_sell_qty > 0 else None
                )
                st.session_state.live_trades.append(
                    {
                        "날짜": str(record_date),
                        "종목": actual_trade_target,
                        "구분": "전량매도" if is_full_exit else "부분매도",
                        "체결상태": order_status,
                        "주문ID": sell_order_id,
                        "차수": auto_stage,
                        "가격": float(record_price),
                        "수량": float(effective_sell_qty),
                        "금액": float(proceeds),
                        "예상가격": float(expected_sell_price),
                        "예상금액": float(live_qty * expected_sell_price),
                        "체결오차": float(sell_error) if sell_error is not None else None,
                        "계획비중": None,
                        "실제투입비중": None,
                        "사이클전략": live_strategy_name if auto_stage > 0 else winner_name,
                        "사이클매수비중": serialize_allocation(
                            live_best_allocation_weights if auto_stage > 0 else best_allocation_weights
                        ),
                        "사이클운용비율": live_best_deployment_ratio if auto_stage > 0 else best_deployment_ratio,
                    }
                )
                autosave_live_trades(st.session_state.live_trades)
                st.rerun()

    trade_log = pd.DataFrame(st.session_state.live_trades)

    if not trade_log.empty:
        trade_log_view = trade_log.copy()
        for pct_col in ["계획비중", "실제투입비중"]:
            if pct_col in trade_log_view.columns:
                trade_log_view[pct_col] = trade_log_view[pct_col].map(
                    lambda x: f"{float(x):.2%}" if pd.notna(x) and str(x).strip() else "-"
                )
        st.dataframe(trade_log_view, use_container_width=True, hide_index=True)

        if "체결상태" in trade_log.columns:
            statuses = trade_log["체결상태"].fillna("전량체결").astype(str)
            total_orders = len(trade_log)
            filled_orders = int(statuses.isin(["전량체결", "부분체결"]).sum())
            full_orders = int((statuses == "전량체결").sum())
            fill_rate = filled_orders / total_orders if total_orders else 0.0
            q1, q2, q3 = st.columns(3)
            q1.metric("주문 기록", f"{total_orders}건")
            q2.metric("체결률", f"{fill_rate:.1%}")
            q3.metric("전량체결", f"{full_orders}건")

        if "체결오차" in trade_log.columns:
            errors = pd.to_numeric(trade_log["체결오차"], errors="coerce").dropna()
            if not errors.empty:
                e1, e2 = st.columns(2)
                e1.metric("평균 체결오차", f"{errors.mean():+.3%}")
                e2.metric("평균 절대오차", f"{errors.abs().mean():.3%}")
                st.caption("양수는 앱 예상가격보다 높게 체결, 음수는 낮게 체결된 것입니다.")

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
            autosave_live_trades([])
            st.rerun()

    # ------------------------------------------------------------
    # 오늘의 실제 매매 신호
    # ------------------------------------------------------------
    st.divider()
    st.subheader("🚦 다음 거래일 매매 신호")
    st.caption(
        "보유 중이면 1차 매수 때 저장된 사이클 전략을 전량매도까지 고정해서 사용합니다. "
        "보유가 없을 때만 현재 백테스트 1위 전략으로 새 사이클을 시작합니다."
    )

    live_stage = auto_stage

    latest = live_best_df.iloc[-1]
    # 신호 ETF 가격은 1위 전략의 신호 기준을 사용하고,
    # 실제 매수/매도 판단 가격은 사용자가 선택한 실제 거래 종목을 사용합니다.
    signal_price = float(latest["SIGNAL"])
    daily_trade_price = float(close[actual_trade_symbol].dropna().iloc[-1])
    latest_trade_price, latest_price_source, latest_price_time = get_latest_market_price(actual_trade_symbol)
    trade_price = (
        latest_trade_price
        if latest_trade_price is not None
        else daily_trade_price
    )
    latest_daily_date = pd.Timestamp(close.index.max())
    if latest_daily_date.tzinfo is not None:
        latest_daily_date = latest_daily_date.tz_localize(None)
    data_age_days = (pd.Timestamp.utcnow().tz_localize(None).normalize() - latest_daily_date.normalize()).days
    price_gap = abs(trade_price / daily_trade_price - 1) if daily_trade_price > 0 else float("inf")
    quote_age_minutes = float("inf")
    if latest_price_time is not None:
        try:
            quote_ts = pd.Timestamp(latest_price_time)
            if quote_ts.tzinfo is None:
                quote_ts = quote_ts.tz_localize(
                    "America/New_York" if market.startswith("🇺🇸") else "Asia/Seoul"
                )
            quote_age_minutes = max(
                0.0,
                (pd.Timestamp.now(tz="UTC") - quote_ts.tz_convert("UTC")).total_seconds() / 60,
            )
        except Exception:
            quote_age_minutes = float("inf")
    data_safe = (
        data_age_days <= 5
        and np.isfinite(trade_price)
        and trade_price > 0
        and price_gap <= max_price_gap_pct
        and quote_age_minutes <= max_quote_age_minutes
    )
    live_anchor = float(latest["ANCHOR"])
    live_dd = max(0.0, 1 - signal_price / live_anchor)

    live_rsi = float(latest["RSI14"])
    live_trend_ok = (
        bool(latest[f"TREND_OK_{int(live_best_ma_period)}"])
        if live_best_ma_period is not None
        else True
    )

    next_stage = int(live_stage) + 1

    live_tranche_budgets = make_tranche_budgets(
        float(investment) * live_best_deployment_ratio,
        tranche_count, live_best_allocation_weights
    )
    live_tranche_weights = normalize_allocation_weights(
        tranche_count, live_best_allocation_weights
    )
    tranche_budget = live_tranche_budgets[
        min(max(next_stage - 1, 0), tranche_count - 1)
    ]
    current_weight_pct = float(
        live_tranche_weights[min(max(next_stage - 1, 0), tranche_count - 1)]
    ) * live_best_deployment_ratio

    avg_buy_price = auto_avg_price if live_stage > 0 else None

    filter_ok = entry_allowed(latest, live_best_ma_period, live_best_rsi)

    loc_buy_fill_ok = True
    loc_buy_limit_today = None
    if market.startswith("🇺🇸") and live_best_loc_buy_ratio > 0:
        trade_series = close[actual_trade_symbol].dropna()
        if len(trade_series) >= 1:
            prev_close = float(trade_series.iloc[-1])
            loc_buy_limit_today = prev_close * (1 - live_best_loc_buy_offset)
            # 다음 거래일 체결 여부는 아직 알 수 없으므로 주문 신호만 냅니다.
            loc_buy_fill_ok = True

    filter_reasons = []
    if live_best_ma_period is not None and not live_trend_ok:
        filter_reasons.append(f"{int(live_best_ma_period)}일선 아래")
    if live_best_rsi is not None and live_rsi > live_best_rsi:
        filter_reasons.append(f"RSI {live_rsi:.1f} > {live_best_rsi:.0f}")

    next_signal_price = (
        live_anchor * (1 - live_best_step * next_stage)
        if next_stage <= tranche_count
        else None
    )

    signal = "대기"
    detail = ""
    loc_sell_limit_today = None

    if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
        target_sell = avg_buy_price * (1 + live_best_tp)
        effective_sell_target = target_sell
        if market.startswith("🇺🇸") and live_best_execution_mode == "LOC 모드":
            loc_sell_limit_today = target_sell * (1 + live_best_loc_sell_offset)
            effective_sell_target = loc_sell_limit_today

        holding_days_now = (
            (pd.Timestamp(live_best_df.index[-1]) - live_first_buy_date).days
            if live_first_buy_date is not None else 0
        )

        if trade_price >= effective_sell_target:
            signal = "익절"
            detail = (
                f"실제 매수 ETF 현재가가 익절 기준 "
                f"{currency}{effective_sell_target:,.2f} 이상입니다."
            )
        elif live_best_max_hold is not None and holding_days_now >= live_best_max_hold:
            signal = "기간청산"
            detail = (
                f"첫 매수 후 {holding_days_now}일이 지나 "
                f"최대 보유기간 {live_best_max_hold}일에 도달했습니다. "
                "단기회전 규칙에 따라 전량매도 신호입니다."
            )
        elif next_stage <= tranche_count and live_dd + 1e-12 >= live_best_step * next_stage:
            if filter_ok and ((1 - live_best_loc_buy_ratio) > 0 or loc_buy_fill_ok):
                signal = f"{next_stage}차 매수"
                detail = (
                    "가격 조건과 필터를 통과했습니다. "
                    + "LOC분은 다음 거래일 마감가격이 한도 조건을 만족할 때만 체결됩니다."
                )
            elif not filter_ok:
                detail = "가격 조건은 충족했지만 필터 때문에 대기: " + ", ".join(filter_reasons)
            else:
                detail = f"가격 신호는 충족했지만 LOC 한도 {currency}{loc_buy_limit_today:,.2f} 이하에서만 체결됩니다."
        elif next_stage <= tranche_count:
            detail = (
                f"다음 {next_stage}차 신호가격: "
                f"{currency}{next_signal_price:,.2f}"
            )
        else:
            detail = f"분할매수를 모두 사용했습니다. +{live_best_tp:.0%} 익절을 기다립니다."
    else:
        first_signal_price = live_anchor * (1 - live_best_step)

        if live_dd + 1e-12 >= live_best_step:
            if filter_ok and ((1 - live_best_loc_buy_ratio) > 0 or loc_buy_fill_ok):
                signal = "1차 매수"
                detail = (
                    "오늘 처음 시작 기준 1차 매수 조건과 필터를 통과했습니다. "
                    + "LOC분은 다음 거래일 마감가격이 한도 조건을 만족할 때만 체결됩니다."
                )
            elif not filter_ok:
                detail = "가격은 1차 구간이지만 필터 때문에 대기: " + ", ".join(filter_reasons)
            else:
                detail = f"1차 가격 신호는 충족했지만 LOC 한도 {currency}{loc_buy_limit_today:,.2f} 이하에서만 체결됩니다."
        else:
            detail = (
                f"오늘은 대기. 1차 신호가격은 약 "
                f"{currency}{first_signal_price:,.2f}"
            )

    if not data_safe:
        signal = "안전대기"
        age_text = "확인불가" if not np.isfinite(quote_age_minutes) else f"{quote_age_minutes:.0f}분"
        detail = (
            f"데이터 안전장치 작동: 최근 일봉 경과 {data_age_days}일, "
            f"최신시세 경과 {age_text}, 최신가와 일봉 차이 {price_gap:.1%}. "
            "데이터 확인 전에는 주문하지 않습니다."
        )

    if signal.endswith("매수") and emergency_stop:
        signal = "안전대기"
        detail = "긴급 매수 중지가 켜져 있어 신규 매수 주문을 내지 않습니다."
    elif signal.endswith("매수") and tranche_budget > float(investment) * max_daily_buy_pct + 1e-6:
        signal = "안전대기"
        detail = f"예정금액이 1일 최대 매수한도 {max_daily_buy_pct:.0%}를 초과해 주문을 보류합니다."
    elif signal.endswith("매수") and live_cost + tranche_budget > float(investment) * max_total_deployed_pct + 1e-6:
        signal = "안전대기"
        detail = f"주문 후 총투입금액이 안전한도 {max_total_deployed_pct:.0%}를 초과해 주문을 보류합니다."

    # SOXL 주문표는 매일 가격을 제시하는 방식이므로 상단에도 '대기' 대신 주문표 상태를 표시한다.
    if market.startswith("🇺🇸") and us_product == "SOXL" and signal in ["대기", "안전대기"]:
        signal = "LOC 주문값 제시" if data_safe else "LOC 주문값 제시 · 데이터확인"

    a1, a2, a3 = st.columns(3)
    signal_display = (
        f"{signal} · 총자금 {current_weight_pct:.2%}"
        if signal.endswith("매수")
        else (f"{signal} · 보유수량 100%" if signal in ["익절", "기간청산"] else signal)
    )
    a1.metric("다음 거래일 신호", signal_display)
    a2.metric("신호 ETF 가격", f"{currency}{signal_price:,.2f}")
    a3.metric("실제 매수 ETF 최신가", f"{currency}{trade_price:,.2f}")
    quote_time_text = format_quote_time(latest_price_time)
    st.caption(
        f"현재가 출처: {latest_price_source} · 최대 약 30초 캐시. "
        "실시간 거래소 직결 시세가 아니라 yfinance 지연/가용 시세입니다."
    )
    if quote_time_text:
        st.caption(f"🕒 시세 시각: {quote_time_text}")
    else:
        st.caption("🕒 시세 시각을 확인하지 못했습니다. 최근 일봉 가격일 수 있습니다.")

    st.write(f"**신호 기준:** {live_best_mode['mode']}")
    st.write(f"**실제 거래 종목:** {actual_trade_target}")
    if auto_stage > 0 and cycle_is_locked:
        st.write(f"**🔒 사이클 고정 전략:** 차수별 매수 % 저장됨 · {live_best_step:.0%} 간격 / {live_best_tp:.0%} 익절")
    elif auto_stage == 0:
        st.write(f"**새 사이클 적용 예정:** 현재 1위 차수별 매수 %")
    if market.startswith("🇺🇸") and us_product == "SOXL":
        trade_series = close[actual_trade_symbol].dropna()
        exposure_now_live = min(1.0, max(0.0, float(live_cost) / float(investment))) if float(investment) > 0 else 0.0
        base_unit_live = float(live_best_step) if 0.02 <= float(live_best_step) <= 0.10 else 0.05
        qqq_live = close["QQQ"].dropna() if "QQQ" in close.columns else None
        smh_live = close["SMH"].dropna() if "SMH" in close.columns else None
        soxl_plan = get_soxl_loc_plan(
            trade_series,
            exposure_now=exposure_now_live,
            base_unit=base_unit_live,
            qqq_series=qqq_live,
            smh_series=smh_live,
        )
        prev_close_live = float(soxl_plan.get("prev_close", trade_series.iloc[-1]))
        soxl_offsets = soxl_plan["offsets"]
        soxl_weights = soxl_plan["weights"]
        soxl_prices = [prev_close_live * (1 + x) for x in soxl_offsets]

        # 배치 후 총투입한도를 넘지 않도록 화면 주문비중도 순차적으로 제한한다.
        cap = min(float(live_best_deployment_ratio), 1.0)
        room = max(0.0, cap - exposure_now_live)
        shown_weights = []
        for w in soxl_weights:
            use_w = min(float(w), room)
            shown_weights.append(max(0.0, use_w))
            room -= use_w

        # 원래 주문표처럼 신호 충족/불충족에 따라 '대기'로 숨기지 않고,
        # 매 거래일 매수 2개 + 매도 2개의 LOC 기준값을 항상 제시한다.
        # 매도 기준은 오늘 제시되는 각 매수 블록이 전략 익절률에 도달하는 가격이다.
        # 실제 보유 블록은 각 블록의 실제 체결가를 기준으로 같은 익절률을 적용한다.
        soxl_sell_prices = [float(px) * (1 + float(live_best_tp)) for px in soxl_prices]

        # 오늘 주문판: 아래 가격은 표시용 임시값이 아니라 get_soxl_loc_plan()의
        # 실제 C-ORIGINAL/C-ALPHA offsets/weights에서 직접 계산한다.
        st.markdown("## 🎯 오늘 SOXL 실제 주문가격")
        # 한국시간과 미국 동부시간을 함께 표시해 주문 시점을 바로 확인할 수 있게 한다.
        try:
            from zoneinfo import ZoneInfo
            _now_utc = datetime.now(timezone.utc)
            _now_kr = _now_utc.astimezone(ZoneInfo("Asia/Seoul"))
            _now_ny = _now_utc.astimezone(ZoneInfo("America/New_York"))
            st.caption(
                f"🕒 현재시간 · 한국 {_now_kr:%Y-%m-%d %H:%M:%S} KST · "
                f"미국 동부 {_now_ny:%Y-%m-%d %H:%M:%S} ET"
            )
        except Exception:
            st.caption(f"🕒 현재 UTC {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}")
        st.caption(
            f"전략 계산값 직접 연결 · 상태: **{soxl_plan['state']}** · "
            f"C위험점수 {soxl_plan.get('risk_score', '-')}/10 · "
            f"현재 추정 투입 {exposure_now_live:.1%} · 최대 누적투입 {cap:.0%} · "
            f"기준 확정종가 {currency}{prev_close_live:,.2f}"
        )
        if soxl_plan.get("risk_flags"):
            st.caption("C타입 위험요인: " + " · ".join(soxl_plan["risk_flags"]))

        b1, b2 = st.columns(2)
        for idx, (col, price, off, w) in enumerate(zip((b1, b2), soxl_prices, soxl_offsets, shown_weights), start=1):
            with col:
                st.metric(f"오늘 매수 LOC {idx}", f"{currency}{price:,.2f}")
                st.caption(
                    f"전일종가 대비 {off:+.0%} · 총자산 {w:.2%} "
                    f"({currency}{float(investment)*w:,.0f})"
                )

        s1, s2 = st.columns(2)
        for idx, (col, sell_px, w) in enumerate(zip((s1, s2), soxl_sell_prices, shown_weights), start=1):
            with col:
                st.metric(f"오늘 매도 LOC {idx}", f"{currency}{sell_px:,.2f}")
                st.caption(
                    f"대응 매수 LOC {idx} 기준 +{live_best_tp:.0%} · "
                    f"기준 비중 {w:.2%}"
                )

        today_order_rows = []
        for idx, (price, off, w) in enumerate(zip(soxl_prices, soxl_offsets, shown_weights), start=1):
            today_order_rows.append({
                "구분": f"매수 {idx}",
                "주문방식": "LOC",
                "가격": f"{currency}{price:,.2f}",
                "총자산 비중": f"{w:.2%}",
                "주문금액": f"{currency}{float(investment)*w:,.0f}",
            })
        for idx, (sell_px, w) in enumerate(zip(soxl_sell_prices, shown_weights), start=1):
            today_order_rows.append({
                "구분": f"매도 {idx}",
                "주문방식": "LOC",
                "가격": f"{currency}{sell_px:,.2f}",
                "총자산 비중": f"{w:.2%}",
                "주문금액": "보유 LOT 기준",
            })
        st.dataframe(pd.DataFrame(today_order_rows), use_container_width=True, hide_index=True)

        st.write(
            f"**오늘 제시 매수비중 합계:** 총자산 {sum(shown_weights):.2%} "
            f"({currency}{float(investment)*sum(shown_weights):,.0f})"
        )
        if sum(shown_weights) <= 1e-12:
            st.warning(
                "총투입 한도 때문에 신규 매수 가능비중은 0%입니다. "
                "가격 기준값은 계속 표시하지만 신규 매수 주문금액은 0입니다."
            )

        if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
            approx_sell = float(avg_buy_price) * (1 + live_best_tp)
            st.metric("현재 보유분 평균단가 기준 매도 참고값", f"{currency}{approx_sell:,.2f}")
            st.caption(
                "실제 백테스트 엔진은 각 매수 블록의 실제 체결가에 익절률을 적용해 독립 청산합니다. "
                "위 매도 LOC 1·2는 매일 주문표를 만들기 위한 신규 블록 기준값입니다."
            )

        st.success(
            "✅ SOXL은 매 거래일 **매수 LOC 2개 + 매도 LOC 2개**를 항상 계산합니다. "
            "가격에 닿지 않으면 미체결될 뿐, 앱이 별도로 '대기' 신호로 주문값을 숨기지 않습니다."
        )

    elif market.startswith("🇺🇸"):
        # 매일 실제 주문에 바로 쓸 수 있도록 '가격'을 하나로 정리합니다.
        # 매수는 전략의 낙폭 신호가격과 LOC 한도가격을 모두 만족해야 하므로 더 낮은 값을 사용합니다.
        loc_effective_buy = None
        if loc_buy_limit_today is not None and next_stage <= tranche_count:
            loc_effective_buy = float(loc_buy_limit_today)

        # 매도는 보유 중일 때 평균단가 × 익절목표에 LOC 여유를 반영합니다.
        if loc_sell_limit_today is None and live_stage > 0 and avg_buy_price and avg_buy_price > 0:
            base_sell_target = float(avg_buy_price) * (1 + live_best_tp)
            loc_sell_limit_today = base_sell_target * (1 + live_best_loc_sell_offset)

        close_order_pct = current_weight_pct * (1 - live_best_loc_buy_ratio)
        loc_order_pct = current_weight_pct * live_best_loc_buy_ratio
        close_order_amount = tranche_budget * (1 - live_best_loc_buy_ratio)
        loc_order_amount = tranche_budget * live_best_loc_buy_ratio

        st.markdown("### 📌 다음 거래일 종가 + LOC 주문금액")

        c_buy, c_sell = st.columns(2)
        with c_buy:
            st.metric(
                f"{next_stage if live_stage > 0 else 1}차 일반 종가 매수",
                f"{currency}{close_order_amount:,.0f}",
            )
            st.caption(f"전체 투자금의 {close_order_pct:.2%}")

        with c_sell:
            if loc_effective_buy is not None and loc_order_amount > 0:
                st.metric(
                    f"{next_stage if live_stage > 0 else 1}차 LOC 매수가",
                    f"{currency}{loc_effective_buy:,.2f} 이하",
                )
                st.caption(
                    f"전체 투자금의 {loc_order_pct:.2%} 매수 "
                    f"({currency}{loc_order_amount:,.0f}) · "
                    f"전략 신호가와 LOC 한도 중 더 낮은 가격"
                )
            else:
                st.metric("LOC 매수가", "-")
                st.caption("이번 1위 전략의 LOC 배정금액이 없거나 가격을 계산할 수 없습니다.")

        if live_best_execution_mode == "LOC 모드":
            if loc_sell_limit_today is not None:
                st.metric(
                    "LOC 전량매도가",
                    f"{currency}{loc_sell_limit_today:,.2f} 이상",
                )
                st.caption(
                    f"보유수량의 100% 매도 · "
                    f"평균단가 {currency}{avg_buy_price:,.2f} × "
                    f"익절 {live_best_tp:.0%}"
                )
            else:
                st.metric("LOC 전량매도가", "-")
                st.caption("현재 보유수량이 없어 매도가가 없습니다.")

        if not filter_ok and next_stage <= tranche_count:
            st.warning(
                "오늘은 가격이 매수가에 도달하더라도 진입 필터가 통과되지 않아 "
                "매수 주문을 내지 않는 날입니다: " + ", ".join(filter_reasons)
            )
        elif next_stage <= tranche_count and signal.endswith("매수"):
            st.success(
                f"🟢 **다음 거래일 매수 주문:** 종가 {currency}{close_order_amount:,.0f} "
                f"({close_order_pct:.2%}) + LOC {currency}{loc_order_amount:,.0f} "
                f"({loc_order_pct:.2%})"
            )

        if live_best_execution_mode == "LOC 모드" and loc_sell_limit_today is not None:
            if live_best_max_hold is not None and live_first_buy_date is not None:
                holding_days_now = (
                    pd.Timestamp(live_best_df.index[-1]) - live_first_buy_date
                ).days
            else:
                holding_days_now = 0

            if live_best_max_hold is not None and holding_days_now >= live_best_max_hold:
                st.warning(
                    f"🔴 **기간청산:** 최대 보유기간 {live_best_max_hold}일에 도달했습니다. "
                    "가격 목표와 관계없이 전량매도 대상입니다."
                )
            else:
                st.success(
                    f"🔴 **다음 거래일 매도 주문:** {us_product} LOC 전량매도 "
                    f"{currency}{loc_sell_limit_today:,.2f} 이상 / "
                    "보유수량의 **100% 매도**"
                )

        st.caption(
            "매수·매도 주문을 동시에 낼 수 있는지는 증권사 주문가능금액/수량 및 주문 방식에 따라 다릅니다. "
            "LOC는 마감 경매에서 한도가격 조건을 만족해야 체결되며, 표시 가격에 반드시 체결되는 것은 아닙니다."
        )
    if not (market.startswith("🇺🇸") and us_product == "SOXL"):
        allocation_view = pd.DataFrame(
            {
                "차수": [f"{i}차" for i in range(1, tranche_count + 1)],
                "총자금 대비 매수비중": [
                    f"{w * live_best_deployment_ratio:.2%}" for w in live_tranche_weights
                ],
                "예정금액": [f"{currency}{x:,.0f}" for x in live_tranche_budgets],
                "종가금액": [f"{currency}{x * (1-live_best_loc_buy_ratio):,.0f}" for x in live_tranche_budgets],
                "LOC금액": [f"{currency}{x * live_best_loc_buy_ratio:,.0f}" for x in live_tranche_budgets],
            }
        )
        st.write("**전체 투자금 기준 분할매수 계획**")
        st.caption(
            f"총자금 {live_best_deployment_ratio:.0%} 운용 · 종가 {1-live_best_loc_buy_ratio:.0%} + "
            f"LOC {live_best_loc_buy_ratio:.0%} · 아래 비중 합계는 총자금의 "
            f"{live_best_deployment_ratio:.0%}입니다."
        )
        st.dataframe(
            allocation_view,
            use_container_width=True,
            hide_index=True,
            height=min(38 * (tranche_count + 1), 500),
        )
        st.write(
            f"**{min(next_stage, tranche_count)}차 예정 비중:** {current_weight_pct:.2%} · "
            f"예정 매수금액 {currency}{tranche_budget:,.0f}"
        )

    if market.startswith("🇺🇸") and us_product == "SOXL":
        st.success(
            "✅ 다음 거래일: 위의 **매수 LOC 2개 + 매도 LOC 2개** 기준값을 사용합니다. "
            "미체결은 가격 결과이며 별도의 '대기' 신호로 처리하지 않습니다."
        )
    elif signal.endswith("매수"):
        expected_close_amount = tranche_budget * (1 - live_best_loc_buy_ratio)
        expected_loc_amount = tranche_budget * live_best_loc_buy_ratio
        st.success(
            f"✅ 다음 거래일 할 일: **종가 {currency}{expected_close_amount:,.0f} + "
            f"LOC {currency}{expected_loc_amount:,.0f} 주문** "
            f"(합계 최대 {current_weight_pct:.2%})"
        )
    elif signal in ["익절", "기간청산"]:
        st.success("✅ 다음 거래일 할 일: **현재 보유수량의 100% 매도**")
    else:
        st.info("⏳ 다음 거래일 할 일: **매수하지 않고 대기**")

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
    display["실전점수"] = display["실전점수"].map(lambda x: f"{x:.2%}")
    display["실전기준통과"] = display["실전기준통과"].map(lambda x: "통과" if x else "미달")
    display["총자금 운용률"] = display["총자금 운용률"].map(lambda x: f"{x:.0%}")
    display["평균 투자금 사용률"] = display["평균 투자금 사용률"].map(lambda x: f"{x:.1%}")

    st.dataframe(
        display[
            [
                "운용방식",
                "매수간격",
                "익절률",
                "차수별 매수%",
                "필터",
                "체결방식",
                "총자금 운용률",
                "평균 투자금 사용률",
                "실전기준통과",
                "실전점수",
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

    k7, k8 = st.columns(2)
    k7.metric("평균 투자금 사용률", f"{best['avg_exposure']:.1%}")
    k8.metric("최대 투자금 사용률", f"{best['max_exposure']:.1%}")

    st.line_chart(best["equity"][["Equity"]])

    st.caption(
        f"교육·백테스트용입니다. 편도 수수료 {fee_pct:.2%}, 슬리피지 {slippage_pct:.2%}, "
        f"환전비용 {fx_cost_pct:.2%}를 반영했습니다. 세금·추적오차와 실제 체결 차이는 별도로 발생할 수 있습니다."
    )

except Exception as e:
    st.error("데이터를 불러오거나 백테스트하는 중 오류가 발생했습니다.")
    st.exception(e)
