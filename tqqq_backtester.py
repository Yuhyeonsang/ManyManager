#!/usr/bin/env python3
"""
눈덩이티큐 전략 엔진  ─  TQQQ 분할매수 백테스터
"""

# ══════════════════════════════════════════════════════
#  ★ 사용자 설정  (여기서만 수정)
# ══════════════════════════════════════════════════════
START_DATE      = "2010-02-11"   # 시작일 (TQQQ 상장: 2010-02-11)
END_DATE        = "2026-05-27"   # 종료일
INITIAL_CAPITAL = 10_000_000     # 시작 자금 (원화 ₩)
USD_KRW_RATE    = 1_380          # 달러→원 환율

# 전략 파라미터
LOOKBACK_HIGH   = 126    # 전고점 기준 기간 (거래일)
DIP1_PCT        = -10    # Dip1 진입 조건 (QQQ 고점 대비 %)
DIP2_PCT        = -22    # Dip2 진입 조건
DIP2_MA_MARGIN  = -7     # Dip2 추가 조건 (200MA 대비 %)
DIP1_ALLOC      = 30     # Dip1 목표 비중 %
DIP2_ALLOC      = 70     # Dip2 목표 비중 %
RSI_PERIOD      = 14     # RSI 계산 기간
RSI35_BONUS     = 10     # RSI≤35 추가 비중 %
RSI25_BONUS     = 15     # RSI≤25 추가 비중 %
GC_SHORT        = 5      # 골든크로스 단기 이평 (일)
GC_LONG         = 220    # 골든크로스 장기 이평 (일)
QQQ_MA_LONG     = 200    # QQQ 장기 이평 (Dip2 조건)
TP1_PCT         = 15     # TP1 수익률 기준 %
TP2_PCT         = 100    # TP2 수익률 기준 %
TP3_PCT         = 350    # TP3 수익률 기준 %
TP1_RATIO       = 0.50   # TP1 매도 비율 (초기수량 기준)
TP2_RATIO       = 0.35   # TP2 매도 비율
LOCK_PCT        = -40    # 매수 잠금 (QQQ 고점 대비 %)
LOCK_RELEASE    = -39    # 잠금 해제 기준 %
# ══════════════════════════════════════════════════════

import sys, os

# ── 패키지 자동 설치 ──────────────────────────────────
def _ensure():
    import subprocess
    for p in ["yfinance","pandas","numpy","matplotlib"]:
        try: __import__(p.replace("-","_"))
        except ImportError:
            print(f"  설치 중: {p}")
            subprocess.check_call([sys.executable,"-m","pip","install",p,"-q"])
_ensure()

import warnings; warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import FuncFormatter

# ── ANSI 컬러 ─────────────────────────────────────────
R  = "\033[91m"   # 빨강
G  = "\033[92m"   # 초록
Y  = "\033[93m"   # 노랑
C  = "\033[96m"   # 시안
W  = "\033[97m"   # 흰색
DIM= "\033[2m"    # 흐리게
RST= "\033[0m"    # 리셋

def col(val, pos_col=G, neg_col=R, fmt=None):
    """숫자에 색상 적용"""
    s = fmt(val) if fmt else str(val)
    return (pos_col if val >= 0 else neg_col) + s + RST

def fmt_krw(n):
    return f"{int(n):,}원"

def fmt_pct(n, plus=True):
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"

# ══════════════════════════════════════════════════════
#  1. 출력 모드 선택
# ══════════════════════════════════════════════════════
os.system("")  # Windows ANSI 활성화

print(f"\n{C}눈덩이티큐 전략 엔진 구동 중...{RST}")
print(f"{DIM}1: 거래 내역 + 요약  /  2: 요약만{RST}  →", end=" ", flush=True)
try:
    mode = input().strip()
    if mode not in ("1","2"):
        mode = "2"
except:
    mode = "2"

SHOW_TRADES = (mode == "1")

# ══════════════════════════════════════════════════════
#  2. 데이터 다운로드
# ══════════════════════════════════════════════════════
# 내부 계산은 USD 기준, 표시는 KRW 변환
init_usd = INITIAL_CAPITAL / USD_KRW_RATE

raw_t = yf.download("TQQQ", start=START_DATE, end=END_DATE,
                    auto_adjust=True, progress=False)
raw_q = yf.download("QQQ",  start=START_DATE, end=END_DATE,
                    auto_adjust=True, progress=False)

