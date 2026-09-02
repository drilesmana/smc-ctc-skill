#!/usr/bin/env python3
"""Adapter: fetch Yahoo GC=F (COMEX gold futures, ~XAUUSD proxy) 5m candles,
convert to OKX candles format, run through smc_ctc.py analysis logic.

Usage: python3 smc_xau_adapter.py [TF]   (default 5m)
"""
import json, subprocess, sys, datetime

TF = sys.argv[1] if len(sys.argv) > 1 else "5m"
URL = f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={TF}&range=1d"

out = subprocess.run(["curl", "-s", "--max-time", "30", "-H", "User-Agent: Mozilla/5.0", URL],
                     capture_output=True, text=True).stdout
j = json.loads(out)
r = j["chart"]["result"][0]
ts = r["timestamp"]
q = r["indicators"]["quote"][0]

# Build OKX-format candle list (oldest first), dropping nulls (market gaps).
rows = []
for i, t in enumerate(ts):
    o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
    if None in (o, h, l, c):
        continue
    # Yahoo last candle of the range is still forming -> confirm=0
    confirm = "0" if i == len(ts) - 1 else "1"
    rows.append(dict(ts=int(t) * 1000, o=float(o), h=float(h), l=float(l), c=float(c), confirm=confirm))

print(f"candles loaded: {len(rows)}")

# ---- reuse smc_ctc.py logic by importing it as a module is messy; instead we
# re-implement the 8-step flow here compactly (same rules as smc_ctc.py) ----

bars = [x for x in rows if x["confirm"] == "1"]
live = rows[-1]

dec = 1 if live["c"] > 100 else (4 if live["c"] > 0.1 else 8)
def f(x): return f"{x:,.{dec}f}"
def t(ts): return datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).strftime("%d %b %H:%M")
def body_hi(b): return max(b["o"], b["c"])
def body_lo(b): return min(b["o"], b["c"])

def swings(bars, k=2):
    sh, sl = [], []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(x["h"] for x in w) and bars[i]["h"] > bars[i - 1]["h"]:
            sh.append(i)
        if bars[i]["l"] == min(x["l"] for x in w) and bars[i]["l"] < bars[i - 1]["l"]:
            sl.append(i)
    return sh, sl

sh, sl = swings(bars)

bos = []
for si in sh:
    lvl = bars[si]["h"]
    for j in range(si + 1, min(si + 80, len(bars))):
        if bars[j]["c"] > lvl:
            bos.append((j, "bull", si, lvl)); break
        if bars[j]["h"] > lvl:
            break
for si in sl:
    lvl = bars[si]["l"]
    for j in range(si + 1, min(si + 80, len(bars))):
        if bars[j]["c"] < lvl:
            bos.append((j, "bear", si, lvl)); break
        if bars[j]["l"] < lvl:
            break
bos.sort()
seen, tmp = set(), []
for b in bos:
    if (b[0], b[1]) in seen:
        continue
    seen.add((b[0], b[1])); tmp.append(b)
bos = tmp

print(f"=== SMC XAUUSD-proxy (GC=F COMEX) {TF} — metode Candle to Candle ===")
print(f"Data : {t(bars[0]['ts'])} -> {t(bars[-1]['ts'])} ({len(bars)} candle closed, UTC)")
print(f"Live : {t(live['ts'])}  O {f(live['o'])}  H {f(live['h'])}  L {f(live['l'])}  C {f(live['c'])}")
if not bos:
    print("\nTidak ada BOS valid pada rentang data ini."); sys.exit()

print("\n[1] BOS terakhir (body break)")
for j, d, si, lvl in bos[-7:]:
    print(f"    {t(bars[j]['ts'])}  {d.upper():4}  level {f(lvl):>12}  close {f(bars[j]['c']):>12}")

j, d, si, lvl = bos[-1]
margin = (bars[j]["c"] - lvl) if d == "bull" else (lvl - bars[j]["c"])
dirs = [b[1] for b in bos[-4:]]
print(f"\n[2] Tren: {dirs}  ->  {'NAIK (cari BUY)' if d=='bull' else 'TURUN (cari SELL)'}")
print(f"    BOS terakhir {d.upper()} @ {t(bars[j]['ts'])}, break {f(lvl)} close {f(bars[j]['c'])}")
print(f"    margin body break: {f(margin)}  {'<- TIPIS, konfirmasi lemah' if margin < (bars[j]['h']-bars[j]['l'])*0.15 else ''}")

if d == "bull":
    ext_i = min(range(si, j + 1), key=lambda i: bars[i]["l"])
    print(f"\n[3] LOW VALID / titik ekstrem: {f(bars[ext_i]['l'])} @ {t(bars[ext_i]['ts'])}")
    ext_lvl = bars[ext_i]["l"]
