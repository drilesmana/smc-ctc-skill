#!/usr/bin/env python3
"""SMC CTC watch — tick cron Hermes (mode no_agent): stdout = pesan Telegram.

stdout KOSONG = diam total (tidak ada notifikasi). Stdlib only.
Deteksi struktur adalah mirror persis ~/smc_ctc.py (metode Candle to Candle):
jangan ubah semantika BOS/IDM/POI tanpa ikut menyetel skill smc-ctc-analysis.

State machine per zona POI (key = inst|bar|arah|zl|zh):
  stage 1 FORMED (setup baru, harga masih jauh)
       -> 2 NEAR   (jarak <= NEAR_ATR dari zona)
       -> 3 ENTRY  (harga menyentuh zona POI)
       -> 4 done   (batal / post-entry warning; tidak alert lagi)
  BATAL: tren berbalik BOS / close melewati titik ekstrem (struktur pecah)
         / close menembus zona (safety net; normalnya touch lebih dulu).
  wait_idm: entry gaya KONSERVATIF menunggu IDM tersapu (gate CTC klasik).

Format alert v1.3 (varian I): tiap setup menampilkan DUA gaya —
  konservatif: TP batas range, entry sah setelah IDM tersapu
  agresif (varian I, juara backtest 1 thn x 4 pair): TP 50% jarak ke range,
  entry boleh langsung tanpa tunggu IDM (M30 WR 40.6% avgR +0.29).

Env testing (jangan dipakai cron):
  SMC_WATCH_DEBUG=1   -> print ringkasan per pair
  SMC_WATCH_STATE=... -> path state file alternatif
"""
import datetime, json, os, shutil, subprocess, time

HOME = os.path.expanduser("~")
STATE_FILE = os.environ.get("SMC_WATCH_STATE") or os.path.join(
    HOME, ".hermes", "scripts", "smc_watch_state.json")
ERR_LOG = os.path.join(HOME, ".hermes", "scripts", "smc_watch_error.log")
DEBUG = os.environ.get("SMC_WATCH_DEBUG") == "1"
CURL = shutil.which("curl") or "/data/data/com.termux/files/usr/bin/curl"

# pair & TF yang diawasi. Ubah daftar ini untuk menambah/mengurangi.
WATCH = [
    ("BTC-USDT", "30m"),
]
NEAR_ATR = 1.0      # jarak (x ATR) untuk alert "mendekat"
MIN_RR = 2.0        # skip setup RR < 2 (aturan metode)
STALE_H = 48.0      # state sig lebih tua dari ini dipangkas
FAIL_ALERT_N = 6    # alert "watcher bermasalah" setelah N tick gagal total
WIB = datetime.timezone(datetime.timedelta(hours=7))


def now_wib():
    return datetime.datetime.now(WIB).strftime("%d %b %H:%M")


# ---------------- Telegram (bot @Drilesmana_bot, direct API) ----------------

def _tg_conf():
    """Token + chat id dari .env Hermes (tidak pernah di-print)."""
    try:
        with open(os.path.join(HOME, ".hermes", ".env")) as fh:
            env = fh.read()
        tok = next((l.split("=", 1)[1].strip().strip('"\'')
                    for l in env.splitlines()
                    if l.startswith("TELEGRAM_BOT_TOKEN=")), "")
        chat = next((l.split("=", 1)[1].strip().strip('"\'')
                     for l in env.splitlines()
                     if l.startswith("TELEGRAM_HOME_CHANNEL=")), "")
        return tok, chat
    except Exception:
        return "", ""


def send_tg(text):
    """Kirim pesan ke chat home Telegram. Return True kalau sukses."""
    tok, chat = _tg_conf()
    if not tok or not chat:
        return False
    api = f"https://api.telegram.org/bot{tok}/sendMessage"
    # jaringan mobile sering gagal ke api.telegram.org -> retry + fallback IP
    for att in range(3):
        cmd = [CURL, "-s", "--max-time", "15", "-X", "POST", api,
               "-d", f"chat_id={chat}", "--data-urlencode", f"text={text}"]
        if att == 2:                      # percobaan terakhir: IP telegram cadangan
            cmd[1:1] = ["--resolve", "api.telegram.org:443:149.154.166.110"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=25).stdout
            if '"ok":true' in out:
                return True
        except Exception:
            pass
        time.sleep(2 + 2 * att)
    return False


