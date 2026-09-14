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
    page_title="TQQQ / SOXL / 코코레 QUANT V32-C4 BEAR",
    page_icon="📈",
    layout="centered",
)

st.title("📈 TQQQ / SOXL / 코코레 QUANT V32-C4 BEAR")
st.caption("실전 체결관리 · 안전장치 · 기록 복구 · 다음 거래일 주문")
st.caption("V32-C4 BEAR · C4 상승장 로직 유지 · 구조적 장기 약세장에서만 방어 스위치")

AUTO_LOG_PATH = Path("quant_trade_log_autosave.csv")
BACKTEST_CHECKPOINT_DIR = Path(".quant_backtest_checkpoints")
try:
    BACKTEST_CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    BACKTEST_CHECKPOINT_DIR = Path("/tmp/.quant_backtest_checkpoints")
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

market = st.radio(
    "시장",
    ["🇺🇸 미국장 — TQQQ", "🇺🇸 미국장 — SOXL", "🇰🇷 한국장 — 코코레"],
    horizontal=True,
)

us_product = "SOXL" if "SOXL" in market else "TQQQ"

if market.startswith("🇺🇸"):
    currency = "$"
    unit = "달러"
    default_investment = 4000.0
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
    if market.startswith("🇺🇸"):
        us_execution_mode = st.radio(
            "미국장 익절 매도 방식",
            ["일반 종가모드", "LOC 모드"],
            horizontal=True,
            help=(
                "매수는 아래의 종가+LOC 혼합비중을 앱이 자동 비교합니다. "
                "여기서는 익절 매도만 일반 종가 또는 LOC 중 선택합니다."
            ),
        )
        loc_buy_ratios = [0.0, 0.25, 0.50, 0.75, 1.0]
        st.info(
            "매 차수의 예정금액을 종가/LOC로 100:0, 75:25, 50:50, "
            "25:75, 0:100으로 나눠 모두 백테스트하고 가장 좋은 혼합비중을 찾습니다."
        )
        loc_buy_offset = st.select_slider(
            "LOC 매수 한도",
            options=[0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02],
            value=0.005,
            format_func=lambda x: f"전일 종가 대비 -{x:.2%}",
            help="예: -0.50%이면 전일 종가보다 0.5% 낮거나 같은 종가에서만 매수 체결로 봅니다.",
        )
        loc_sell_offset = st.select_slider(
            "LOC 익절 한도 여유",
            options=[0.0, 0.0025, 0.005, 0.0075, 0.01],
            value=0.0,
            format_func=lambda x: f"+{x:.2%}",
            disabled=(us_execution_mode != "LOC 모드"),
            help="0%이면 목표 익절가 이상 종가에서만 매도 체결로 봅니다.",
        )
    else:
        us_execution_mode = "일반 종가모드"
        loc_buy_ratios = [0.0]
        loc_buy_offset = 0.0
        loc_sell_offset = 0.0

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


@st.cache_data(ttl=900, show_spinner=False)
def download_close(symbols):
    """Streamlit Cloud에서 멈춤을 줄이기 위한 안전한 일봉 다운로드.

    여러 종목을 한 번에 받지 않고 하나씩 짧은 timeout으로 받아
    특정 티커 응답 지연이 앱 전체 부팅을 막지 않도록 합니다.
    """
    symbols = list(dict.fromkeys(list(symbols)))
    out = []
    errors = []

    for sym in symbols:
        raw = None
        last_err = None
        # max 우선, 실패하면 10y로 한 번 더 시도
        for period in ("max", "10y"):
            try:
                raw = yf.download(
                    sym,
                    period=period,
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    group_by="column",
                    threads=False,
                    timeout=12,
                )
                if raw is not None and not raw.empty:
                    break
            except TypeError:
                # 구버전 yfinance가 timeout 인자를 지원하지 않는 경우
                try:
                    raw = yf.download(
                        sym,
                        period=period,
                        interval="1d",
                        auto_adjust=True,
                        progress=False,
                        group_by="column",
                        threads=False,
                    )
                    if raw is not None and not raw.empty:
                        break
                except Exception as e:
                    last_err = e
            except Exception as e:
                last_err = e

        if raw is None or raw.empty:
            errors.append(f"{sym}: {last_err or 'no data'}")
            continue

        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if "Close" in raw.columns.get_level_values(0):
                    c = raw["Close"]
                elif "Close" in raw.columns.get_level_values(-1):
                    c = raw.xs("Close", axis=1, level=-1)
                else:
                    raise ValueError("Close column missing")
                if isinstance(c, pd.DataFrame):
                    if sym in c.columns:
                        c = c[sym]
                    else:
                        c = c.iloc[:, 0]
            else:
                if "Close" not in raw.columns:
                    raise ValueError("Close column missing")
                c = raw["Close"]

            c = pd.Series(c, index=raw.index, name=sym).astype(float).dropna()
            # yfinance 버전에 따라 timezone이 붙는 경우를 통일
            try:
                c.index = pd.to_datetime(c.index).tz_localize(None)
            except Exception:
                c.index = pd.to_datetime(c.index)
            out.append(c)
        except Exception as e:
            errors.append(f"{sym}: {e}")

    if not out:
        raise ValueError("가격 데이터를 받지 못했습니다. " + " | ".join(errors))

    close = pd.concat(out, axis=1).sort_index()
    missing = [sym for sym in symbols if sym not in close.columns]
    if missing:
        raise ValueError(
            "필수 가격 데이터 누락: " + ", ".join(missing) +
            (" | " + " | ".join(errors) if errors else "")
        )
    return close


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

    for p in (5, 10, 20):
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
    d["S_VOL20"] = d["SOXL"].pct_change().rolling(20, min_periods=10).std()
    return d



