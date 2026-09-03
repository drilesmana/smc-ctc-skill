#!/usr/bin/env python3
"""Backtest varian metode CTC (Candle to Candle) v2 — hasil ekstraksi 77 video channel.

Varian yang diuji (semua dari aturan objektif video):
  A. 2A classic   : BOS -> IDM tersapu -> entry POI fresh, SL POI+ATR, TP range boundary
  B. 2A no-gate   : sama tapi tanpa tunggu IDM tersapu (validasi BOS tanpa IDM)
  C. 2A half-TP   : TP = 50% jarak entry->boundary (scalp)
  D. EQ follow    : entry di equilibrium (50% leg) searah struktur (discount/premium)
  E. IDM sniper   : entry pas pullback ke level IDM, SL tipis di ekstrem candle IDM (RR besar)
  F. 1A counter    : LAWAN struktur — entry di equilibrium swing after-BOS, SL swing extrema,
                     TP extrema leg (video: RR optimal 1:2..1:3, winrate ~1 dari 3 swing)
  G. 2A TP-fixed-2R: varian A tapi TP = 2R (klaim video: winrate lebih tinggi)
  H. 2A TP-fixed-3R: varian A tapi TP = 3R

Sim konservatif: SL diprioritaskan di bar isian sama; tanpa lookahead; fee/spread 0.
"""
import json, subprocess, time, datetime, sys, os

TF = sys.argv[1] if len(sys.argv) > 1 else "30m"
PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 30
PAIRS = [("BTC-USDT", "BTC"), ("ETH-USDT", "ETH"), ("SOL-USDT", "SOL"),
         ("XAUT-USDT", "XAU")]
TIMEOUT = 120      # bar
EXPIRE = 240       # bar
W = 250            # window analisa
STEP = 2
CACHE_DIR = os.path.expanduser("~/bt_cache")


def curl_json(url):
    for att in range(3):
        out = subprocess.run(["curl", "-s", "--max-time", "20", url],
                             capture_output=True, text=True).stdout
        try:
            j = json.loads(out)
            if j.get("code") == "0":
                return j.get("data") or []
        except Exception:
            pass
        time.sleep(1 + att)
    return None


def fetch_all(inst, bar):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = f"{CACHE_DIR}/{inst}_{bar}.json"
    if os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    rows, after = {}, None
    for _ in range(PAGES):
        url = (f"https://www.okx.com/api/v5/market/history-candles?instId={inst}"
               f"&bar={bar}&limit=300" + (f"&after={after}" if after else ""))
        data = curl_json(url)
        if not data:
            break
        new, oldest = 0, None
        for c in data:
            ts = int(c[0])
            if ts not in rows:
                rows[ts] = c
                new += 1
            oldest = ts if oldest is None or ts < oldest else oldest
        if new == 0:
            break
        after = oldest
        time.sleep(0.2)
    out = []
    for ts in sorted(rows):
        c = rows[ts]
        if c[8] != "1":
            continue
        out.append(dict(ts=ts, o=float(c[1]), h=float(c[2]),
                        l=float(c[3]), c=float(c[4])))
    with open(cache, "w") as fh:
        json.dump(out, fh)
    return out


