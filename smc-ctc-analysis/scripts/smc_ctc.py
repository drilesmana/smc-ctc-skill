#!/usr/bin/env python3
"""SMC structure analyzer - 'Candle to Candle' (CTC) method.

Alur baku (sama seperti video CTC):
  1. zoom out -> tentukan tren dari arah BOS terakhir
  2. BOS harus BODY break (close melewati level), wick-only = swing gugur
  3. tandai low/high valid = titik ekstrem leg
  4. inducement = internal structure terdekat di dalam leg (standar baru CTC:
     IDM berpindah ke internal terbaru saat leg memanjang); cek sudah tersapu belum
  5. trading range = low valid <-> high valid
  6. POI = order block (candle berlawanan terakhir sebelum impuls), hanya yang FRESH
  7. entry limit di POI, SL di luar zona + padding ATR ("jangan pelit"), TP = BOS baru
  8. MODE AGRESIF "varian I" (hasil backtest 77 video channel, OKX 1 thn x 4 pair):
     entry POI fresh TANPA nunggu IDM tersapu, TP = 50% jarak ke batas range.
     M30: WR 40.6% avgR +0.29 | H1: WR 35.0% avgR +0.15 | M5: negatif (jangan).
     Gate IDM di sim justru memotong hasil; counter-trend 1A rugi besar.

Usage:
  python3 smc_ctc.py [PAIR] [BAR]
    PAIR : btc, eth, sol, ... atau instId lengkap (BTC-USDT)
    BAR  : 1m 3m 5m 15m 30m 1H 4H 1D   (default 5m)

Sumber data: OKX public candles (tanpa API key). Binance/Bybit/Kraken/MEXC
diblokir dari jaringan Termux ini - jangan buang waktu mencoba.
"""
import json, subprocess, sys, datetime

ALIAS = {
    "btc": "BTC-USDT", "bitcoin": "BTC-USDT", "xbt": "BTC-USDT",
    "eth": "ETH-USDT", "ethereum": "ETH-USDT",
    "sol": "SOL-USDT", "solana": "SOL-USDT",
    "bnb": "BNB-USDT", "xrp": "XRP-USDT", "ripple": "XRP-USDT",
    "doge": "DOGE-USDT", "ada": "ADA-USDT", "cardano": "ADA-USDT",
    "avax": "AVAX-USDT", "link": "LINK-USDT", "chainlink": "LINK-USDT",
    "ton": "TON-USDT", "trx": "TRX-USDT", "dot": "DOT-USDT",
    "matic": "MATIC-USDT", "pol": "POL-USDT", "sui": "SUI-USDT",
    "apt": "APT-USDT", "near": "NEAR-USDT", "op": "OP-USDT",
    "arb": "ARB-USDT", "inj": "INJ-USDT", "sei": "SEI-USDT",
    "tia": "TIA-USDT", "ltc": "LTC-USDT", "bch": "BCH-USDT",
    "pepe": "PEPE-USDT", "wif": "WIF-USDT", "shib": "SHIB-USDT",
    "hbar": "HBAR-USDT", "atom": "ATOM-USDT", "fil": "FIL-USDT",
    "etc": "ETC-USDT", "uni": "UNI-USDT", "aave": "AAVE-USDT",
}

arg = (sys.argv[1] if len(sys.argv) > 1 else "btc").lower()
BAR = sys.argv[2] if len(sys.argv) > 2 else "5m"
inst = ALIAS.get(arg, arg.upper() if "-" in arg else arg.upper() + "-USDT")


def fetch(inst, bar, limit=300):
    """OKX candles. Retry: jaringan Termux/mobile kadang drop koneksi (exit 35)."""
    import time
    url = ("https://www.okx.com/api/v5/market/candles"
           f"?instId={inst}&bar={bar}&limit={limit}")
    j = None
    for attempt in range(4):
        out = subprocess.run(["curl", "-s", "--max-time", "30", url],
                             capture_output=True, text=True).stdout
        try:
            j = json.loads(out)
            if j.get("code") == "0" and j.get("data"):
                break
        except json.JSONDecodeError:
            j = None
        time.sleep(1.5 * (attempt + 1))
    if not j or j.get("code") != "0" or not j.get("data"):
        print(f"GAGAL ambil data {inst} {bar} setelah 4 percobaan: {(j or {}).get('msg') or 'koneksi gagal'}")
        print("Cek nama pair di https://www.okx.com/api/v5/public/instruments?instType=SPOT")
        sys.exit(1)
    rows = []
    for c in reversed(j["data"]):          # OKX = newest first
        rows.append(dict(ts=int(c[0]), o=float(c[1]), h=float(c[2]),
                         l=float(c[3]), c=float(c[4]), confirm=c[8]))
    return rows