tqqq = raw_t["Close"].squeeze().dropna()
qqq  = raw_q["Close"].squeeze().dropna()
idx  = tqqq.index.intersection(qqq.index)
tqqq, qqq = tqqq.loc[idx], qqq.loc[idx]

# ══════════════════════════════════════════════════════
#  3. 지표 계산
# ══════════════════════════════════════════════════════
def calc_rsi(s, p=RSI_PERIOD):
    d  = s.diff()
    g  = d.clip(lower=0).ewm(com=p-1, min_periods=p).mean()
    l  = (-d.clip(upper=0)).ewm(com=p-1, min_periods=p).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100/(1+rs)

qqq_high  = qqq.rolling(LOOKBACK_HIGH).max()
qqq_ma200 = qqq.rolling(QQQ_MA_LONG).mean()
qqq_dd    = (qqq / qqq_high - 1) * 100

tqqq_rsi  = calc_rsi(tqqq)
tqqq_ma5  = tqqq.rolling(GC_SHORT).mean()
tqqq_ma220= tqqq.rolling(GC_LONG).mean()

valid = tqqq_ma5.notna() & tqqq_ma220.notna()
gc_sig = valid & (tqqq_ma5 > tqqq_ma220) & (tqqq_ma5.shift(1) <= tqqq_ma220.shift(1))
dc_sig = valid & (tqqq_ma5 < tqqq_ma220) & (tqqq_ma5.shift(1) >= tqqq_ma220.shift(1))

# ══════════════════════════════════════════════════════
#  4. 백테스트 상태 머신
# ══════════════════════════════════════════════════════
cash   = float(init_usd)
shares = 0.0

dip1_done = dip2_done = False
rsi35_done = rsi25_done = False
tp1_done = tp2_done = tp3_done = False
lock_40 = tp3_gc_lock = dip1_after_tp3 = False
entry_cost = init_shares_ref = 0.0

pv_hist    = []   # 포트폴리오 가치 이력
sh_hist    = []   # 보유 수량 이력
trades_log = []   # 거래 기록

def _avg(old_sh, old_c, new_sh, new_p):
    total = old_sh * old_c + new_sh * new_p
    return total / (old_sh + new_sh) if (old_sh + new_sh) > 0 else 0.0

def buy(date, price, dollars, reason):
    global cash, shares, entry_cost, init_shares_ref
    amt = min(dollars, cash)
    if amt < 0.01: return 0.0
    ns = amt / price
    entry_cost = _avg(shares, entry_cost, ns, price)
    shares += ns
    cash   -= amt
    trades_log.append(dict(date=date, side="BUY", price=price,
                           shares=ns, amount=amt, reason=reason,
                           cost_at_trade=entry_cost, win=None))
    return ns

def sell(date, price, sh, reason):
    global cash, shares, entry_cost
    sh = min(sh, shares)
    if sh < 1e-6: return
    proceeds = sh * price
    win = price >= entry_cost
    cash   += proceeds
    shares -= sh
    if shares < 1e-6:
        shares = 0.0
        entry_cost = 0.0
    trades_log.append(dict(date=date, side="SELL", price=price,
                           shares=sh, amount=proceeds, reason=reason,
                           cost_at_trade=entry_cost, win=win))

def reset_cycle():
    global dip1_done, dip2_done, rsi35_done, rsi25_done
    global tp1_done, tp2_done, tp3_done, entry_cost, init_shares_ref
    dip1_done = dip2_done = rsi35_done = rsi25_done = False
    tp1_done  = tp2_done  = tp3_done   = False
    entry_cost = init_shares_ref = 0.0