def t_wib(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000, WIB).strftime("%d %b %H:%M")


# ---------------- data ----------------

def fetch(inst, bar, limit=300):
    """OKX candles; None kalau gagal (jaringan mobile sering drop)."""
    url = ("https://www.okx.com/api/v5/market/candles"
           f"?instId={inst}&bar={bar}&limit={limit}")
    for att in range(3):
        try:
            out = subprocess.run([CURL, "-s", "--max-time", "12", url],
                                 capture_output=True, text=True,
                                 timeout=20).stdout
            j = json.loads(out)
            if j.get("code") == "0" and j.get("data"):
                rows = []
                for c in reversed(j["data"]):   # OKX = newest first
                    rows.append(dict(ts=int(c[0]), o=float(c[1]), h=float(c[2]),
                                     l=float(c[3]), c=float(c[4]), confirm=c[8]))
                return rows
        except Exception:
            pass
        time.sleep(1 + att)
    return None


# ---------------- analisa (mirror smc_ctc.py) ----------------

def swings(bars, k=2):
    sh, sl = [], []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(x["h"] for x in w) and bars[i]["h"] > bars[i - 1]["h"]:
            sh.append(i)
        if bars[i]["l"] == min(x["l"] for x in w) and bars[i]["l"] < bars[i - 1]["l"]:
            sl.append(i)
    return sh, sl


