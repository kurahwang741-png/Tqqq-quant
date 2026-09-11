import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
from io import StringIO

st.set_page_config(
    page_title="TQQQ / SOXL / 코코레 QUANT V26",
    page_icon="📈",
    layout="centered",
)

st.title("📈 TQQQ / SOXL / 코코레 QUANT V26")
st.caption("일봉 백테스트 · 오늘 현재가 프리/정규/애프터 최신 시세 + 가중비중 자동비교 + TQQQ LOC")

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

st.info(
    "백테스트와 전략 신호 계산은 일봉 기준입니다. "
    "오늘 화면의 실제 거래 종목 현재가만 프리마켓/정규장/애프터마켓 최신 시세를 별도로 사용합니다."
)

with st.expander("⚙️ 백테스트 설정", expanded=False):
    if market.startswith("🇺🇸"):
        us_execution_mode = st.radio(
            "미국장 체결 방식",
            ["일반 종가모드", "LOC 모드"],
            horizontal=True,
            help=(
                "일반 종가모드는 신호 발생일 종가 체결 가정입니다. "
                "LOC 모드는 직전 거래일 종가 기준으로 LOC 한도가격을 정하고, "
                "당일 종가가 한도가격 조건을 만족할 때만 체결된 것으로 계산합니다."
            ),
        )
        loc_buy_offset = st.select_slider(
            "LOC 매수 한도",
            options=[0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02],
            value=0.005,
            format_func=lambda x: f"전일 종가 대비 -{x:.2%}",
            disabled=(us_execution_mode != "LOC 모드"),
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

    tranche_count = 10
    st.caption(
        "📌 최대 진입단계는 10단계로 고정하고, 분할횟수 자체보다 "
        "각 단계의 자금배분 비율과 총자금 투입상한을 자동 최적화합니다."
    )

    weight_modes = st.multiselect(
        "단계별 자금배분 패턴 자동 비교",
        ["균등", "완만한 후반가중", "강한 후반가중", "초반집중", "바벨형"],
        default=["균등", "완만한 후반가중", "강한 후반가중", "초반집중", "바벨형"],
        help=(
            "같은 10단계라도 1차~10차에 배정하는 비율을 다르게 비교합니다. "
            "후반가중은 큰 하락에서 더 많은 현금을 사용하고, 초반집중은 초기 하락에 더 적극적입니다."
        ),
    )

    deploy_pct_candidates = st.multiselect(
        "총자금 최대 투입비율",
        [0.60, 0.70, 0.80, 0.90, 1.00],
        default=[0.70, 0.80, 0.90, 1.00],
        format_func=lambda x: f"{x:.0%}",
        help=(
            "10단계가 모두 체결되어도 총자금의 몇 %까지만 사용할지 비교합니다. "
            "예: 80% 선택 전략이면 나머지 20%는 현금으로 남깁니다."
        ),
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

    sell_mask = df["구분"].astype(str).str.contains("매도", na=False)
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
def download_close(symbols):
    raw = yf.download(
        symbols,
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

    if isinstance(close, pd.Series):
        close = close.to_frame()

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


def make_tranche_budgets(initial_cash, n_tranches, weight_mode, deploy_pct=1.0):
    """10개 진입단계에 총자금의 deploy_pct만 배분합니다."""
    n = int(n_tranches)

    if weight_mode in ("완만한 후반가중", "약한 가중"):
        weights = np.linspace(1.0, 2.0, n)
    elif weight_mode in ("강한 후반가중", "강한 가중"):
        weights = np.linspace(1.0, 4.0, n)
    elif weight_mode == "초반집중":
        weights = np.linspace(2.5, 1.0, n)
    elif weight_mode == "바벨형":
        x = np.linspace(-1.0, 1.0, n)
        weights = 1.0 + 1.5 * (x ** 2)
    else:
        weights = np.ones(n)

    weights = weights / weights.sum()
    deploy_cash = float(initial_cash) * float(deploy_pct)
    return (deploy_cash * weights).tolist()


def simulate(
    df,
    initial_cash,
    step_pct,
    take_profit,
    n_tranches,
    ma_period,
    rsi_max,
    weight_mode="균등",
    deploy_pct=1.0,
    max_hold_days=None,
    execution_mode="일반 종가모드",
    loc_buy_offset=0.0,
    loc_sell_offset=0.0,
):
    cash = float(initial_cash)
    shares = 0.0
    total_cost = 0.0
    entries = []
    trades = []
    equity_rows = []
    next_level = 1
    tranche_budgets = make_tranche_budgets(
        initial_cash, n_tranches, weight_mode, deploy_pct
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

        if shares > 0:
            avg_price = total_cost / shares
            pnl_pct = trade_price / avg_price - 1
            first_buy_date = entries[0][0] if entries else dt
            holding_days = (dt - pd.Timestamp(first_buy_date)).days

            exit_reason = None

            target_sell_price = avg_price * (1 + take_profit)
            sell_fill_ok = True
            if execution_mode == "LOC 모드":
                loc_sell_limit = target_sell_price * (1 + loc_sell_offset)
                sell_fill_ok = trade_price >= loc_sell_limit

            if pnl_pct >= take_profit and sell_fill_ok:
                exit_reason = "익절"
            elif max_hold_days is not None and holding_days >= max_hold_days:
                exit_reason = "기간청산"

            if exit_reason is not None:
                proceeds = shares * trade_price
                realized = proceeds - total_cost
                cash += proceeds

                trades.append(
                    {
                        "매도일": dt,
                        "평균매수가": avg_price,
                        "매도가": trade_price,
                        "수익률": pnl_pct,
                        "실현손익": realized,
                        "보유일수": holding_days,
                        "매수횟수": len(entries),
                        "청산사유": exit_reason,
                    }
                )

                shares = 0.0
                total_cost = 0.0
                entries = []
                next_level = 1
                exited_today = True

        allowed = True
        if ma_period is not None and not trend_arr[i]:
            allowed = False
        if rsi_max is not None and (not np.isfinite(rsi_arr[i]) or rsi_arr[i] > rsi_max):
            allowed = False

        buy_fill_ok = True
        if execution_mode == "LOC 모드":
            prev_trade_price = prev_trade_arr[i]
            if not np.isfinite(prev_trade_price) or prev_trade_price <= 0:
                buy_fill_ok = False
            else:
                loc_buy_limit = prev_trade_price * (1 - loc_buy_offset)
                buy_fill_ok = trade_price <= loc_buy_limit

        if allowed and not exited_today and buy_fill_ok:
            while next_level <= n_tranches and drawdown >= step_pct * next_level:
                planned_budget = tranche_budgets[next_level - 1]
                budget = min(planned_budget, cash)
                if budget <= 0:
                    break

                qty = budget / trade_price
                shares += qty
                total_cost += budget
                cash -= budget
                entries.append((dt, trade_price, budget, next_level))
                next_level += 1

        equity = cash + shares * trade_price
        equity_rows.append(
            (dt, equity, cash, shares, signal_price, trade_price, drawdown)
        )

    if not equity_rows:
        raise ValueError("백테스트에 사용할 데이터가 없습니다.")

    eq = pd.DataFrame(
        equity_rows,
        columns=[
            "Date", "Equity", "Cash", "Shares",
            "SignalPrice", "TradePrice", "Drawdown",
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

    return {
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "mdd": mdd,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_hold": avg_hold,
        "max_hold": max_hold,
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
        "weight_mode": weight_mode,
        "deploy_pct": float(deploy_pct),
    }


try:
    # ------------------------------------------------------------
    # 데이터 / 비교할 운용 방식
    # ------------------------------------------------------------
    if market.startswith("🇺🇸"):
        symbols = [us_product, ("QQQ" if us_product == "TQQQ" else "SOXX")]
        close = download_close(symbols).dropna()

        modes = [
            {
                 "mode": f"{us_product} 신호 → {us_product} 매수",
                "signal_symbol": us_product,
                "trade_symbol": us_product,
                "ref_symbol": ("QQQ" if us_product == "TQQQ" else "SOXX"),
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

    current_params = (
        market,
        float(investment),
        anchor_mode,
        tuple(effective_buy_steps),
        tuple(effective_take_profits),
        tuple(effective_max_holds),
        tranche_count,
        tuple(weight_modes),
        tuple(deploy_pct_candidates),
        tuple(ma_periods),
        backtest_period_mode,
        selected_start_year,
        selected_end_year,
        selected_start_date,
        selected_end_date,
        use_rsi_candidates,
        tuple(rsi_thresholds),
        short_mode,
        us_execution_mode,
        float(loc_buy_offset),
        float(loc_sell_offset),
    )

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
        or not weight_modes
        or not deploy_pct_candidates
    ):
        st.warning("매수 간격, 익절률, 최대 보유기간, 자금배분 패턴, 총자금 투입비율을 각각 하나 이상 선택해 주세요.")
        st.stop()

    st.divider()
    st.subheader("🏆 전략 자동 비교")

    if run_backtest:
        results = []
        sims = {}

        total_jobs = (
            len(modes)
            * len(effective_buy_steps)
            * len(effective_take_profits)
            * len(filter_candidates)
            * len(effective_max_holds)
            * len(weight_modes)
            * len(deploy_pct_candidates)
        )
        done_jobs = 0

        progress = st.progress(0)
        status = st.empty()

        with st.spinner("백테스트 계산 중..."):
            for mode in modes:
                df = pd.DataFrame(index=close.index)
                df["SIGNAL"] = close[mode["signal_symbol"]]
                df["TRADE"] = close[mode["trade_symbol"]]
                df["REF"] = close[mode["ref_symbol"]]
                df = df.dropna()

                df = apply_backtest_period(
                    df,
                    backtest_period_mode,
                    selected_start_year,
                    selected_end_year,
                    selected_start_date,
                    selected_end_date,
                )

                # 선택한 이평선 중 가장 긴 기간만큼의 사전 데이터가 필요합니다.
                # 이평선 필터를 쓰지 않으면 짧은 연도 단독 백테스트도 허용합니다.
                required_ma = max([int(x) for x in ma_periods], default=0)
                min_rows = max(30, required_ma + 5)

                if len(df) < min_rows:
                    if required_ma > 0:
                        raise ValueError(
                            f"선택한 기간이 너무 짧습니다. "
                            f"{required_ma}일 이동평균을 계산하려면 최소 약 {required_ma}거래일 이상의 데이터가 필요합니다."
                        )
                    else:
                        raise ValueError("선택한 백테스트 기간이 너무 짧습니다.")

                df["ANCHOR"] = anchor_series(df["SIGNAL"], anchor_mode)
                df["RSI14"] = calc_rsi(df["REF"], 14)

                # 선택한 이평선만 계산합니다.
                for p in ma_periods:
                    p = int(p)
                    df[f"MA{p}"] = df["REF"].rolling(p).mean()
                    df[f"TREND_OK_{p}"] = df["REF"] > df[f"MA{p}"]

                # RSI 계산에 필요한 초반 NaN만 제거합니다.
                # 선택하지 않은 MA 때문에 2022년 초 데이터가 사라지지 않도록 합니다.
                required_cols = ["SIGNAL", "TRADE", "REF", "ANCHOR", "RSI14"]
                for p in ma_periods:
                    required_cols.append(f"TREND_OK_{int(p)}")
                df = df.dropna(subset=required_cols)

                for step_pct in effective_buy_steps:
                    for tp in effective_take_profits:
                        for ma_period, rsi_max in filter_candidates:
                            for max_hold_days in effective_max_holds:
                                for weight_mode in weight_modes:
                                    for deploy_pct in deploy_pct_candidates:
                                        sim = simulate(
                                            df,
                                            float(investment),
                                            step_pct,
                                            tp,
                                            tranche_count,
                                            ma_period,
                                            rsi_max,
                                            weight_mode,
                                            float(deploy_pct),
                                            max_hold_days,
                                            us_execution_mode,
                                            float(loc_buy_offset),
                                            float(loc_sell_offset),
                                        )

                                        fname = filter_name(ma_period, rsi_max)
                                        hold_name = "제한없음" if max_hold_days is None else f"{max_hold_days}일"
                                        exec_name = (
                                            f"LOC(-{loc_buy_offset:.2%})"
                                            if market.startswith("🇺🇸") and us_execution_mode == "LOC 모드"
                                            else "종가"
                                        )
                                        strategy_name = (
                                            f"{mode['mode']} | "
                                            f"{step_pct:.0%} 간격 / {tp:.0%} 익절 / "
                                            f"{weight_mode} / 최대투입 {float(deploy_pct):.0%} / "
                                            f"최대 {hold_name} / {fname} / {exec_name}"
                                        )

                                        sims[strategy_name] = {
                                            "sim": sim,
                                            "mode": mode,
                                            "df": df,
                                            "step_pct": step_pct,
                                            "tp": tp,
                                            "ma_period": ma_period,
                                            "rsi_max": rsi_max,
                                            "weight_mode": weight_mode,
                                            "deploy_pct": float(deploy_pct),
                                            "max_hold_days": max_hold_days,
                                            "execution_mode": us_execution_mode,
                                            "loc_buy_offset": float(loc_buy_offset),
                                            "loc_sell_offset": float(loc_sell_offset),
                                        }

                                        stage_pcts = [
                                            x / float(investment)
                                            for x in make_tranche_budgets(
                                                float(investment),
                                                tranche_count,
                                                weight_mode,
                                                float(deploy_pct),
                                            )
                                        ]

                                        results.append(
                                            {
                                                "전략": strategy_name,
                                                "운용방식": mode["mode"],
                                                "매수간격": step_pct,
                                                "익절률": tp,
                                                "자금배분": weight_mode,
                                                "최대투입": float(deploy_pct),
                                                "단계비중": " / ".join(f"{x:.1%}" for x in stage_pcts),
                                                "이평선": "없음" if ma_period is None else f"{int(ma_period)}일",
                                                "필터": fname,
                                                "체결방식": exec_name,
                                                "보유제한": hold_name,
                                                "최종자산": sim["final_value"],
                                                "누적수익률": sim["total_return"],
                                                "CAGR": sim["cagr"],
                                                "MDD": sim["mdd"],
                                                "최대보유일": sim["max_hold"],
                                                "기간청산": sim["forced_exit_count"],
                                                "완료매매": sim["trade_count"],
                                                "승률": sim["win_rate"],
                                            }
                                        )

                                        done_jobs += 1
                                        progress.progress(done_jobs / max(total_jobs, 1))
                                    status.caption(f"계산 중... {done_jobs}/{total_jobs}")

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
    best_weight_mode = winner_bundle.get("weight_mode", "균등")
    best_deploy_pct = float(winner_bundle.get("deploy_pct", 1.0))
    best_max_hold = winner_bundle.get("max_hold_days")
    best_execution_mode = winner_bundle.get("execution_mode", "일반 종가모드")
    best_loc_buy_offset = winner_bundle.get("loc_buy_offset", 0.0)
    best_loc_sell_offset = winner_bundle.get("loc_sell_offset", 0.0)

    bt_start = pd.Timestamp(best_df.index.min())
    bt_end = pd.Timestamp(best_df.index.max())
    bt_years = max((bt_end - bt_start).days / 365.25, 0)
    st.caption(
        f"📅 실제 백테스트 사용기간: {bt_start:%Y-%m-%d} ~ {bt_end:%Y-%m-%d} "
        f"(약 {bt_years:.1f}년) · 선택모드: {backtest_period_mode}"
    )

    st.success(f"🥇 최종자산 1위: **{winner['운용방식']}**")

    if market.startswith("🇺🇸"):
        if best_execution_mode == "LOC 모드":
            st.info(
                f"🇺🇸 LOC 적용: 전일 종가 대비 -{best_loc_buy_offset:.2%} 매수 한도 / "
                f"익절 목표가 대비 +{best_loc_sell_offset:.2%} 매도 한도"
            )
        else:
            st.info("🇺🇸 일반 종가 체결 가정")
    st.write(
        f"**{best_step:.0%} 간격 / {best_tp:.0%} 익절 / "
        f"{best_weight_mode} / 최대투입 {best_deploy_pct:.0%} / "
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

    st.subheader("🔄 최근 2개년 워크포워드 검증")
    st.caption(
        "전체 연도를 반복 계산하지 않고 최근 완료연도와 올해만 검증합니다. "
        "각 실전연도 직전 3년·5년·10년 데이터만으로 전략을 선정한 뒤 해당 연도에 고정 적용합니다."
    )

    run_walk_forward = st.button("🔬 최근 2개년 워크포워드 실행", use_container_width=True)

    if run_walk_forward:
        wf_windows = [3, 5, 10]
        current_year = int(pd.DatetimeIndex(close.index).year.max())
        test_years = [current_year - 1, current_year]
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

                    if len(train_df) < 100:
                        continue

                    train_sim = simulate(
                        train_df,
                        10000.0,
                        bundle["step_pct"],
                        bundle["tp"],
                        tranche_count,
                        ma_period,
                        bundle["rsi_max"],
                        bundle["weight_mode"],
                        float(bundle.get("deploy_pct", 1.0)),
                        bundle["max_hold_days"],
                        bundle["execution_mode"],
                        bundle["loc_buy_offset"],
                        bundle["loc_sell_offset"],
                    )
                    candidates.append(
                        (train_sim["final_value"], train_sim["cagr"], strategy_name, bundle, base)
                    )

                if candidates:
                    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                    _, _, chosen_name, chosen_bundle, chosen_base = candidates[0]

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
                            chosen_bundle["weight_mode"],
                            float(chosen_bundle.get("deploy_pct", 1.0)),
                            chosen_bundle["max_hold_days"],
                            chosen_bundle["execution_mode"],
                            chosen_bundle["loc_buy_offset"],
                            chosen_bundle["loc_sell_offset"],
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
        wf_status.caption("최근 2개년 워크포워드 계산 완료")

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
                with st.expander("2025/2026 연도별 선택 전략 보기"):
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
            st.warning("최근 2개년 워크포워드에 사용할 데이터가 충분하지 않습니다.")

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

    st.subheader("⚖️ 비중 방식 비교")
    weight_summary = (
        result.sort_values(["최종자산", "CAGR"], ascending=False)
        .groupby("매수비중", as_index=False)
        .first()
        .sort_values(["최종자산", "CAGR"], ascending=False)
    )

    weight_view = weight_summary[
        ["매수비중", "최종자산", "CAGR", "MDD", "최대보유일", "완료매매"]
    ].copy()
    weight_view["최종자산"] = weight_view["최종자산"].map(
        lambda x: f"{currency}{x:,.0f}"
    )
    weight_view["CAGR"] = weight_view["CAGR"].map(lambda x: f"{x:.1%}")
    weight_view["MDD"] = weight_view["MDD"].map(lambda x: f"{x:.1%}")
    weight_view["최대보유일"] = weight_view["최대보유일"].map(lambda x: f"{x:.0f}일")
    st.dataframe(weight_view, use_container_width=True, hide_index=True)

    best_weight_row = weight_summary.iloc[0]
    st.success(
        f"🏆 비중 방식 1위: **{best_weight_row['매수비중']}** · "
        f"최종자산 {currency}{best_weight_row['최종자산']:,.0f} · "
        f"CAGR {best_weight_row['CAGR']:.1%} · "
        f"MDD {best_weight_row['MDD']:.1%}"
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
        "새 매수부터는 사이클 전략도 CSV에 함께 저장됩니다. Streamlit Cloud 재부팅에 대비해 CSV 백업을 보관해 주세요."
    )

    if "live_trades" not in st.session_state:
        st.session_state.live_trades = []

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
    cycle_strategy_name = None
    cycle_weight_mode = None
    cycle_deploy_pct = None

    if not trade_log.empty:
        for _, rec in trade_log.iterrows():
            rec_target = str(rec.get("종목", actual_trade_target))
            if rec_target != actual_trade_target:
                continue

            side = str(rec.get("구분", ""))
            px = float(rec.get("가격", 0) or 0)
            amount = float(rec.get("금액", 0) or 0)

            if side == "매수" and px > 0 and amount > 0:
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
                        cycle_weight_mode = str(raw_weight)

                    raw_deploy = rec.get("사이클최대투입", None)
                    if pd.notna(raw_deploy) and str(raw_deploy).strip():
                        try:
                            cycle_deploy_pct = float(raw_deploy)
                        except Exception:
                            cycle_deploy_pct = None
                live_qty += amount / px
                live_cost += amount
                live_buys += 1
            elif side == "전량매도":
                live_qty = 0.0
                live_cost = 0.0
                live_buys = 0
                live_first_buy_date = None
                cycle_strategy_name = None
                cycle_weight_mode = None
                cycle_deploy_pct = None

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
    live_best_weight_mode = live_bundle.get("weight_mode", "균등")
    valid_weight_modes = ["균등", "완만한 후반가중", "강한 후반가중", "초반집중", "바벨형",
                          "균등비중", "약한 가중", "강한 가중"]
    if cycle_weight_mode in valid_weight_modes:
        live_best_weight_mode = cycle_weight_mode

    live_best_deploy_pct = float(live_bundle.get("deploy_pct", best_deploy_pct))
    if cycle_deploy_pct is not None and 0 < float(cycle_deploy_pct) <= 1:
        live_best_deploy_pct = float(cycle_deploy_pct)

    live_best_max_hold = live_bundle.get("max_hold_days")
    live_best_execution_mode = live_bundle.get("execution_mode", "일반 종가모드")
    live_best_loc_buy_offset = live_bundle.get("loc_buy_offset", 0.0)
    live_best_loc_sell_offset = live_bundle.get("loc_sell_offset", 0.0)

    log1, log2, log3 = st.columns(3)
    log1.metric("자동 보유 차수", f"{auto_stage}차")
    log2.metric(
        "자동 평균단가",
        f"{currency}{auto_avg_price:,.2f}" if live_qty > 0 else "-",
    )
    log3.metric("투입금액", f"{currency}{live_cost:,.0f}")

    if auto_stage > 0 and cycle_is_locked:
        st.success(
            f"🔒 현재 사이클 전략 고정: **{live_best_weight_mode} / 최대투입 {live_best_deploy_pct:.0%}** · "
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
                    rec["사이클매수비중"] = best_weight_mode
                    rec["사이클최대투입"] = best_deploy_pct
            st.rerun()
    elif auto_stage == 0:
        st.caption(
            f"다음 1차 매수 시 현재 1위 전략 **{best_weight_mode} / 최대투입 {best_deploy_pct:.0%}**을 자동 저장하고, "
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
        record_weight_mode = live_best_weight_mode if auto_stage > 0 else best_weight_mode
        record_deploy_pct = live_best_deploy_pct if auto_stage > 0 else best_deploy_pct
        record_budgets = make_tranche_budgets(
            float(investment), tranche_count, record_weight_mode, record_deploy_pct
        )
        record_next_stage = min(auto_stage + 1, tranche_count)
        default_amount = float(record_budgets[record_next_stage - 1])
        record_amount = st.number_input(
            f"매수금액 ({unit})",
            min_value=0.0,
            value=default_amount,
            step=100.0 if currency == "$" else 10000.0,
            key="record_amount",
        )

        b1, b2 = st.columns(2)

        if b1.button("🟢 매수 기록", use_container_width=True):
            if auto_stage >= tranche_count:
                st.warning("설정한 분할매수 횟수를 이미 모두 사용했습니다.")
            elif record_price <= 0 or record_amount <= 0:
                st.warning("체결가격과 매수금액을 입력해 주세요.")
            else:
                st.session_state.live_trades.append(
                    {
                        "날짜": str(record_date),
                        "종목": actual_trade_target,
                        "구분": "매수",
                        "가격": float(record_price),
                        "금액": float(record_amount),
                        "사이클전략": live_strategy_name if auto_stage > 0 and cycle_is_locked else winner_name,
                        "사이클매수비중": live_best_weight_mode if auto_stage > 0 and cycle_is_locked else best_weight_mode,
                        "사이클최대투입": live_best_deploy_pct if auto_stage > 0 and cycle_is_locked else best_deploy_pct,
                    }
                )
                st.rerun()

        if b2.button("🔴 전량매도 기록", use_container_width=True):
            if live_qty <= 0:
                st.warning("현재 기록상 보유 수량이 없습니다.")
            elif record_price <= 0:
                st.warning("매도 체결가격을 입력해 주세요.")
            else:
                proceeds = live_qty * float(record_price)
                st.session_state.live_trades.append(
                    {
                        "날짜": str(record_date),
                        "종목": actual_trade_target,
                        "구분": "전량매도",
                        "가격": float(record_price),
                        "금액": float(proceeds),
                        "사이클전략": live_strategy_name if auto_stage > 0 else winner_name,
                        "사이클매수비중": live_best_weight_mode if auto_stage > 0 else best_weight_mode,
                        "사이클최대투입": live_best_deploy_pct if auto_stage > 0 else best_deploy_pct,
                    }
                )
                st.rerun()

    trade_log = pd.DataFrame(st.session_state.live_trades)

    if not trade_log.empty:
        st.dataframe(trade_log, use_container_width=True, hide_index=True)

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
            st.rerun()

    # ------------------------------------------------------------
    # 오늘의 실제 매매 신호
    # ------------------------------------------------------------
    st.divider()
    st.subheader("🚦 오늘의 매매 신호")
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
        float(investment), tranche_count, live_best_weight_mode, live_best_deploy_pct
    )
    tranche_budget = live_tranche_budgets[
        min(max(next_stage - 1, 0), tranche_count - 1)
    ]
    tranche_pct = (
        float(tranche_budget) / float(investment) * 100.0
        if float(investment) > 0 else 0.0
    )
    planned_cum_pct = (
        sum(live_tranche_budgets[:min(next_stage, tranche_count)])
        / float(investment) * 100.0
        if float(investment) > 0 else 0.0
    )

    avg_buy_price = auto_avg_price if live_stage > 0 else None

    filter_ok = entry_allowed(latest, live_best_ma_period, live_best_rsi)

    loc_buy_fill_ok = True
    loc_buy_limit_today = None
    if market.startswith("🇺🇸") and live_best_execution_mode == "LOC 모드":
        trade_series = close[actual_trade_symbol].dropna()
        if len(trade_series) >= 2:
            prev_close = float(trade_series.iloc[-2])
            loc_buy_limit_today = prev_close * (1 - live_best_loc_buy_offset)
            loc_buy_fill_ok = trade_price <= loc_buy_limit_today

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

    if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
        target_sell = avg_buy_price * (1 + live_best_tp)

        holding_days_now = (
            (pd.Timestamp(live_best_df.index[-1]) - live_first_buy_date).days
            if live_first_buy_date is not None else 0
        )

        if trade_price >= target_sell:
            signal = "익절"
            detail = (
                f"실제 매수 ETF 현재가가 익절 기준 "
                f"{currency}{target_sell:,.2f} 이상입니다."
            )
        elif live_best_max_hold is not None and holding_days_now >= live_best_max_hold:
            signal = "기간청산"
            detail = (
                f"첫 매수 후 {holding_days_now}일이 지나 "
                f"최대 보유기간 {live_best_max_hold}일에 도달했습니다. "
                "단기회전 규칙에 따라 전량매도 신호입니다."
            )
        elif next_stage <= tranche_count and live_dd >= live_best_step * next_stage:
            if filter_ok and loc_buy_fill_ok:
                signal = f"{next_stage}차 매수"
                detail = "가격 조건과 필터를 모두 통과했습니다."
            elif filter_ok and not loc_buy_fill_ok:
                detail = (
                    f"가격 신호는 충족했지만 LOC 한도 "
                    f"{currency}{loc_buy_limit_today:,.2f} 이하에서만 체결로 봅니다."
                )
            else:
                detail = "가격 조건은 충족했지만 필터 때문에 대기: " + ", ".join(filter_reasons)
        elif next_stage <= tranche_count:
            detail = (
                f"다음 {next_stage}차 신호가격: "
                f"{currency}{next_signal_price:,.2f}"
            )
        else:
            detail = f"분할매수를 모두 사용했습니다. +{live_best_tp:.0%} 익절을 기다립니다."
    else:
        first_signal_price = live_anchor * (1 - live_best_step)

        if live_dd >= live_best_step:
            if filter_ok and loc_buy_fill_ok:
                signal = "1차 매수"
                detail = "오늘 처음 시작 기준 1차 매수 조건과 필터를 통과했습니다."
            elif filter_ok and not loc_buy_fill_ok:
                detail = (
                    f"1차 가격 신호는 충족했지만 LOC 한도 "
                    f"{currency}{loc_buy_limit_today:,.2f} 이하에서만 체결로 봅니다."
                )
            else:
                detail = "가격은 1차 구간이지만 필터 때문에 대기: " + ", ".join(filter_reasons)
        else:
            detail = (
                f"오늘은 대기. 1차 신호가격은 약 "
                f"{currency}{first_signal_price:,.2f}"
            )

    a1, a2, a3 = st.columns(3)
    a1.metric("오늘 신호", signal)
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
        st.write(f"**🔒 사이클 고정 전략:** {live_best_weight_mode} / 최대투입 {live_best_deploy_pct:.0%} · {live_best_step:.0%} 간격 / {live_best_tp:.0%} 익절")
    elif auto_stage == 0:
        st.write(f"**새 사이클 적용 예정:** 현재 1위 · {live_best_weight_mode} / 최대투입 {live_best_deploy_pct:.0%}")
    if market.startswith("🇺🇸") and live_best_execution_mode == "LOC 모드":
        # 매일 실제 주문에 바로 쓸 수 있도록 '가격'을 하나로 정리합니다.
        # 매수는 전략의 낙폭 신호가격과 LOC 한도가격을 모두 만족해야 하므로 더 낮은 값을 사용합니다.
        loc_effective_buy = None
        if loc_buy_limit_today is not None and next_stage <= tranche_count:
            raw_buy_trigger = next_signal_price if live_stage > 0 else first_signal_price
            if raw_buy_trigger is not None:
                loc_effective_buy = min(float(raw_buy_trigger), float(loc_buy_limit_today))

        # 매도는 보유 중일 때 평균단가 × 익절목표에 LOC 여유를 반영합니다.
        loc_sell_limit_today = None
        if live_stage > 0 and avg_buy_price and avg_buy_price > 0:
            base_sell_target = float(avg_buy_price) * (1 + live_best_tp)
            loc_sell_limit_today = base_sell_target * (1 + live_best_loc_sell_offset)

        st.markdown("### 📌 오늘의 LOC 주문가격")

        c_buy, c_sell = st.columns(2)
        with c_buy:
            if loc_effective_buy is not None:
                st.metric(
                    f"{next_stage if live_stage > 0 else 1}차 LOC 매수가",
                    f"{currency}{loc_effective_buy:,.2f} 이하",
                )
                st.caption(
                    f"이번 매수비중 **{tranche_pct:.1f}%** · "
                    f"주문금액 {currency}{tranche_budget:,.0f} · "
                    f"전략 신호가와 LOC 한도 중 더 낮은 가격"
                )
            else:
                st.metric("LOC 매수가", "-")
                st.caption("추가 매수 차수가 없거나 가격을 계산할 수 없습니다.")

        with c_sell:
            if loc_sell_limit_today is not None:
                st.metric(
                    "LOC 전량매도가",
                    f"{currency}{loc_sell_limit_today:,.2f} 이상",
                )
                st.caption(
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
        elif next_stage <= tranche_count and loc_effective_buy is not None:
            st.success(
                f"🟢 **오늘 매수 주문:** {us_product} LOC "
                f"{currency}{loc_effective_buy:,.2f} 이하 / "
                f"총자금의 **{tranche_pct:.1f}%** ({currency}{tranche_budget:,.0f})"
            )

        if loc_sell_limit_today is not None:
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
                    f"🔴 **오늘 매도 주문:** {us_product} LOC 전량매도 "
                    f"{currency}{loc_sell_limit_today:,.2f} 이상"
                )

        st.caption(
            "매수·매도 주문을 동시에 낼 수 있는지는 증권사 주문가능금액/수량 및 주문 방식에 따라 다릅니다. "
            "LOC는 마감 경매에서 한도가격 조건을 만족해야 체결되며, 표시 가격에 반드시 체결되는 것은 아닙니다."
        )
    allocation_text = " → ".join(
        f"{(float(x) / float(investment) * 100.0):.1f}%"
        for x in live_tranche_budgets
    )
    st.write(
        f"**자금배분:** {live_best_weight_mode} · "
        f"**총자금 최대투입:** {live_best_deploy_pct:.0%}"
    )
    st.caption(f"1차→10차 단계별 예정비중: {allocation_text}")
    st.write(
        f"**{min(next_stage, tranche_count)}차 예정:** "
        f"총자금의 **{tranche_pct:.1f}%** "
        f"({currency}{tranche_budget:,.0f}) · "
        f"이 단계까지 계획 누적 {planned_cum_pct:.1f}%"
    )

    if signal.endswith("매수"):
        st.success(f"✅ 오늘 할 일: **{currency}{tranche_budget:,.0f} 매수**")
    elif signal in ["익절", "기간청산"]:
        st.success(
            "✅ 오늘 할 일: **실제 보유 ETF 전량매도**"
            if signal == "기간청산"
            else "✅ 오늘 할 일: **실제 보유 ETF 전량 익절**"
        )
    else:
        st.info("⏳ 오늘 할 일: **매수하지 않고 대기**")

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

    st.dataframe(
        display[
            [
                "운용방식",
                "매수간격",
                "익절률",
                "매수비중",
                "필터",
                "체결방식",
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

    st.line_chart(best["equity"][["Equity"]])

    st.caption(
        "교육·백테스트용입니다. 세금, 수수료, 슬리피지, 환율, "
        "추적오차와 레버리지 ETF의 일일 재조정 효과는 별도 반영하지 않습니다."
    )

except Exception as e:
    st.error("데이터를 불러오거나 백테스트하는 중 오류가 발생했습니다.")
    st.exception(e)