# ── 메인 루프 ─────────────────────────────────────────
for i in range(len(tqqq)):
    date = tqqq.index[i]
    tp   = float(tqqq.iloc[i])
    qp   = float(qqq.iloc[i])
    dd   = float(qqq_dd.iloc[i])   if not pd.isna(qqq_dd.iloc[i])   else 0.0
    rsi  = float(tqqq_rsi.iloc[i]) if not pd.isna(tqqq_rsi.iloc[i]) else 50.0
    m200 = float(qqq_ma200.iloc[i])if not pd.isna(qqq_ma200.iloc[i])else None
    gc   = bool(gc_sig.iloc[i])
    dc   = bool(dc_sig.iloc[i])

    # -40% 잠금
    if   dd <= LOCK_PCT:    lock_40 = True
    elif dd >= LOCK_RELEASE: lock_40 = False

    # 데드크로스 탈출
    if dc and shares > 0:
        sell(date, tp, shares, "DC_비상탈출")
        reset_cycle()
        tp3_gc_lock = dip1_after_tp3 = False

    # TP 처리 (TP1 → TP2 → TP3 순서 보장)
    if shares > 0 and entry_cost > 0:
        profit = (tp / entry_cost - 1) * 100

        # TP1 → TP2 → TP3 순서 보장 (같은 날 여러 TP 동시 도달 시 순서대로 처리)
        if not tp1_done and profit >= TP1_PCT:
            sell(date, tp, init_shares_ref * TP1_RATIO, f"TP1(+{TP1_PCT}%)")
            tp1_done = True

        if not tp2_done and profit >= TP2_PCT:
            sell(date, tp, init_shares_ref * TP2_RATIO, f"TP2(+{TP2_PCT}%)")
            tp2_done = True

        if not tp3_done and profit >= TP3_PCT:
            sell(date, tp, shares, f"TP3(+{TP3_PCT}%)")
            tp3_done = True
            reset_cycle()
            tp3_gc_lock    = True
            dip1_after_tp3 = False

    # 매수 조건 (잠금 해제 시)
    if not lock_40:
        pf = cash + shares * tp

        # ① 골든크로스 풀매수
        if gc:
            if tp3_gc_lock and not dip1_after_tp3:
                pass
            elif cash > 0:
                if not dip1_done and shares == 0:
                    pass  # 현금100% + GC: Dip1 대기
                else:
                    bought = buy(date, tp, cash, "GC_풀매수")
                    if bought > 0 and init_shares_ref == 0:
                        init_shares_ref = shares

        # ② Dip2
        if (not dip2_done and dd <= DIP2_PCT
                and m200 is not None
                and (qp/m200 - 1)*100 <= DIP2_MA_MARGIN):
            target = pf * DIP2_ALLOC / 100
            amt    = max(0.0, target - shares * tp)
            if amt > 0:
                bought = buy(date, tp, amt, "Dip2(-22%)")
                if bought > 0:
                    dip2_done = True
                    if init_shares_ref == 0:
                        init_shares_ref = shares

        # ③ Dip1
        elif not dip1_done and dd <= DIP1_PCT:
            target = pf * DIP1_ALLOC / 100
            amt    = max(0.0, target - shares * tp)
            if amt > 0:
                bought = buy(date, tp, amt, "Dip1(-10%)")
                if bought > 0:
                    dip1_done = True
                    init_shares_ref = shares
                    if tp3_gc_lock:
                        dip1_after_tp3 = True

        # ④ RSI 보너스
        if dip1_done:
            pf = cash + shares * tp
            if not rsi25_done and rsi <= 25:
                amt = min(pf * RSI25_BONUS / 100, cash)
                if amt > 0:
                    buy(date, tp, amt, "RSI25_보너스")
                    rsi25_done = True
            elif not rsi35_done and rsi <= 35:
                amt = min(pf * RSI35_BONUS / 100, cash)
                if amt > 0:
                    buy(date, tp, amt, "RSI35_보너스")
                    rsi35_done = True

    pv_hist.append(cash + shares * tp)
    sh_hist.append(shares)

# ══════════════════════════════════════════════════════
#  5. 성과 분석
# ══════════════════════════════════════════════════════
pv = pd.Series(pv_hist, index=tqqq.index, dtype=float)
tdf= pd.DataFrame(trades_log)

# 벤치마크
tqqq_bh = init_usd / float(tqqq.iloc[0]) * tqqq
qqq_bh  = init_usd / float(qqq.iloc[0])  * qqq

def cagr(s):
    yrs = len(s)/252
    return ((s.iloc[-1]/init_usd)**(1/yrs) - 1)*100 if yrs > 0 else 0.0

def mdd_full(s):
    return ((s/s.cummax()-1)*100).min()

def sharpe(s):
    r = s.pct_change().dropna()
    v = r.std()*np.sqrt(252)
    return (r.mean()*252)/v if v != 0 else 0.0

