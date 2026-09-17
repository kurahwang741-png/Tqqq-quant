import gzip, json, pickle, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import FinanceDataReader as fdr

OUT=Path('kr_leader_v23_signals.pkl.gz')
META=Path('kr_leader_v23_meta.json')
CKPT=Path('kr_leader_v23_build_checkpoint.pkl.gz')
START='2015-01-01'; END='2023-12-31'

def normalize(d, source):
    if d is None or d.empty: return pd.DataFrame(columns=['Code','Name','Source'])
    code=next((c for c in ['Code','Symbol'] if c in d.columns),None); name=next((c for c in ['Name','Company'] if c in d.columns),None)
    if not code or not name: return pd.DataFrame(columns=['Code','Name','Source'])
    return pd.DataFrame({'Code':d[code].astype(str).str.zfill(6),'Name':d[name].astype(str),'Source':source}).drop_duplicates('Code')

def universe():
    frames=[normalize(fdr.StockListing('KRX'),'현재상장')]
    try: frames.append(normalize(fdr.StockListing('KRX-DELISTING'),'상장폐지'))
    except Exception as e: print('delisting listing warning:',e)
    u=pd.concat(frames,ignore_index=True).drop_duplicates('Code',keep='first')
    return u[u.Code.str.fullmatch(r'\d{6}',na=False)].reset_index(drop=True)

def price(code):
    d=fdr.DataReader(str(code),START,END)
    if d is None or d.empty: return pd.DataFrame()
    d=d.copy(); d.index=pd.to_datetime(d.index).tz_localize(None)
    need=['Open','High','Low','Close','Volume']
    if any(c not in d.columns for c in need): return pd.DataFrame()
    for c in need: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.dropna(subset=need).sort_index()

def leader_events(d):
    x=d.copy(); x['R20']=x.Close.pct_change(20); x['R40']=x.Close.pct_change(40); x['R60']=x.Close.pct_change(60); x['R1']=x.Close.pct_change()
    x['AmountProxy']=x.Close*x.Volume; x['Amt20']=x.AmountProxy.rolling(20).mean(); x['AmtRatio']=x.AmountProxy/x.Amt20.replace(0,np.nan)
    x['BigUp']=(x.R1>=.12).astype(int); x['BigUp20']=x.BigUp.rolling(20).sum()
    x['LeaderScore']=(x.R20.ge(.25).astype(int)*2+x.R40.ge(.45).astype(int)*2+x.R60.ge(.70).astype(int)*2+x.AmountProxy.ge(50_000_000_000).astype(int)+x.AmountProxy.ge(150_000_000_000).astype(int)+x.AmtRatio.ge(2).astype(int)+x.BigUp20.ge(2).astype(int))
    return x

def scan(code,name,source,d):
    if len(d)<180:return []
    x=leader_events(d); dev=x[(x.index.year>=2016)&(x.index.year<=2023)]
    if dev.empty or float(dev.LeaderScore.max())<6:return []
    rows=[]; last=None
    for i in range(120,len(x)-21):
        dt=x.index[i]
        if not 2016<=dt.year<=2023:continue
        hist=x.iloc[max(0,i-120):i+1]; leaders=hist[hist.LeaderScore>=6]
        if leaders.empty:continue
        lead_dt=leaders.LeaderScore.idxmax(); after=x.loc[lead_dt:dt]; peak=float(after.High.max()); close=float(x.Close.iloc[i]); dd=close/peak-1 if peak>0 else np.nan
        if not np.isfinite(dd) or not(-.65<=dd<=-.25):continue
        if last is not None and (dt-last).days<15:continue
        entry=float(x.Open.iloc[i+1]);
        if entry<=0:continue
        resistance=float(x.High.iloc[max(0,i-60):i+1].max()); upside=resistance/entry-1
        def rr(n): return float(x.Close.iloc[i+n]/entry-1) if i+n<len(x) else np.nan
        rows.append({'Code':code,'Name':name,'Source':source,'SignalDate':dt,'LeaderScore':float(leaders.LeaderScore.max()),'Drawdown':dd,'Entry':entry,'Resistance':resistance,'UpsideToResistance':upside,'R5':rr(5),'R10':rr(10),'R20':rr(20)})
        last=dt
    return rows

def save(path,obj):
    tmp=path.with_suffix(path.suffix+'.tmp')
    with gzip.open(tmp,'wb',compresslevel=3) as f: pickle.dump(obj,f,pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)

def load_ckpt():
    try:
        with gzip.open(CKPT,'rb') as f:return pickle.load(f)
    except Exception:return {'done':{},'rows':[]}

def main():
    u=universe(); ck=load_ckpt(); done=ck.get('done',{}); rows=ck.get('rows',[]); t0=time.time(); n0=len(done)
    print(f'universe={len(u):,}, resume={n0:,}')
    for j,r in u.iterrows():
        code=str(r.Code)
        if code in done:continue
        try:
            d=price(code); add=scan(code,str(r.Name),str(r.Source),d) if not d.empty else []
            rows.extend(add); done[code]=len(add)
        except Exception as e: done[code]=f'ERR:{type(e).__name__}'
        n=len(done)
        if n%25==0:
            save(CKPT,{'done':done,'rows':rows})
            elapsed=time.time()-t0; rate=(n-n0)/elapsed if elapsed else 0; eta=(len(u)-n)/rate/60 if rate else 0
            print(f'{n:,}/{len(u):,} signals={len(rows):,} rate={rate:.2f}/s eta={eta:.1f}m',flush=True)
    sig=pd.DataFrame(rows); save(OUT,sig); save(CKPT,{'done':done,'rows':rows})
    META.write_text(json.dumps({'built_at':datetime.now(timezone.utc).isoformat(),'universe_count':len(u),'signal_count':len(sig),'development_period':'2016-2023','blind_period':'2024-2026'},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'DONE signals={len(sig):,}')
if __name__=='__main__': main()