def swings(bars, k=2):
    n = len(bars)
    sh, sl = [], []
    for i in range(k, n - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(x["h"] for x in w) and bars[i]["h"] > bars[i - 1]["h"]:
            sh.append(i)
        if bars[i]["l"] == min(x["l"] for x in w) and bars[i]["l"] < bars[i - 1]["l"]:
            sl.append(i)
    return sh, sl


def bos_list(bars, sh, sl):
    n = len(bars)
    bos = []
    for si in sh:
        lvl = bars[si]["h"]
        for j in range(si + 1, min(si + 80, n)):
            if bars[j]["c"] > lvl:
                bos.append((j, "bull", si, lvl)); break
            if bars[j]["h"] > lvl:
                break
    for si in sl:
        lvl = bars[si]["l"]
        for j in range(si + 1, min(si + 80, n)):
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
    return tmp


def analyze(bars):
    """Struktur CTC di window bars. Return dict atau None."""
    n = len(bars)
    sh, sl = swings(bars)
    bos = bos_list(bars, sh, sl)
    if not bos:
        return None
    j, d, si, _ = bos[-1]
    if d == "bull":
        ext_i = min(range(si, j + 1), key=lambda i: bars[i]["l"])
    else:
        ext_i = max(range(si, j + 1), key=lambda i: bars[i]["h"])
    post = bars[j:]
    hh_i = j + max(range(len(post)), key=lambda i: post[i]["h"])
    ll_i = j + min(range(len(post)), key=lambda i: post[i]["l"])

    # IDM standar baru = internal terdekat dalam leg (cand[-1])
    idm, idm_i, idm_swept = None, None, None
    if d == "bull":
        cand = [i for i in sl if ext_i < i < hh_i]
        if cand:
            idm_i = cand[-1]
            idm = bars[idm_i]["l"]
            idm_swept = any(b["l"] < idm for b in bars[idm_i + 1:])
    else:
        cand = [i for i in sh if ext_i < i < ll_i]
        if cand:
            idm_i = cand[-1]
            idm = bars[idm_i]["h"]
            idm_swept = any(b["h"] > idm for b in bars[idm_i + 1:])

    # swing fade terakhir dalam leg (buat setup 1A counter)
    if d == "bull":
        fades = [i2 for i2 in sh if ext_i < i2 < hh_i]
        fade = bars[fades[-1]]["h"] if fades else None
    else:
        fades = [i2 for i2 in sl if ext_i < i2 < ll_i]
        fade = bars[fades[-1]]["l"] if fades else None

    trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]),
               abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(1, n)]
    atr = sum(trs[-14:]) / 14 or 1e-9
    cmp_ = bars[-1]["c"]

    # POI fresh searah struktur
    seenz, pois = set(), []
    for bj, bd, bsi, blvl in bos:
        if bd != d:
            continue
        zl = zh = oi = None
        for i in range(bj - 1, max(bj - 25, 0), -1):
            if d == "bull" and bars[i]["c"] < bars[i]["o"]:
                zl, zh, oi = bars[i]["l"], max(bars[i]["o"], bars[i]["c"]), i; break
            if d == "bear" and bars[i]["c"] > bars[i]["o"]:
                zl, zh, oi = min(bars[i]["o"], bars[i]["c"]), bars[i]["h"], i; break
        if oi is None:
            continue
        after_ = bars[oi + 2:]
        if d == "bull":
            fr = not any(b["l"] <= zh for b in after_); ah = zh < cmp_
        else:
            fr = not any(b["h"] >= zl for b in after_); ah = zl > cmp_
        if not (fr and ah):
            continue
        k = (round(zl, 6), round(zh, 6))
        if k in seenz:
            continue
        seenz.add(k)
        pois.append(dict(zl=zl, zh=zh, ts=bars[oi]["ts"]))
    pois.sort(key=lambda p: -p["zh"] if d == "bull" else p["zl"])

    return dict(d=d, idm=idm, idm_i=idm_i, idm_swept=idm_swept, pois=pois, fade=fade,
                atr=atr, hh=bars[hh_i]["h"], ll=bars[ll_i]["l"],
                ext_l=bars[ext_i]["l"], ext_h=bars[ext_i]["h"])


# ---------------- varian metode ----------------

def variants():
    return {
        "A_2a_poi_idm_rng": dict(entry="poi", gate="idm", tp="range", min_rr=2.0),
        "B_2a_poi_nogate": dict(entry="poi", gate="none", tp="range", min_rr=2.0),
        "C_2a_poi_half": dict(entry="poi", gate="idm", tp="half", min_rr=2.0),
        "D_poi_eq_band": dict(entry="poi_eq", gate="idm", tp="range", min_rr=2.0),
        "E_idm_sniper": dict(entry="idmsweep", gate="none", tp="range", min_rr=2.0),
        "F_1a_counter_eq": dict(entry="countereq", gate="none", tp="range",
                                min_rr=1.5, counter=True),
        "G_2a_poi_tp2R": dict(entry="poi", gate="idm", tp="range", tp_fix=2.0, min_rr=2.0),
        "H_2a_poi_tp3R": dict(entry="poi", gate="idm", tp="range", tp_fix=3.0, min_rr=2.0),
        "I_nogate_half": dict(entry="poi", gate="none", tp="half", min_rr=2.0),
    }