# 연도별 통계
years_list = sorted(set(pv.index.year))
year_stats = []
for yr in years_list:
    mask   = pv.index.year == yr
    pv_yr  = pv[mask]
    if len(pv_yr) == 0: continue

    # 연간 수익률
    pv_prev_yr = pv[pv.index.year < yr]
    start_val  = float(pv_prev_yr.iloc[-1]) if len(pv_prev_yr) > 0 else float(init_usd)
    end_val    = float(pv_yr.iloc[-1])
    ann_ret    = (end_val / start_val - 1) * 100

    # 연간 MDD
    ann_mdd = ((pv_yr / pv_yr.cummax() - 1)*100).min()

    # 해당 연도 거래
    if len(tdf) > 0:
        tr_yr  = tdf[pd.DatetimeIndex(tdf["date"]).year == yr]
        sells  = tr_yr[tr_yr.side == "SELL"]
        n_sell = len(sells)
        wins   = sells["win"].sum() if n_sell > 0 else 0
        win_r  = (wins / n_sell * 100) if n_sell > 0 else 0.0
        n_trade= len(tr_yr)
    else:
        n_trade = n_sell = wins = win_r = 0

    year_stats.append(dict(
        year=yr, n_trade=n_trade, n_sell=n_sell,
        ann_ret=ann_ret, ann_mdd=ann_mdd,
        win_rate=win_r, end_val_krw=end_val*USD_KRW_RATE
    ))

# 전체 승률
if len(tdf) > 0:
    all_sells  = tdf[tdf.side == "SELL"]
    total_wins = int(all_sells["win"].sum()) if len(all_sells) > 0 else 0
    total_loss = len(all_sells) - total_wins
    total_wr   = (total_wins/len(all_sells)*100) if len(all_sells) > 0 else 0.0
    total_buys = len(tdf[tdf.side=="BUY"])
    total_trade= len(tdf)
else:
    total_wins=total_loss=total_buys=total_trade=0; total_wr=0.0

avg_mdd = np.mean([s["ann_mdd"] for s in year_stats]) if year_stats else 0.0

# ══════════════════════════════════════════════════════
#  6. 터미널 출력
# ══════════════════════════════════════════════════════
SEP  = "─" * 80
SEP2 = "═" * 80

print(f"\n{C}{SEP}{RST}")
print(f"{C}  연도별 포트폴리오 성과 분석  (매일의 계좌 잔고 기준 MDD){RST}")
print(f"{C}{SEP}{RST}")

print(f"  {'연도':<6}  {'실매매':>6}  {'계좌 수익률':>12}  {'MDD':>10}  {'승률':>7}  {'자산':>18}")
print(f"  {DIM}{'─'*6}  {'─'*6}  {'─'*12}  {'─'*10}  {'─'*7}  {'─'*18}{RST}")

for s in year_stats:
    yr   = s["year"]
    nt   = s["n_trade"]
    nr   = s["ann_ret"]
    nm   = s["ann_mdd"]
    nw   = s["win_rate"]
    ev   = s["end_val_krw"]

    ret_s = col(nr, fmt=fmt_pct)
    mdd_s = col(nm, neg_col=R, pos_col=G, fmt=fmt_pct)
    win_s = col(nw, pos_col=G, neg_col=Y, fmt=lambda x: f"{x:.1f}%")
    val_s = f"{Y}{fmt_krw(ev)}{RST}"

    print(f"  {W}{yr}년{RST}  (실매매 {nt:>2}건)  :  계좌 수익률 {ret_s:>20}  |  MDD {mdd_s:>18}  |  승률 {win_s:>14}  |  자산 {val_s}")

print(f"\n{C}{SEP2}{RST}")
print(f"{C}  눈덩이티큐 (Snowball TQQQ) 전략  최종 자산 요약{RST}")
print(f"{C}{SEP2}{RST}")

final_krw = pv.iloc[-1] * USD_KRW_RATE
tot_ret   = (pv.iloc[-1]/init_usd - 1)*100

print(f"  시작 자금    :  {W}{fmt_krw(INITIAL_CAPITAL)}{RST}")
print(f"  최종 자산    :  {Y}{fmt_krw(final_krw)}{RST}")
print(f"  총 수익률    :  {col(tot_ret, fmt=fmt_pct)}")
print(f"  전체 MDD     :  {col(mdd_full(pv), neg_col=R, pos_col=G, fmt=fmt_pct)}")
print()
print(f"  연평균 수익 (CAGR)  :  {col(cagr(pv), fmt=fmt_pct)}")
print(f"  연평균 평균 MDD     :  {col(avg_mdd, neg_col=R, pos_col=G, fmt=fmt_pct)}")
print(f"  샤프 비율           :  {W}{sharpe(pv):.2f}{RST}")
print()
print(f"  총 거래수  :  {W}{total_trade}건  (TQQQ {total_buys}건 매수 / {len(tdf[tdf.side=='SELL']) if len(tdf)>0 else 0}건 매도){RST}")
print(f"  승률       :  {col(total_wr, fmt=lambda x: f'{x:.1f}%')}  ({total_wins}승 {total_loss}패)  ※ 실질 매매 승률")
print(f"{C}{SEP2}{RST}\n")