rows = fetch(inst, BAR)
bars = [r for r in rows if r["confirm"] == "1"]     # closed candles only
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

# ---- BOS: close (body) melewati swing sebelumnya; wick-only -> swing gugur ----
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

print(f"=== SMC {inst} {BAR} (OKX) — metode Candle to Candle ===")
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

# ---- titik ekstrem leg ----
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

# ---- inducement ----
print("\n[4] INDUCEMENT (internal structure terdekat — standar CTC baru)")
idm = None
if d == "bull":
    cand = [i for i in sl if ext_i < i < hh_i]
    if cand:
        idm = bars[cand[-1]]["l"]
        swept = any(b["l"] < idm for b in bars[cand[-1] + 1:])
        print(f"    IDM low {f(idm)} @ {t(bars[cand[-1]]['ts'])} — tersapu: {'YA' if swept else 'BELUM'}")
else:
    cand = [i for i in sh if ext_i < i < ll_i]
    if cand:
        idm = bars[cand[-1]]["h"]
        swept = any(b["h"] > idm for b in bars[cand[-1] + 1:])
        print(f"    IDM high {f(idm)} @ {t(bars[cand[-1]]['ts'])} — tersapu: {'YA' if swept else 'BELUM'}")
if idm is None:
    swept = None
    print("    belum ada IDM internal di dalam leg")

print("\n[5] TRADING RANGE")
print(f"    batas atas : {f(bars[hh_i]['h'])} @ {t(bars[hh_i]['ts'])}")
print(f"    batas bawah: {f(bars[ll_i]['l'])} @ {t(bars[ll_i]['ts'])}")
rng = bars[hh_i]["h"] - bars[ll_i]["l"]

# ---- ATR ----
trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]),
           abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(1, len(bars))]
atr = sum(trs[-14:]) / 14

# ---- POI = order block searah struktur, fresh only ----
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
print(f"\n[7] SETUP — 2 gaya (backtest 1 thn OKX, 4 pair: M30/H1 oke, M5 jangan)")
print(f"    ATR14 {BAR}: {f(atr)}   CMP: {f(cmp_)}")
for n, p in enumerate(fresh[:3], 1):
    entry = (p["zl"] + p["zh"]) / 2
    pad = atr                                    # 'SL jangan pelit'
    sl_ = (p["zl"] - pad) if d == "bull" else (p["zh"] + pad)
    risk = abs(entry - sl_); rew = abs(tp - entry)
    rr = rew / risk if risk else 0
    # TP agresif = 50% jarak ke batas range (varian I — juara backtest)
    tp_half = entry + (tp - entry) / 2
    rr_half = (abs(tp_half - entry) / risk) if risk else 0
    warn = "  [RR<2, skip]" if rr < 2 else ""
    print(f"    POI{n}  entry {f(entry)}  SL {f(sl_)}")
    print(f"      konservatif: TP {f(tp)}  risk {f(risk)}  reward {f(rew)}  RR 1:{rr:.2f}{warn}")
    print(f"      agresif  : TP {f(tp_half)} (50% range)  risk {f(risk)}  RR 1:{rr_half:.2f}  <- varian I, tanpa tunggu IDM")

print("\n[8] SYARAT & PEMBATAL")
if idm is not None and swept is False:
    print(f"    GAYA KONSERVATIF: TUNGGU IDM {f(idm)} tersapu dulu")
    print(f"    GAYA AGRESIF (varian I): boleh entry sekarang — backtest: gate IDM memotong hasil")
elif idm is not None:
    print("    IDM sudah tersapu — kedua gaya siap dieksekusi")
print(f"    PEMBATAL: close {'di bawah' if d=='bull' else 'di atas'} titik ekstrem {f(ext_lvl)} "
      "= struktur pecah, ulang dari langkah 1")
if rng < atr * 3:
    print(f"    CATATAN: range {f(rng)} hanya {rng/atr:.1f}x ATR — ruang TP sempit, RR rapuh")