def build_setup(ana, v, bars, i):
    d = ana["d"]; bull = d == "bull"
    atr = ana["atr"]
    hh, ll = ana["hh"], ana["ll"]
    ext_l, ext_h = ana["ext_l"], ana["ext_h"]
    out = []

    if v["entry"] == "poi" or v["entry"] == "poi_eq":
        for p in ana["pois"][:2]:        # top-2 POI fresh
            # filter band premium/discount: POI harus di band equilibrium±ATR
            if v["entry"] == "poi_eq":
                if bull:
                    eq = (ext_l + hh) / 2
                    if p["zl"] > eq + atr:   # POI terlalu atas (mahal)
                        continue
                else:
                    eq = (ext_h + ll) / 2
                    if p["zh"] < eq - atr:   # POI terlalu bawah (mahal)
                        continue
            entry = (p["zl"] + p["zh"]) / 2
            sl_ = (p["zl"] - atr) if bull else (p["zh"] + atr)
            tp_ = hh if bull else ll
            if v.get("tp_fix"):
                risk0 = abs(entry - sl_)
                tp_ = entry + (v["tp_fix"] * risk0 if bull else -v["tp_fix"] * risk0)
            elif v["tp"] == "half":
                # TP = 50% jarak entry->boundary (scalp style)
                tp_ = entry + ((tp_ - entry) / 2 if bull else (tp_ - entry) / 2)
            out.append(dict(kind="poi", zl=p["zl"], zh=p["zh"], ts=p["ts"],
                            entry=entry, sl=sl_, tp=tp_, bull=bull))

    elif v["entry"] == "eq":
        # equilibrium leg terakhir: extrema valid -> post-BOS extrema (bias searah)
        entry = (ext_l + hh) / 2 if bull else (ext_h + ll) / 2
        if (bull and entry < bars[-1]["c"]) or (not bull and entry > bars[-1]["c"]):
            sl_ = (ext_l - atr) if bull else (ext_h + atr)
            tp_ = hh if bull else ll
            out.append(dict(kind="eq", zl=entry, zh=entry, ts=bars[-1]["ts"],
                            entry=entry, sl=sl_, tp=tp_, bull=bull))

    elif v["entry"] == "idmsweep":
        # sniper: entry pullback ke IDM, SL tipis di ekstrem candle IDM
        if ana["idm"] is not None and not ana["idm_swept"]:
            idm = ana["idm"]
            if bull:
                sl_ = bars[ana["idm_i"]]["l"] - 0.5 * atr
                tp_ = hh
                if sl_ < idm:                       # IDM candle low harus di bawah entry
                    out.append(dict(kind="idm", zl=idm, zh=idm, ts=bars[-1]["ts"],
                                    entry=idm, sl=sl_, tp=tp_, bull=True))
            else:
                sl_ = bars[ana["idm_i"]]["h"] + 0.5 * atr
                tp_ = ll
                if sl_ > idm:
                    out.append(dict(kind="idm", zl=idm, zh=idm, ts=bars[-1]["ts"],
                                    entry=idm, sl=sl_, tp=tp_, bull=False))

    elif v["entry"] == "countereq":
        # Setup 1A: LAWAN tren fresh (IDM belum tersapu) — fade swing terakhir
        # entry di ekstrem swing fade, TP = level IDM (video: sapu IDM dulu baru POI)
        if ana["idm"] is not None and not ana["idm_swept"] and ana["fade"] is not None:
            idm = ana["idm"]
            if bull:      # tren bull -> SELL counter di high swing terakhir
                entry = ana["fade"]
                if entry > bars[-1]["c"]:
                    sl_ = entry + atr
                    tp_ = idm
                    out.append(dict(kind="cfade", zl=entry, zh=entry, ts=bars[-1]["ts"],
                                    entry=entry, sl=sl_, tp=tp_, bull=False))
            else:         # tren bear -> BUY counter di low swing terakhir
                entry = ana["fade"]
                if entry < bars[-1]["c"]:
                    sl_ = entry - atr
                    tp_ = idm
                    out.append(dict(kind="cfade", zl=entry, zh=entry, ts=bars[-1]["ts"],
                                    entry=entry, sl=sl_, tp=tp_, bull=True))

    res = []
    for s in out:
        risk = abs(s["entry"] - s["sl"])
        if risk <= 0:
            continue
        rr = abs(s["tp"] - s["entry"]) / risk
        if rr < v.get("min_rr", 2.0):
            continue
        s["rr"] = rr
        s["risk"] = risk
        # zona toleransi batal: setengah jalan ke SL
        half = 0.5 * risk
        if s["bull"]:
            s["zl_v"] = s["entry"] - half
            s["zh_v"] = s["entry"] + half
        else:
            s["zl_v"] = s["entry"] - half
            s["zh_v"] = s["entry"] + half
        res.append(s)
    return res