# ── 벤치마크 비교표 ──────────────────────────────────
print(f"{C}  벤치마크 비교{RST}")
print(f"  {DIM}{'항목':<16}  {'전략':>14}  {'TQQQ B&H':>14}  {'QQQ B&H':>14}{RST}")
print(f"  {DIM}{'─'*16}  {'─'*14}  {'─'*14}  {'─'*14}{RST}")

bm = {
    "전략":      pv,
    "TQQQ B&H": tqqq_bh,
    "QQQ B&H":  qqq_bh,
}
for label, fn in [
    ("CAGR",      lambda s: f"{cagr(s):+.1f}%"),
    ("MDD",       lambda s: f"{mdd_full(s):+.1f}%"),
    ("샤프",      lambda s: f"{sharpe(s):.2f}"),
    ("최종수익률", lambda s: f"{(s.iloc[-1]/init_usd-1)*100:+.0f}%"),
]:
    row = f"  {label:<16}"
    for s in bm.values():
        row += f"  {fn(s):>14}"
    print(row)
print()

# ── 거래 내역 상세 ──────────────────────────────────
if SHOW_TRADES and len(tdf) > 0:
    print(f"{C}  거래 내역 상세{RST}")
    print(f"  {DIM}{'날짜':<12}  {'구분':>5}  {'가격':>10}  {'수량':>10}  {'금액(₩)':>14}  {'사유':<18}{RST}")
    print(f"  {DIM}{'─'*12}  {'─'*5}  {'─'*10}  {'─'*10}  {'─'*14}  {'─'*18}{RST}")
    for _, r in tdf.iterrows():
        d     = str(r["date"])[:10]
        side  = f"{G}매수{RST}" if r["side"]=="BUY" else f"{R}매도{RST}"
        amt_w = int(r["amount"] * USD_KRW_RATE)
        win_s = f" {G}✓{RST}" if r["win"] is True else (f" {R}✗{RST}" if r["win"] is False else "")
        print(f"  {W}{d}{RST}  {side}  {r['price']:>10.2f}  {r['shares']:>10.2f}  {amt_w:>14,}  {r['reason']:<18}{win_s}")
    print()

# ══════════════════════════════════════════════════════
#  7. 차트 생성
# ══════════════════════════════════════════════════════
print("  차트 생성 중...", end="", flush=True)

BG="#0d1117"; GRID="#21262d"; BLUE="#58a6ff"
ORANGE="#ff7b72"; GREEN="#3fb950"; YELLOW="#d29922"; WHITE="#e6edf3"; GRAY="#8b949e"

fig = plt.figure(figsize=(18,13), facecolor=BG)
gs  = GridSpec(3,2,figure=fig,hspace=0.45,wspace=0.3,
               left=0.07,right=0.97,top=0.92,bottom=0.06)

def style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=GRAY, labelsize=8)
    ax.spines[:].set_color(GRID)
    ax.grid(True,color=GRID,linewidth=0.6,alpha=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_major_locator(mdates.YearLocator(2))

# ① 포트폴리오 (KRW, 로그)
ax1 = fig.add_subplot(gs[0,:])
pv_krw      = pv      * USD_KRW_RATE
tqqq_bh_krw = tqqq_bh * USD_KRW_RATE
qqq_bh_krw  = qqq_bh  * USD_KRW_RATE
ax1.plot(pv_krw.index,      pv_krw/INITIAL_CAPITAL,      color=BLUE,   lw=2.0, label="전략")
ax1.plot(tqqq_bh_krw.index, tqqq_bh_krw/INITIAL_CAPITAL, color=ORANGE, lw=1.0, alpha=0.65, label="TQQQ B&H")
ax1.plot(qqq_bh_krw.index,  qqq_bh_krw/INITIAL_CAPITAL,  color=GREEN,  lw=1.0, alpha=0.65, label="QQQ B&H")
if len(tdf):
    buys = tdf[tdf.side=="BUY"]
    sels = tdf[tdf.side=="SELL"]
    bvals = [pv.loc[d]*USD_KRW_RATE/INITIAL_CAPITAL for d in buys.date if d in pv.index]
    svals = [pv.loc[d]*USD_KRW_RATE/INITIAL_CAPITAL for d in sels.date if d in pv.index]
    ax1.scatter([d for d in buys.date if d in pv.index], bvals, color=GREEN,  s=15, zorder=5, alpha=0.6)
    ax1.scatter([d for d in sels.date if d in pv.index], svals, color=ORANGE, s=15, zorder=5, alpha=0.6, marker="v")
ax1.set_yscale("log")
ax1.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f"{x:.0f}×"))
ax1.set_title("포트폴리오 가치 비교 (로그 스케일)", color=WHITE, fontsize=11, fontweight="bold")
ax1.legend(fontsize=8, facecolor=BG, labelcolor=WHITE)
style(ax1)