def _attach_soxl_c_precomputed(df):
    """SOXL V32-C 정적 지표/기본 위험점수를 1회만 계산합니다.

    LOT(exposure) 관련 2개 위험 플래그만 시뮬레이션 중 동적으로 더합니다.
    이렇게 하면 각 거래일마다 rolling/RSI를 처음부터 다시 계산하던 O(n^2) 병목을 제거합니다.
    """
    out = df.copy()
    s = out["TRADE"].astype(float)
    q = out["QQQ"].astype(float)
    h = out["SMH"].astype(float)

    for period in (5, 10, 20):
        out[f"C_S_MA{period}"] = s.rolling(period, min_periods=max(3, period // 2)).mean()
        out[f"C_Q_MA{period}"] = q.rolling(period, min_periods=max(3, period // 2)).mean()

    # C4-BEAR: 구조적 약세장 판별용 장기 QQQ 지표. 오늘 주문에는 전일 값만 사용됩니다.
    out["C_Q_MA50"] = q.rolling(50, min_periods=40).mean()
    out["C_Q_MA100"] = q.rolling(100, min_periods=80).mean()
    out["C_Q_MA200"] = q.rolling(200, min_periods=160).mean()
    out["C_Q_MA200_SLOPE20"] = out["C_Q_MA200"].pct_change(20)
    out["C_Q_HIGH252"] = q.rolling(252, min_periods=160).max()
    out["C_Q_DD252"] = q / out["C_Q_HIGH252"] - 1.0
    # 단기 폭락(예: 2020)과 장기 하락(예: 2022)을 구분하기 위해 4개 중 3개 이상일 때만 발동.
    bear_votes = (
        (q < out["C_Q_MA200"]).fillna(False).astype("int16")
        + (out["C_Q_MA50"] < out["C_Q_MA200"]).fillna(False).astype("int16")
        + (out["C_Q_MA200_SLOPE20"] < -0.005).fillna(False).astype("int16")
        + (out["C_Q_DD252"] < -0.18).fillna(False).astype("int16")
    )
    out["C_BEAR_VOTES"] = bear_votes
    out["C_STRUCT_BEAR"] = bear_votes >= 3

    out["C_S_RSI14"] = calc_rsi(s, 14)
    out["C_S_DIST10"] = s / out["C_S_MA10"] - 1
    out["C_S_DIST20"] = s / out["C_S_MA20"] - 1
    out["C_S_DIST10_D3"] = out["C_S_DIST10"].diff(3)
    out["C_S_RSI_D3"] = out["C_S_RSI14"].diff(3)
    out["C_Q_MA20_SLOPE5"] = out["C_Q_MA20"].pct_change(5)
    out["C_SMH_R5"] = h.pct_change(5)
    out["C_QQQ_R5"] = q.pct_change(5)
    out["C_RS5"] = out["C_SMH_R5"] - out["C_QQQ_R5"]

    # LOT를 제외한 8개 정적 위험조건. bool을 int로 바꿔 행별 합산합니다.
    conditions = [
        out["C_Q_MA5"] < out["C_Q_MA10"],
        out["C_Q_MA10"] < out["C_Q_MA20"],
        out["C_Q_MA20_SLOPE5"] < 0,
        out["C_RS5"] < 0,
        s < out["C_S_MA10"],
        s < out["C_S_MA20"],
        out["C_S_DIST10_D3"] < 0,
        out["C_S_RSI_D3"] < 0,
    ]
    risk = pd.Series(0, index=out.index, dtype="int16")
    for cond in conditions:
        risk = risk + cond.fillna(False).astype("int16")
    out["C_STATIC_RISK"] = risk
    return out


def _soxl_c_plan_from_precomputed(prev_row, exposure_now):
    """V32-C4 BEAR: CAGR 최우선. 상승/반등에서는 체결과 재진입을 빠르게, 약세에서만 제한 방어."""
    risk = int(prev_row.get("C_STATIC_RISK", 0))
    exp = float(exposure_now)
    # LOT 패널티를 C3보다 약하게 적용해 상승/반등 구간의 재진입을 막지 않습니다.
    if exp > 0.35:
        risk += 1
    if exp > 0.60:
        risk += 1

    d3 = float(prev_row.get("C_S_DIST10_D3", np.nan))
    rsi_d3 = float(prev_row.get("C_S_RSI_D3", np.nan))
    q_slope = float(prev_row.get("C_Q_MA20_SLOPE5", np.nan))
    rs5 = float(prev_row.get("C_RS5", np.nan))

    rebound = (np.isfinite(d3) and d3 > 0.025 and np.isfinite(rsi_d3) and rsi_d3 > 1.5
               and np.isfinite(q_slope) and q_slope >= -0.006 and np.isfinite(rs5) and rs5 > -0.015)
    strong = (risk <= 3 and np.isfinite(q_slope) and q_slope >= 0 and np.isfinite(rs5) and rs5 >= 0)
    structural_bear = bool(prev_row.get("C_STRUCT_BEAR", False))

    # C4-BEAR의 유일한 매수 변경: 장기 구조적 약세에서만 신규 LOC를 깊고 작게 둡니다.
    # 그 외 모든 구간은 아래 C4 원본 분기를 그대로 사용합니다.
    if structural_bear:
        return "C4-BEAR 구조약세 2+3", (-0.060, -0.120), (0.02, 0.03), max(risk, 7)

    if strong and exp < 0.55:
        return "C4-강공격 9+9", (0.055, 0.000), (0.09, 0.09), risk
    if rebound and exp < 0.55:
        return "C4-반등공격 9+8", (0.040, -0.010), (0.09, 0.08), risk
    if risk <= 4:
        return "C4-공격 8+8", (0.025, -0.020), (0.08, 0.08), risk
    if risk <= 6:
        if exp >= 0.65:
            return "C4-고LOT 4+5", (-0.030, -0.070), (0.04, 0.05), risk
        return "C4-중립 6+7", (-0.010, -0.045), (0.06, 0.07), risk
    if exp >= 0.60:
        return "C4-강약세 3+4", (-0.055, -0.110), (0.03, 0.04), risk
    return "C4-약세 5+6", (-0.035, -0.080), (0.05, 0.06), risk

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

    exp = float(exposure_now)
    rebound = (np.isfinite(r["S_DIST10_D3"]) and r["S_DIST10_D3"] > 0.035 and
               np.isfinite(r["S_RSI_D3"]) and r["S_RSI_D3"] > 3.0 and
               np.isfinite(r["Q_MA20_SLOPE5"]) and r["Q_MA20_SLOPE5"] >= -0.004 and
               np.isfinite(r["RS5"]) and r["RS5"] > -0.01)
    strong = risk <= 2 and np.isfinite(r["Q_MA20_SLOPE5"]) and r["Q_MA20_SLOPE5"] > 0 and np.isfinite(r["RS5"]) and r["RS5"] > 0
    if strong and exp < 0.30:
        state, offsets, weights = "C3-강공격 8+8", (0.05, -0.005), (0.08, 0.08)
    elif rebound and exp < 0.25:
        state, offsets, weights = "C3-반등공격 8+7", (0.035, -0.015), (0.08, 0.07)
    elif risk <= 4:
        state, offsets, weights = "C3-정상 7+7", (0.02, -0.025), (0.07, 0.07)
    elif risk <= 6:
        if exp >= 0.45:
            state, offsets, weights = "C3-LOT방어 3+5", (-0.04, -0.09), (0.03, 0.05)
        else:
            state, offsets, weights = "C3-선택방어 5+7", (-0.025, -0.065), (0.05, 0.07)
    else:
        if exp >= 0.40:
            state, offsets, weights = "C3-강방어 2+4", (-0.065, -0.13), (0.02, 0.04)
        else:
            state, offsets, weights = "C3-약세 4+5", (-0.045, -0.09), (0.04, 0.05)

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
    """SOXL V32-C 백테스트 엔진.

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

    if "QQQ" not in df.columns or "SMH" not in df.columns:
        raise ValueError("V32-C SOXL 백테스트에는 QQQ와 SMH 일봉이 필요합니다.")

    close = df["TRADE"].astype(float).copy()
    qqq = df["QQQ"].astype(float).copy()
    smh = df["SMH"].astype(float).copy()
    for i, dt in enumerate(df.index):
        px = float(close.iloc[i])
        if not np.isfinite(px) or px <= 0:
            continue

        # 1) 기존 블록 청산
        remaining = []
        for lot in lots:
            age = (pd.Timestamp(dt) - pd.Timestamp(lot["date"])).days
            # C4 RETURN 회전 엔진: 고LOT는 빠르게 회수하고, 낮은 LOT/강한 장에서는 목표수익을 충분히 유지합니다.
            mark_before = sum(x["qty"] * px for x in lots)
            eq_before = cash + mark_before
            exp_before = mark_before / eq_before if eq_before > 0 else 0.0
            tp_eff = float(take_profit)
            if exp_before >= 0.75:
                tp_eff = min(tp_eff, 0.025)
            elif exp_before >= 0.60:
                tp_eff = min(tp_eff, 0.035)
            elif exp_before >= 0.45:
                tp_eff = min(tp_eff, 0.045)
            target = lot["price"] * (1 + tp_eff)
            hit_tp = px >= target
            forced = max_hold_days is not None and age >= int(max_hold_days)
            if hit_tp or forced:
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
                    "청산사유": "익절" if hit_tp else "기간청산",
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

            prev_row = df.iloc[i - 1]
            prev = float(close.iloc[i - 1])

            # C4-BEAR: 전일까지 구조적 약세가 확인된 날에만 기존 LOT 노출을 35%까지 축소.
            # 당일 QQQ/SOXL 미래정보는 사용하지 않습니다. 오래된 LOT부터 유지하고 최근 LOT부터 줄입니다.
            structural_bear = bool(prev_row.get("C_STRUCT_BEAR", False))
            bear_cap = min(float(deployment_ratio), 0.35) if structural_bear else float(deployment_ratio)
            if structural_bear and exposure_now > bear_cap + 1e-12 and lots:
                target_value = equity_now * bear_cap
                excess = max(0.0, invested_now - target_value)
                new_lots = []
                # 최근 진입 LOT부터 축소하여 오래 버틴 저가 LOT는 최대한 보존
                for lot in reversed(lots):
                    if excess <= 1e-9:
                        new_lots.append(lot)
                        continue
                    lot_value = lot["qty"] * px
                    sell_value = min(lot_value, excess)
                    sell_qty = sell_value / px if px > 0 else 0.0
                    if sell_qty > 0:
                        sell_px = px * (1 - slippage_pct)
                        frac = min(1.0, sell_qty / lot["qty"])
                        cost_part = lot["cash_cost"] * frac
                        proceeds = sell_qty * sell_px * (1 - fee_pct - fx_cost_pct)
                        cash += proceeds
                        trades.append({
                            "신호일": lot["date"], "매도일": pd.Timestamp(dt),
                            "평균매수가": lot["price"], "매도가": sell_px,
                            "수익률": proceeds / cost_part - 1 if cost_part > 0 else 0.0,
                            "실현손익": proceeds - cost_part,
                            "보유일수": (pd.Timestamp(dt) - pd.Timestamp(lot["date"])).days,
                            "매수횟수": 1, "청산사유": "구조약세축소",
                            "진입상태": lot.get("state", ""),
                        })
                        remain_frac = 1.0 - frac
                        if remain_frac > 1e-9:
                            kept = dict(lot)
                            kept["qty"] = lot["qty"] * remain_frac
                            kept["cash_cost"] = lot["cash_cost"] * remain_frac
                            new_lots.append(kept)
                        excess -= sell_value
                    else:
                        new_lots.append(lot)
                lots = list(reversed(new_lots))
                mark_value = sum(lot["qty"] * px for lot in lots)
                equity_now = cash + mark_value
                invested_now = mark_value
                exposure_now = invested_now / equity_now if equity_now > 0 else 0.0
                max_invested = equity_now * bear_cap
            else:
                max_invested = equity_now * bear_cap

            if "C_STATIC_RISK" in df.columns:
                state, offsets, weights, risk_score = _soxl_c_plan_from_precomputed(prev_row, exposure_now)
            else:
                # 호환 폴백: 사전계산 열이 없는 외부 호출도 정상 동작
                plan = get_soxl_loc_plan(
                    close.iloc[:i],
                    exposure_now=exposure_now,
                    base_unit=base_unit,
                    qqq_series=qqq.iloc[:i],
                    smh_series=smh.iloc[:i],
                )
                state = plan["state"]
                offsets = plan["offsets"]
                weights = plan["weights"]
                risk_score = int(plan.get("risk_score", -1))

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
                    "risk_score": int(risk_score),
                })
                invested_now += qty * px

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
    eq["Drawdown"] = eq["Equity"] / eq["Equity"].cummax() - 1

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


try:
    # ------------------------------------------------------------
    # 데이터 / 비교할 운용 방식
    # ------------------------------------------------------------
    if market.startswith("🇺🇸"):
        symbols = [us_product, "QQQ"] if us_product == "TQQQ" else ["SOXL", "QQQ", "SMH"]
        with st.spinner("가격 데이터를 불러오는 중입니다..."):
            close = download_close(symbols).dropna()

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
        with st.spinner("가격 데이터를 불러오는 중입니다..."):
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
        # V32-C4 BEAR: CAGR을 끌어올리기 위해 회전율/재진입을 우선 탐색합니다.
        # 상태별 매수비중은 C4 템플릿으로 고정하고 익절/보유기간/총투입한도를 비교합니다.
        effective_buy_steps = [0.06]
        effective_take_profits = [0.025, 0.035, 0.045, 0.06, 0.08]
        effective_max_holds = [45, 90, 180]
        filter_candidates = [(None, None)]
        allocation_candidates = [np.ones(int(tranche_count)) / int(tranche_count)]
        loc_buy_ratios = [1.0]
        deployment_ratios = [0.90, 1.00]

    current_params = (
        market,
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
    if market.startswith("🇺🇸") and us_product == "SOXL":
        st.info(
            "🧪 SOXL V32-C4 BEAR 백테스트: SOXL 이평/RSI + 실제 LOT + QQQ 이평 구조 + SMH/QQQ 5일 상대강도로 "
            "4+4 / 5+5 / 6+6 / 7+7 매수비중을 자동 전환합니다. VIX는 제외했습니다. "
            "LOC 가격 격자는 V31과 동일하게 유지해 C타입 상태판단 자체의 효과를 먼저 비교합니다."
        )
    st.subheader("🏆 전략 자동 비교")

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
        exhaustive_limit = 120
        stage1_limit = 36
        stage2_limit = 24

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
            if market.startswith("🇺🇸") and us_product == "SOXL":
                df = _attach_soxl_c_precomputed(df)
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
            if market.startswith("🇺🇸"):
                buy_exec_name = (
                    f"종가 {1-loc_buy_ratio:.0%} + LOC {loc_buy_ratio:.0%}"
                )
                sell_exec_name = "LOC 매도" if us_execution_mode == "LOC 모드" else "종가 매도"
                exec_name = f"{buy_exec_name} / {sell_exec_name}"
            else:
                buy_exec_name = "종가 100%"
                sell_exec_name = "종가 매도"
                exec_name = "종가"
            strategy_name = (
                f"{mode['mode']} | C3 공격/선택방어 자동비중 / {tp:.0%} 블록익절 / "
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
                "CAGR55달성": bool(sim["cagr"] >= 0.55),
            }
            results.append(row)
            scored.append((((1.0 if float(sim["cagr"]) >= 0.55 else 0.0), (float(sim["mdd"]) if float(sim["cagr"]) >= 0.55 else float(sim["cagr"])), float(sim["cagr"]), float(sim["final_value"])), job))
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
                status.caption(f"고속 모드: 핵심 후보 {total_grid:,}개만 계산합니다.")
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
        # SOXL C타입 블라인드 검증도 학습/탐색 구간과 동일하게 QQQ·SMH를 반드시 포함합니다.
        if market.startswith("🇺🇸") and us_product == "SOXL":
            if "QQQ" not in close.columns or "SMH" not in close.columns:
                raise ValueError("V32-C SOXL 블라인드 검증에는 QQQ와 SMH 일봉이 필요합니다.")
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
            if market.startswith("🇺🇸") and us_product == "SOXL":
                # TURBO용 정적 C타입 지표를 블라인드 검증 데이터에도 동일하게 사전계산합니다.
                vdf = _attach_soxl_c_precomputed(vdf)

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

    # ------------------------------------------------------------
    # C4 연도별 성과 분석
    # ------------------------------------------------------------
    if market.startswith("🇺🇸") and us_product == "SOXL":
        eq_year = best.get("equity")
        trades_year = best.get("trades")
        if isinstance(eq_year, pd.DataFrame) and not eq_year.empty and "Equity" in eq_year.columns:
            eq_year = eq_year.copy().sort_index()
            eq_year.index = pd.to_datetime(eq_year.index)
            annual_rows = []
            years = sorted(eq_year.index.year.unique())

            for yr in years:
                ydf = eq_year.loc[eq_year.index.year == int(yr)].copy()
                if ydf.empty:
                    continue

                first_idx = ydf.index[0]
                prev = eq_year.loc[eq_year.index < first_idx]
                start_equity = float(prev["Equity"].iloc[-1]) if not prev.empty else float(ydf["Equity"].iloc[0])
                end_equity = float(ydf["Equity"].iloc[-1])
                year_return = end_equity / start_equity - 1.0 if start_equity > 0 else np.nan

                # 연초 자산을 직전 고점 후보로 포함한 해당 연도 MDD
                dd_base = pd.concat([
                    pd.Series([start_equity], index=[first_idx - pd.Timedelta(days=1)]),
                    ydf["Equity"],
                ])
                year_peak = dd_base.cummax()
                year_mdd = float((dd_base / year_peak - 1.0).min())

                avg_exp = float(ydf["Exposure"].mean()) if "Exposure" in ydf.columns else np.nan
                max_exp = float(ydf["Exposure"].max()) if "Exposure" in ydf.columns else np.nan

                trade_count = 0
                if isinstance(trades_year, pd.DataFrame) and not trades_year.empty:
                    if "매도일" in trades_year.columns:
                        sell_dates = pd.to_datetime(trades_year["매도일"], errors="coerce")
                        trade_count = int((sell_dates.dt.year == int(yr)).sum())

                annual_rows.append({
                    "연도": int(yr),
                    "연초자산": start_equity,
                    "연말/현재자산": end_equity,
                    "연간수익률": float(year_return),
                    "연도 MDD": year_mdd,
                    "평균 투자율": avg_exp,
                    "최대 투자율": max_exp,
                    "완료매매": trade_count,
                })

            if annual_rows:
                annual = pd.DataFrame(annual_rows)
                st.subheader("📅 C4 연도별 성과 분석")
                annual_view = annual.copy()
                annual_view["연초자산"] = annual_view["연초자산"].map(lambda x: f"{currency}{x:,.0f}")
                annual_view["연말/현재자산"] = annual_view["연말/현재자산"].map(lambda x: f"{currency}{x:,.0f}")
                annual_view["연간수익률"] = annual_view["연간수익률"].map(lambda x: f"{x:.1%}")
                annual_view["연도 MDD"] = annual_view["연도 MDD"].map(lambda x: f"{x:.1%}")
                annual_view["평균 투자율"] = annual_view["평균 투자율"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
                annual_view["최대 투자율"] = annual_view["최대 투자율"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
                st.dataframe(annual_view, use_container_width=True, hide_index=True)

                best_y = annual.loc[annual["연간수익률"].idxmax()]
                worst_y = annual.loc[annual["연간수익률"].idxmin()]
                loss_years = annual.loc[annual["연간수익률"] < 0].copy()

                c1, c2, c3 = st.columns(3)
                c1.metric("최고 수익 연도", str(int(best_y["연도"])), f"{best_y['연간수익률']:.1%}")
                c2.metric("최저 수익 연도", str(int(worst_y["연도"])), f"{worst_y['연간수익률']:.1%}")
                c3.metric("손실 연도 수", f"{len(loss_years)}개", f"전체 {len(annual)}개")

                if not loss_years.empty:
                    loss_txt = ", ".join(
                        f"{int(r['연도'])} {r['연간수익률']:.1%}"
                        for _, r in loss_years.sort_values("연간수익률").iterrows()
                    )
                    st.warning(f"📉 CAGR을 깎은 손실 연도: {loss_txt}")
                else:
                    st.success("✅ 이 백테스트 구간에는 연간 기준 손실 연도가 없습니다.")

                # 3년 이동 기하수익률: 특정 몇 년이 전체 CAGR을 깎는지 보조 진단
                if len(annual) >= 3:
                    rolling_rows = []
                    vals = annual["연간수익률"].to_numpy(dtype=float)
                    yrs = annual["연도"].to_numpy(dtype=int)
                    for i in range(2, len(annual)):
                        gross = float(np.prod(1.0 + vals[i-2:i+1]))
                        ann = gross ** (1.0/3.0) - 1.0 if gross > 0 else np.nan
                        rolling_rows.append({
                            "3년구간": f"{yrs[i-2]}~{yrs[i]}",
                            "3년 연환산": ann,
                        })
                    rolling = pd.DataFrame(rolling_rows).sort_values("3년 연환산")
                    st.caption("🔬 가장 약한 3년 구간 (CAGR 병목 확인)")
                    rv = rolling.head(5).copy()
                    rv["3년 연환산"] = rv["3년 연환산"].map(lambda x: f"{x:.1%}")
                    st.dataframe(rv, use_container_width=True, hide_index=True)

    # ------------------------------------------------------------
    # 최대낙폭(MDD) 원인 구간 진단
    # ------------------------------------------------------------
    if market.startswith("🇺🇸") and us_product == "SOXL":
        eq_diag = best.get("equity")
        if isinstance(eq_diag, pd.DataFrame) and not eq_diag.empty and "Equity" in eq_diag.columns:
            eq_diag = eq_diag.copy().sort_index()
            running_peak = eq_diag["Equity"].cummax()
            dd_series = eq_diag["Equity"] / running_peak - 1.0
            trough_date = pd.Timestamp(dd_series.idxmin())
            trough_pos = eq_diag.index.get_loc(trough_date)
            peak_slice = eq_diag.iloc[:trough_pos + 1]["Equity"]
            peak_date = pd.Timestamp(peak_slice.idxmax())
            peak_equity = float(eq_diag.loc[peak_date, "Equity"])
            trough_equity = float(eq_diag.loc[trough_date, "Equity"])
            mdd_value = trough_equity / peak_equity - 1.0 if peak_equity > 0 else np.nan

            # 고점 회복일: 저점 이후 처음으로 이전 고점 자산을 회복한 날
            after_trough = eq_diag.loc[trough_date:]
            recovered = after_trough[after_trough["Equity"] >= peak_equity]
            recovery_date = pd.Timestamp(recovered.index[0]) if not recovered.empty else None

            peak_exp = float(eq_diag.loc[peak_date, "Exposure"]) if "Exposure" in eq_diag.columns else np.nan
            trough_exp = float(eq_diag.loc[trough_date, "Exposure"]) if "Exposure" in eq_diag.columns else np.nan
            peak_px = float(eq_diag.loc[peak_date, "TradePrice"]) if "TradePrice" in eq_diag.columns else np.nan
            trough_px = float(eq_diag.loc[trough_date, "TradePrice"]) if "TradePrice" in eq_diag.columns else np.nan
            px_dd = trough_px / peak_px - 1.0 if np.isfinite(peak_px) and peak_px > 0 else np.nan
            fall_days = int((trough_date - peak_date).days)
            recovery_text = f"{recovery_date:%Y-%m-%d}" if recovery_date is not None else "백테스트 종료까지 미회복"
            recovery_days = int((recovery_date - peak_date).days) if recovery_date is not None else None

            st.subheader("🔎 C4 최대낙폭 구간")
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("직전 자산고점", f"{peak_date:%Y-%m-%d}", f"${peak_equity:,.0f}")
            d2.metric("MDD 저점", f"{trough_date:%Y-%m-%d}", f"${trough_equity:,.0f}")
            d3.metric("최대낙폭", f"{mdd_value:.1%}", f"{fall_days}일")
            d4.metric("SOXL 동기간", f"{px_dd:.1%}" if np.isfinite(px_dd) else "-")

            if recovery_date is not None:
                st.caption(
                    f"고점 회복일: **{recovery_text}** · 고점→회복 {recovery_days}일 · "
                    f"고점 투자율 {peak_exp:.1%} → 저점 투자율 {trough_exp:.1%}"
                )
            else:
                st.caption(
                    f"고점 회복: **{recovery_text}** · "
                    f"고점 투자율 {peak_exp:.1%} → 저점 투자율 {trough_exp:.1%}"
                )

            # 저점 전후 10거래일씩을 표로 보여서 LOT 누적/회복 과정을 바로 확인
            left = max(0, trough_pos - 10)
            right = min(len(eq_diag), trough_pos + 11)
            around = eq_diag.iloc[left:right].copy()
            around["Drawdown"] = around["Equity"] / around["Equity"].cummax().combine_first(running_peak.loc[around.index]) - 1.0
            # 위 계산의 로컬 peak 왜곡 방지를 위해 전체기간 running peak로 다시 계산
            around["Drawdown"] = eq_diag.loc[around.index, "Equity"] / running_peak.loc[around.index] - 1.0
            cols = [c for c in ["Equity", "Drawdown", "Exposure", "TradePrice", "Cash"] if c in around.columns]
            around_view = around[cols].copy()
            around_view.index = pd.to_datetime(around_view.index).strftime("%Y-%m-%d")
            if "Equity" in around_view.columns:
                around_view["Equity"] = around_view["Equity"].map(lambda x: f"${x:,.0f}")
            if "Cash" in around_view.columns:
                around_view["Cash"] = around_view["Cash"].map(lambda x: f"${x:,.0f}")
            if "Drawdown" in around_view.columns:
                around_view["Drawdown"] = around_view["Drawdown"].map(lambda x: f"{x:.1%}")
            if "Exposure" in around_view.columns:
                around_view["Exposure"] = around_view["Exposure"].map(lambda x: f"{x:.1%}")
            if "TradePrice" in around_view.columns:
                around_view["TradePrice"] = around_view["TradePrice"].map(lambda x: f"${x:,.2f}")
            st.dataframe(around_view, use_container_width=True)
            st.caption("↑ MDD 저점 전후 10거래일. 투자율이 급격히 올라가면서 낙폭이 커지는지 확인하는 진단표입니다.")

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

    if market.startswith("🇺🇸"):
        st.subheader("🔀 종가 + LOC 매수금액 비교")
        mix_summary = (
            result.sort_values(["최종자산", "CAGR"], ascending=False)
            .groupby("매수체결혼합", as_index=False)
            .first()
            .sort_values(["최종자산", "CAGR"], ascending=False)
        )
        mix_view = mix_summary[
            ["매수체결혼합", "최종자산", "CAGR", "MDD", "최대보유일", "완료매매"]
        ].copy()
        mix_view["최종자산"] = mix_view["최종자산"].map(lambda x: f"{currency}{x:,.0f}")
        mix_view["CAGR"] = mix_view["CAGR"].map(lambda x: f"{x:.1%}")
        mix_view["MDD"] = mix_view["MDD"].map(lambda x: f"{x:.1%}")
        mix_view["최대보유일"] = mix_view["최대보유일"].map(lambda x: f"{x:.0f}일")
        st.dataframe(mix_view, use_container_width=True, hide_index=True)

        best_mix_row = mix_summary.iloc[0]
        st.success(
            f"🏆 매수 혼합 1위: **{best_mix_row['매수체결혼합']}** · "
            f"최종자산 {currency}{best_mix_row['최종자산']:,.0f} · "
            f"CAGR {best_mix_row['CAGR']:.1%} · MDD {best_mix_row['MDD']:.1%}"
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

        st.markdown("### 📌 SOXL 다음 거래일 LOC 주문 — 매일 매수·매도 동시 제시")
        st.caption(
            f"상태: **{soxl_plan['state']}** · C위험점수 {soxl_plan.get('risk_score', '-')}/10 · "
            f"현재 추정 투입 {exposure_now_live:.1%} · 최대 누적투입 {cap:.0%} · "
            f"전일종가 {currency}{prev_close_live:,.2f} · 조건이 약한 날도 주문 기준값을 표시합니다."
        )
        if soxl_plan.get("risk_flags"):
            st.caption("C타입 위험요인: " + " · ".join(soxl_plan["risk_flags"]))

        b1, b2 = st.columns(2)
        for idx, (col, price, off, w) in enumerate(zip((b1, b2), soxl_prices, soxl_offsets, shown_weights), start=1):
            with col:
                st.metric(f"매수 LOC {idx}", f"{currency}{price:,.2f}")
                st.caption(
                    f"전일종가 대비 {off:+.0%} · 총자산 {w:.2%} "
                    f"({currency}{float(investment)*w:,.0f})"
                )

        s1, s2 = st.columns(2)
        for idx, (col, sell_px, w) in enumerate(zip((s1, s2), soxl_sell_prices, shown_weights), start=1):
            with col:
                st.metric(f"매도 LOC {idx}", f"{currency}{sell_px:,.2f}")
                st.caption(
                    f"대응 매수 LOC {idx} 기준 +{live_best_tp:.0%} · "
                    f"기준 비중 {w:.2%}"
                )

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