def backtest(bars, vname, v):
    stats = dict(setup=0, fill=0, win=0, loss=0, scratch=0, r=0.0)
    setups = {}
    n = len(bars)
    for i in range(W, n):
        bar = bars[i]
        # ---- kelola setup aktif ----
        for key, s in list(setups.items()):
            b = s["bull"]
            if s["filled"]:
                if s["done"]:
                    continue
                if i - s["fill_i"] > TIMEOUT:
                    exit_ = bar["c"]
                    r = ((exit_ - s["entry"]) if b else (s["entry"] - exit_)) / s["risk"]
                    stats["scratch"] += 1; stats["r"] += r; s["done"] = True
                    continue
                loss = bar["l"] <= s["sl"] if b else bar["h"] >= s["sl"]
                win = (not loss) and (bar["h"] >= s["tp"] if b else bar["l"] <= s["tp"])
                if loss:
                    stats["loss"] += 1; stats["r"] -= 1; s["done"] = True
                elif win:
                    stats["win"] += 1; stats["r"] += s["rr"]; s["done"] = True
                continue
            # belum isi: update gate IDM
            if v["gate"] == "idm" and not s.get("gate_passed"):
                idm = s["idm"]
                if idm is not None and ((b and bar["l"] < idm) or (not b and bar["h"] > idm)):
                    s["gate_passed"] = True
                elif idm is None:
                    s["gate_passed"] = True
            viol = bar["c"] < s["zl_v"] if b else bar["c"] > s["zh_v"]
            if s.get("gate_passed") or v["gate"] == "none":
                hit = bar["l"] <= s["entry"] if b else bar["h"] >= s["entry"]
                if hit:
                    s["filled"] = True; s["fill_i"] = i
                    stats["fill"] += 1
                    loss = bar["l"] <= s["sl"] if b else bar["h"] >= s["sl"]
                    win = (not loss) and (bar["h"] >= s["tp"] if b else bar["l"] <= s["tp"])
                    if loss:
                        stats["loss"] += 1; stats["r"] -= 1; s["done"] = True
                    elif win:
                        stats["win"] += 1; stats["r"] += s["rr"]; s["done"] = True
                    continue
            if viol or i - s["born_i"] > EXPIRE:
                del setups[key]
        # ---- scan sinyal baru ----
        if (i - W) % STEP == 0:
            ana = analyze(bars[i - W + 1:i + 1])
            if ana:
                for s in build_setup(ana, v, bars, i):
                    key = f"{vname}|{s['kind']}|{s['zl']:.4f}|{s['zh']:.4f}|{s['ts']}"
                    if key in setups:
                        continue
                    st = dict(bull=s["bull"], entry=s["entry"], sl=s["sl"],
                              tp=s["tp"], rr=s["rr"], risk=s["risk"],
                              zl_v=s["zl_v"], zh_v=s["zh_v"],
                              idm=ana["idm"], filled=False, fill_i=-1, done=False,
                              born_i=i, gate_passed=(
                                  v["gate"] == "none" or ana["idm"] is None
                                  or bool(ana["idm_swept"])))
                    setups[key] = st
                    stats["setup"] += 1
                # cleanup tren flip — hanya setup follow-trend (bukan counter)
                cur_bull = ana["d"] == "bull"
                for key, s in list(setups.items()):
                    if v.get("counter"):
                        continue
                    if s["bull"] != cur_bull and not s["filled"] and not s["done"]:
                        del setups[key]
    return stats


def dt(ts):
    return datetime.datetime.fromtimestamp(
        ts / 1000, datetime.timezone.utc).strftime("%d %b")


def show(title, grand):
    print(f"\n{title}")
    print(f"{'varian':20} {'set':>4} {'isi':>4} {'win':>4} {'loss':>4} {'scr':>3} "
          f"{'wr%':>6} {'R':>7} {'avgR':>6}")
    for vn, st in grand.items():
        decided = st["win"] + st["loss"]
        wr = 100 * st["win"] / decided if decided else 0
        avg = st["r"] / st["fill"] if st["fill"] else 0
        print(f"{vn:20} {st['setup']:>4} {st['fill']:>4} {st['win']:>4} "
              f"{st['loss']:>4} {st['scratch']:>3} {wr:>5.1f}% {st['r']:>+7.1f} {avg:>+6.2f}")


print("=" * 78)
print(f"BACKTEST VARIAN METODE CTC — TF {TF} — {len(PAIRS)} pair")
print("=" * 78)
V = variants()
grand = {vn: dict(setup=0, fill=0, win=0, loss=0, scratch=0, r=0.0) for vn in V}
for inst, label in PAIRS:
    bars = fetch_all(inst, TF)
    if len(bars) < W + 200:
        print(f"{label}: data kurang ({len(bars)}) — skip")
        continue
    print(f"\n### {inst} {TF} — {len(bars)} bar ({dt(bars[0]['ts'])} -> {dt(bars[-1]['ts'])})")
    pair_stats = {vn: dict(setup=0, fill=0, win=0, loss=0, scratch=0, r=0.0) for vn in V}
    for vn, v in V.items():
        st = backtest(bars, vn, v)
        pair_stats[vn] = st
        for k in grand[vn]:
            grand[vn][k] += st[k]
    show(f"--- {label} {TF} ---", pair_stats)
show(f"GRAND TOTAL (semua pair) TF {TF}", grand)
print("\nCatatan: SL prioritas bar-isian-sama (konservatif), fee 0, tanpa lookahead.")