# ② MDD
ax2 = fig.add_subplot(gs[1,0])
for s, c, lbl in [(pv,BLUE,"전략"),(tqqq_bh,ORANGE,"TQQQ"),(qqq_bh,GREEN,"QQQ")]:
    dd_s = (s/s.cummax()-1)*100
    ax2.fill_between(dd_s.index,dd_s,0,alpha=0.35,color=c)
    ax2.plot(dd_s.index,dd_s,color=c,lw=0.8,label=lbl)
ax2.set_title("낙폭(Drawdown) 비교", color=WHITE, fontsize=10, fontweight="bold")
ax2.set_ylabel("%", color=GRAY, fontsize=8)
ax2.legend(fontsize=7, facecolor=BG, labelcolor=WHITE)
style(ax2)

# ③ TQQQ 보유 비중
ax3 = fig.add_subplot(gs[1,1])
sh_s    = pd.Series(sh_hist, index=tqqq.index, dtype=float)
alloc   = (sh_s * tqqq / pv * 100).clip(0,100)
ax3.fill_between(alloc.index, alloc, color=BLUE, alpha=0.45)
ax3.plot(alloc.index, alloc, color=BLUE, lw=0.8)
for pct, c, lbl in [(30,YELLOW,"Dip1 30%"),(70,ORANGE,"Dip2 70%"),(100,GREEN,"GC 100%")]:
    ax3.axhline(pct, color=c, lw=0.6, ls="--", alpha=0.7, label=lbl)
ax3.set_ylim(0,110)
ax3.set_title("TQQQ 보유 비중 (%)", color=WHITE, fontsize=10, fontweight="bold")
ax3.legend(fontsize=7, facecolor=BG, labelcolor=WHITE)
style(ax3)

# ④ QQQ & 이동평균
ax4 = fig.add_subplot(gs[2,0])
ax4.plot(qqq.index, qqq, color=GREEN, lw=1.0, label="QQQ")
ax4.plot(qqq_high.index,  qqq_high,  color=YELLOW, lw=0.7, ls="--", alpha=0.6, label=f"{LOOKBACK_HIGH}일 고점")
ax4.plot(qqq_ma200.index, qqq_ma200, color=GRAY,   lw=0.7, ls="--", alpha=0.6, label="QQQ 200MA")
ax4.set_title("QQQ 가격 & 기준선", color=WHITE, fontsize=10, fontweight="bold")
ax4.set_ylabel("$", color=GRAY, fontsize=8)
ax4.legend(fontsize=7, facecolor=BG, labelcolor=WHITE)
style(ax4)

# ⑤ TQQQ RSI
ax5 = fig.add_subplot(gs[2,1])
ax5.plot(tqqq_rsi.index, tqqq_rsi, color=BLUE, lw=0.8)
ax5.axhline(35, color=YELLOW, lw=0.7, ls="--", alpha=0.8, label="RSI 35")
ax5.axhline(25, color=ORANGE, lw=0.7, ls="--", alpha=0.8, label="RSI 25")
ax5.fill_between(tqqq_rsi.index, tqqq_rsi, 35, where=(tqqq_rsi<=35), color=ORANGE, alpha=0.3)
ax5.set_ylim(0,100)
ax5.set_title("TQQQ RSI (14)", color=WHITE, fontsize=10, fontweight="bold")
ax5.legend(fontsize=7, facecolor=BG, labelcolor=WHITE)
style(ax5)

fig.suptitle(
    f"TQQQ 분할매수 전략  |  {START_DATE} ~ {END_DATE}  |  "
    f"{fmt_krw(INITIAL_CAPITAL)} → {fmt_krw(final_krw)}  ({tot_ret:+.0f}%)",
    color=WHITE, fontsize=11, fontweight="bold", y=0.97
)

out = "backtest_chart.png"
plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
print(f"  저장: {out}")

# CSV
if len(tdf):
    tdf.to_csv("trade_log.csv", index=False, encoding="utf-8-sig")
    print(f"  거래내역: trade_log.csv")

print(f"\n{G}[Program finished]{RST}\n")