else:
    ext_i = max(range(si, j + 1), key=lambda i: bars[i]["h"])
    print(f"\n[3] HIGH VALID / titik ekstrem: {f(bars[ext_i]['h'])} @ {t(bars[ext_i]['ts'])}")
    ext_lvl = bars[ext_i]["h"]

post = bars[j:]
hh_i = j + max(range(len(post)), key=lambda i: post[i]["h"])
ll_i = j + min(range(len(post)), key=lambda i: post[i]["l"])

print("\n[4] INDUCEMENT (simple structure terdekat)")
idm = None
if d == "bull":
    cand = [i for i in sl if ext_i < i < hh_i]
    if cand:
        idm = bars[cand[0]]["l"]
        swept = any(b["l"] < idm for b in bars[cand[0] + 1:])
        print(f"    IDM low {f(idm)} @ {t(bars[cand[0]]['ts'])} — tersapu: {'YA' if swept else 'BELUM'}")
else:
    cands = [i for i in sh if ext_i < i < ll_i]
    if cands:
        idm = bars[cands[0]]["h"]
        swept = any(b["h"] > idm for b in bars[cands[0] + 1:])
        print(f"    IDM high {f(idm)} @ {t(bars[cands[0]]['ts'])} — tersapu: {'YA' if swept else 'BELUM'}")
if idm is None:
    swept = None
    print("    belum ada IDM minor di dalam leg")

print("\n[5] TRADING RANGE")
print(f"    batas atas : {f(bars[hh_i]['h'])} @ {t(bars[hh_i]['ts'])}")
print(f"    batas bawah: {f(bars[ll_i]['l'])} @ {t(bars[ll_i]['ts'])}")
rng = bars[hh_i]["h"] - bars[ll_i]["l"]

trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]),
           abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(1, len(bars))]
atr = sum(trs[-14:]) / 14

cmp_ = live["c"]
pois = []
for bj, bd, bsi, blvl in bos:
    if bd != d:
        continue
    zl = zh = oi = None
    for i in range(bj - 1, max(bj - 25, 0), -1):
        if d == "bull" and bars[i]["c"] < bars[i]["o"]:
            zl, zh, oi = bars[i]["l"], body_hi(bars[i]), i; break
        if d == "bear" and bars[i]["c"] > bars[i]["o"]:
            zl, zh, oi = body_lo(bars[i]), bars[i]["h"], i; break
    if oi is None:
        continue
    after = bars[oi + 2:]
    if d == "bull":
        fresh = not any(b["l"] <= zh for b in after)
        ahead = zh < cmp_
    else:
        fresh = not any(b["h"] >= zl for b in after)
        ahead = zl > cmp_
    pois.append(dict(zl=zl, zh=zh, ts=bars[oi]["ts"], fresh=fresh, ahead=ahead))

fresh = [p for p in pois if p["fresh"] and p["ahead"]]
fresh.sort(key=lambda x: -x["zh"] if d == "bull" else x["zl"])

print(f"\n[6] POI FRESH searah struktur di jalur harga: {len(fresh)}")
for n, p in enumerate(fresh, 1):
    tag = "  <- ekstrem" if n == len(fresh) else ("  <- terdekat" if n == 1 else "")
    print(f"    POI{n}: {f(p['zl'])} - {f(p['zh'])}   ({t(p['ts'])}){tag}")

tp = bars[hh_i]["h"] if d == "bull" else bars[ll_i]["l"]
print(f"\n[7] SETUP  (TP = BOS baru / batas range = {f(tp)})")
print(f"    ATR14 {TF}: {f(atr)}   CMP: {f(cmp_)}")
for n, p in enumerate(fresh[:3], 1):
    entry = (p["zl"] + p["zh"]) / 2
    pad = atr
    sl_ = (p["zl"] - pad) if d == "bull" else (p["zh"] + pad)
    risk = abs(entry - sl_); rew = abs(tp - entry)
    rr = rew / risk if risk else 0
    warn = "  [RR<2, skip]" if rr < 2 else ""
    print(f"    POI{n}  entry {f(entry)}  SL {f(sl_)}  TP {f(tp)}  "
          f"risk {f(risk)}  reward {f(rew)}  RR 1:{rr:.2f}{warn}")

print("\n[8] SYARAT & PEMBATAL")
if idm is not None and swept is False:
    print(f"    TUNGGU: IDM {f(idm)} belum tersapu — entry baru sah setelah harga melewatinya")
elif idm is not None:
    print("    IDM sudah tersapu — POI siap dieksekusi")
print(f"    PEMBATAL: close {'di bawah' if d=='bull' else 'di atas'} titik ekstrem {f(ext_lvl)} "
      "= struktur pecah, ulang dari langkah 1")
if rng < atr * 3:
    print(f"    CATATAN: range {f(rng)} hanya {rng/atr:.1f}x ATR — ruang TP sempit, RR rapuh")