def analyze(rows, inst, bar):
    bars = [r for r in rows if r["confirm"] == "1"]
    if len(bars) < 40:
        return None
    live = rows[-1]                      # candle live hanya untuk CMP/trigger
    cmp_ = live["c"]

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
    if not bos:
        return None

    j, d, si, lvl = bos[-1]
    margin = (bars[j]["c"] - lvl) if d == "bull" else (lvl - bars[j]["c"])
    thin = margin < (bars[j]["h"] - bars[j]["l"]) * 0.15

    if d == "bull":
        ext_i = min(range(si, j + 1), key=lambda i: bars[i]["l"])
    else:
        ext_i = max(range(si, j + 1), key=lambda i: bars[i]["h"])
    ext_lvl = bars[ext_i]["l" if d == "bull" else "h"]
    post = bars[j:]
    hh_i = j + max(range(len(post)), key=lambda i: post[i]["h"])
    ll_i = j + min(range(len(post)), key=lambda i: post[i]["l"])
    rng = bars[hh_i]["h"] - bars[ll_i]["l"]

    idm, swept = None, None
    if d == "bull":
        cand = [i for i in sl if ext_i < i < hh_i]
        if cand:
            idm = bars[cand[-1]]["l"]
            swept = any(b["l"] < idm for b in bars[cand[-1] + 1:])
    else:
        cand = [i for i in sh if ext_i < i < ll_i]
        if cand:
            idm = bars[cand[-1]]["h"]
            swept = any(b["h"] > idm for b in bars[cand[-1] + 1:])
    ready = idm is None or bool(swept)

    trs = [max(bars[i]["h"] - bars[i]["l"],
               abs(bars[i]["h"] - bars[i - 1]["c"]),
               abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(1, len(bars))]
    atr = (sum(trs[-14:]) / 14) or 1e-9

    pois = []
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
        after = bars[oi + 2:]
        if d == "bull":
            fr = not any(b["l"] <= zh for b in after)
            ah = zh < cmp_
        else:
            fr = not any(b["h"] >= zl for b in after)
            ah = zl > cmp_
        pois.append(dict(zl=zl, zh=zh, ts=bars[oi]["ts"], fresh=fr, ahead=ah))
    seenz, uniq = set(), []
    for p in pois:
        k = (round(p["zl"], 6), round(p["zh"], 6))
        if k in seenz:
            continue
        seenz.add(k); uniq.append(p)
    fresh = [p for p in uniq if p["fresh"] and p["ahead"]]
    fresh.sort(key=lambda x: -x["zh"] if d == "bull" else x["zl"])

    tp = bars[hh_i]["h"] if d == "bull" else bars[ll_i]["l"]
    for p in fresh:
        entry = (p["zl"] + p["zh"]) / 2
        sl_ = (p["zl"] - atr) if d == "bull" else (p["zh"] + atr)
        risk = abs(entry - sl_); rew = abs(tp - entry)
        p["entry"], p["sl"], p["tp"] = entry, sl_, tp
        p["rr"] = (rew / risk) if risk else 0
        p["dist_atr"] = ((cmp_ - p["zh"]) / atr if d == "bull"
                         else (p["zl"] - cmp_) / atr)

    dec = 1 if cmp_ > 100 else (4 if cmp_ > 0.1 else 8)
    return dict(inst=inst, bar=bar, d=d, cmp=cmp_, live=live, bars=bars,
                bos_lvl=lvl, bos_close=bars[j]["c"], bos_ts=bars[j]["ts"],
                thin=thin, narrow=rng < atr * 3, ext_lvl=ext_lvl, idm=idm,
                swept=swept, ready=ready, hh=bars[hh_i]["h"], ll=bars[ll_i]["l"],
                n_fresh=len(fresh), poi_nearest=fresh[0] if fresh else None,
                poi_valid=next((p for p in fresh if p["rr"] >= MIN_RR), None),
                f=lambda x, dec=dec: f"{x:,.{dec}f}", t=t_wib)


# ---------------- pesan ----------------

def sig_key(inst, bar, d, zl, zh):
    return f"{inst}|{bar}|{d}|{zl:.4f}|{zh:.4f}"


def tp_agr(sig):
    """TP agresif varian I: 50% jarak entry -> TP konservatif (batas range)."""
    return sig["entry"] + (sig["tp"] - sig["entry"]) / 2


def rr_agr(sig):
    risk = abs(sig["entry"] - sig["sl"]) or 1e-9
    return abs(tp_agr(sig) - sig["entry"]) / risk


def batal_msg(ana, sig, reason):
    f = ana["f"]
    return (f"🔴 SETUP BATAL · {sig['inst']} {sig['bar']} · arah {sig['d'].upper()}\n"
            f"POI {f(sig['zl'])} – {f(sig['zh'])} — {reason}.")


def post_msg(ana, sig, reason):
    f = ana["f"]
    return (f"🟠 POST-ENTRY · {sig['inst']} {sig['bar']} · {sig['d'].upper()} — {reason}\n"
            f"Entry {f(sig['entry'])} · SL {f(sig['sl'])} — cek posisi, jangan hope.")


def formed_msg(ana, sig, wait):
    f, t = ana["f"], ana["t"]
    bull = sig["d"] == "bull"
    arah = "BUY (tren naik)" if bull else "SELL (tren turun)"
    side = "di atas zona" if bull else "di bawah zona"
    lines = [f"🔵 SETUP BARU · {sig['inst']} {sig['bar']} · {arah}",
             f"POI fresh: {f(sig['zl'])} – {f(sig['zh'])} (candle {t(sig['ts'])} WIB)",
             f"Entry limit {f(sig['entry'])} · SL {f(sig['sl'])}",
             f"  konservatif: TP {f(sig['tp'])} (batas range) · RR 1:{sig['rr']:.2f}",
             f"  agresif (varian I): TP {f(tp_agr(sig))} (50% range) · RR 1:{rr_agr(sig):.2f}",
             f"Harga {f(ana['cmp'])} — jarak ~{sig['dist']:.1f} ATR {side}. "
             "Pasang limit + alarm di chart."]
    if wait:
        lines.append(f"⏳ KONSERVATIF: tunggu IDM {f(sig['idm'])} tersapu dulu. "
                     f"AGRESIF (varian I): boleh entry sekarang — backtest: gate IDM memotong hasil.")
    elif sig["idm"] is not None:
        lines.append(f"IDM {f(sig['idm'])} sudah tersapu ✔ — kedua gaya siap.")
    else:
        lines.append("Belum ada IDM internal di leg — ikuti struktur.")
    if ana["n_fresh"] > 1:
        lines.append(f"POI fresh lain di jalur: {ana['n_fresh'] - 1} — layering opsional.")
    if ana["thin"]:
        lines.append("⚠️ Konfirmasi BOS tipis (margin body break < 15% range) — setup lemah.")
    if ana["narrow"]:
        lines.append("⚠️ Range sempit (< 3x ATR) — ruang TP tipis, RR rapuh.")
    lines.append(f"Batal jika close {'di bawah low valid' if bull else 'di atas high valid'}"
                 f" {f(sig['ext'])}.")
    return "\n".join(lines)


def near_msg(ana, sig):
    f = ana["f"]
    return (f"🟡 MENDEKAT · {sig['inst']} {sig['bar']} · {sig['d'].upper()}\n"
            f"Harga {f(ana['cmp'])}, ~{sig['dist']:.1f} ATR lagi ke POI "
            f"{f(sig['zl'])} – {f(sig['zh'])}.\n"
            f"Entry {f(sig['entry'])} · SL {f(sig['sl'])}\n"
            f"konservatif: TP {f(sig['tp'])} · RR 1:{sig['rr']:.2f} | "
            f"agresif (varian I): TP {f(tp_agr(sig))} · RR 1:{rr_agr(sig):.2f}\n"
            "Pastikan limit sudah terpasang.")


def entry_msg(ana, sig, idm_ok):
    f = ana["f"]
    lines = [f"🟢 ENTRY · {sig['inst']} {sig['bar']} · {sig['d'].upper()} — "
             f"harga menyentuh POI {f(sig['zl'])} – {f(sig['zh'])}",
             f"Entry {f(sig['entry'])} · SL {f(sig['sl'])}",
             f"konservatif: TP {f(sig['tp'])} · RR 1:{sig['rr']:.2f} | "
             f"agresif (varian I): TP {f(tp_agr(sig))} · RR 1:{rr_agr(sig):.2f}",
             "Limit di tengah zona idealnya sudah terpasang; sesuaikan angka dengan chart sendiri."]
    if idm_ok is True:
        lines.append("IDM tersapu ✔ — kedua gaya sah menurut metode.")
    elif idm_ok is False:
        lines.append("⚠️ IDM belum tersapu — gaya KONSERVATIF belum sah; "
                     "gaya AGRESIF (varian I) tetap valid menurut backtest.")
    return "\n".join(lines)


def ready_msg(ana, sig, near):
    f = ana["f"]
    lines = [f"🟢 IDM TERSAPU · {sig['inst']} {sig['bar']} · {sig['d'].upper()} — "
             f"gate konservatif terpenuhi",
             f"IDM {f(sig['idm'])} tersapu ✔. POI {f(sig['zl'])} – {f(sig['zh'])} "
             f"sah dieksekusi.",
             f"Entry {f(sig['entry'])} · SL {f(sig['sl'])}",
             f"konservatif: TP {f(sig['tp'])} · RR 1:{sig['rr']:.2f} | "
             f"agresif (varian I): TP {f(tp_agr(sig))} · RR 1:{rr_agr(sig):.2f}"]
    if near:
        lines.append("Harga sudah dekat zona — siapkan/pasang order limit sekarang.")
    else:
        lines.append(f"Harga {f(ana['cmp'])} — pasang limit, tunggu harga "
                     f"{'turun ke' if sig['d'] == 'bull' else 'naik ke'} zona.")
    return "\n".join(lines)


# ---------------- state machine ----------------

def transitions(ana, state, alerts):
    now = time.time()
    inst, bar, d = ana["inst"], ana["bar"], ana["d"]
    f, t = ana["f"], ana["t"]
    bars, live, cmp_ = ana["bars"], ana["live"], ana["cmp"]
    last = bars[-1]
    bull = d == "bull"

    # --- sig lama utk pair|tf ini ---
    for key, sig in list(state.items()):
        if not isinstance(sig, dict) or "stage" not in sig:
            continue
        if sig.get("inst") != inst or sig.get("bar") != bar:
            continue
        if sig["stage"] >= 4:
            continue                      # done: tua-kan via updated, jangan sentuh
        if sig["d"] != d:
            sig["stage"] = 4
            alerts.append(batal_msg(
                ana, sig, f"struktur berbalik — BOS {d.upper()} baru @ {f(ana['bos_lvl'])} "
                          f"({t(ana['bos_ts'])} WIB)"))
            continue
        broke = (bull and last["c"] < sig["ext"]) or (not bull and last["c"] > sig["ext"])
        violated = (bull and last["c"] < sig["zl"]) or (not bull and last["c"] > sig["zh"])
        touched = (((live["l"] <= sig["zh"]) or (last["l"] <= sig["zh"])) if bull
                  else ((live["h"] >= sig["zl"]) or (last["h"] >= sig["zl"])))
        if broke:
            was3 = sig["stage"] == 3
            sig["stage"] = 4
            word = "di bawah low valid" if bull else "di atas high valid"
            reason = f"struktur pecah — close {f(last['c'])} {word} {f(sig['ext'])}"
            alerts.append(post_msg(ana, sig, reason) if was3
                          else batal_msg(ana, sig, reason))
            continue
        # gate IDM: tunggu -> tersapu?
        if sig["stage"] == 1 and sig.get("wait_idm") and sig.get("idm") is not None:
            if bull:
                swept_now = (any(b["l"] < sig["idm"] for b in bars[-6:])
                             or live["l"] < sig["idm"])
            else:
                swept_now = (any(b["h"] > sig["idm"] for b in bars[-6:])
                             or live["h"] > sig["idm"])
            if swept_now:
                sig["wait_idm"] = False
                near = sig.get("dist", 9) <= NEAR_ATR
                if near:
                    sig["stage"] = 2
                alerts.append(ready_msg(ana, sig, near))
        # harga menyentuh zona = saatnya entry
        if touched and sig["stage"] < 3:
            sig["stage"] = 3
            if sig.get("idm") is None:
                idm_ok = None
            else:
                if bull:
                    idm_ok = (any(b["l"] < sig["idm"] for b in bars[-6:])
                              or live["l"] < sig["idm"])
                else:
                    idm_ok = (any(b["h"] > sig["idm"] for b in bars[-6:])
                              or live["h"] > sig["idm"])
            alerts.append(entry_msg(ana, sig, idm_ok))
            continue
        if violated:
            was3 = sig["stage"] == 3
            sig["stage"] = 4
            side = "ke bawah" if bull else "ke atas"
            if was3:
                alerts.append(post_msg(
                    ana, sig, f"close {f(last['c'])} menembus POI {side} — "
                              f"SL {f(sig['sl'])} terancam"))
            else:
                alerts.append(batal_msg(ana, sig, "close menembus zona POI — order block gugur"))
            continue
        sig["updated"] = now

    # --- POI fresh saat ini (zona terdekat dengan RR >= 2) ---
    p = ana.get("poi_valid")
    if p:
        key = sig_key(inst, bar, d, p["zl"], p["zh"])
        sig = state.get(key)
        if sig is None:
            sig = dict(inst=inst, bar=bar, d=d, zl=p["zl"], zh=p["zh"],
                       entry=p["entry"], sl=p["sl"], tp=p["tp"], rr=p["rr"],
                       idm=ana["idm"], ext=ana["ext_lvl"], ts=p["ts"],
                       dist=p["dist_atr"], stage=0, wait_idm=False, updated=now)
            state[key] = sig
            if not ana["ready"]:
                sig["stage"] = 1
                sig["wait_idm"] = True
                alerts.append(formed_msg(ana, sig, wait=True))
            elif p["dist_atr"] <= NEAR_ATR:
                sig["stage"] = 2
                alerts.append(near_msg(ana, sig))
            else:
                sig["stage"] = 1
                alerts.append(formed_msg(ana, sig, wait=False))
        else:
            sig.update(entry=p["entry"], sl=p["sl"], tp=p["tp"], rr=p["rr"],
                       ext=ana["ext_lvl"], idm=ana["idm"],
                       dist=p["dist_atr"], updated=now)
            if (sig["stage"] == 1 and not sig.get("wait_idm")
                    and p["dist_atr"] <= NEAR_ATR):
                sig["stage"] = 2
                alerts.append(near_msg(ana, sig))
            # standar IDM baru: IDM bisa berpindah — sinkronkan gate
            # dgn kondisi IDM terbaru (sudah tersapu di history = siap)
            if (sig["stage"] == 1 and sig.get("wait_idm")
                    and (ana["idm"] is None or ana["swept"])):
                sig["wait_idm"] = False
                near = sig.get("dist", 9) <= NEAR_ATR
                if near:
                    sig["stage"] = 2
                alerts.append(ready_msg(ana, sig, near))


def prune(state, now):
    for k in list(state.keys()):
        v = state.get(k)
        if not isinstance(v, dict) or "updated" not in v:
            continue
        if now - v.get("updated", now) > STALE_H * 3600:
            del state[k]


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


# ---------------- tick ----------------

def tick():
    state = load_state()
    alerts = []
    now = time.time()
    n_ok = n_fail = 0
    for inst, bar in WATCH:
        rows = fetch(inst, bar)
        if not rows:
            n_fail += 1
            if DEBUG:
                print(f"DBG {inst} {bar}: FETCH GAGAL")
            continue
        n_ok += 1
        ana = analyze(rows, inst, bar)
        if ana is None:
            if DEBUG:
                print(f"DBG {inst} {bar}: tidak ada BOS/struktur")
            continue
        if DEBUG:
            p = ana["poi_valid"]
            pd = (f"{p['zl']:.6g}-{p['zh']:.6g} rr={p['rr']:.2f} "
                  f"dist={p['dist_atr']:.1f}ATR") if p else "-"
            print(f"DBG {inst} {bar}: tren {ana['d']} nPOI={ana['n_fresh']} "
                  f"idm={ana['idm']} swept={ana['swept']} POI1={pd}")
        transitions(ana, state, alerts)
    prune(state, now)
    if n_ok == 0 and n_fail > 0:
        state["_fail"] = state.get("_fail", 0) + 1
        if state["_fail"] >= FAIL_ALERT_N:
            state["_fail"] = 1
            alerts.append(
                "⚠️ WATCHER SMC-CTC bermasalah: OKX tidak bisa diakses pada "
                f"{FAIL_ALERT_N} tick beruntun (~{FAIL_ALERT_N * 5} menit). "
                "Cek jaringan/gateway — sinyal bisa terlewat selama kondisi ini.")
    else:
        state["_fail"] = 0
    save_state(state)
    if not alerts:
        return
    footer = ("— SMC-CTC watcher · data OKX · semua waktu WIB · level bisa "
              "geser 20–80 poin vs exchange lain · cek " + now_wib() + " WIB")
    if DEBUG:
        print("\n\n".join(alerts + [footer]))
    ok = send_tg("\n\n".join(alerts + [footer]))
    if DEBUG:
        print(f"TG SEND {'OK' if ok else 'GAGAL'}")


def main():
    try:
        tick()
    except Exception:
        import traceback
        try:
            with open(ERR_LOG, "a") as fh:
                fh.write(f"--- {now_wib()} ---\n" + traceback.format_exc() + "\n")
        except Exception:
            pass
        # tetap exit 0 + stdout kosong supaya cron tidak spam error tiap 5 menit


if __name__ == "__main__":
    main()
