# smc-ctc-skill

SMC (Smart Money Concepts) market-structure analysis skill for **Hermes Agent**,
implementing the **"Candle to Candle" (CTC)** method: BOS body-break, inducement
(IDM), valid low/high, trading range, fresh POI (order blocks), and RR-graded
limit setups.

Data: OKX public candles API (no API key). The script is Python stdlib-only —
no pip installs needed, just `python3` + `curl` on PATH.

## What it does

Type `smc btc` (or `smc eth 15m`, `smc sol`, ...) to your Hermes agent and it
runs a full 8-step structure analysis:

1. Trend from the last 4 BOS (body breaks only — wick-only breaks invalidate
   the swing)
2. Body-break margin check ("weak confirmation" if < 15% of candle range)
3. Valid low/high (leg extremes)
4. Inducement sweep check — the execution gate
5. Trading range
6. Fresh POI zones (untouched order blocks in the price path)
7. Setup: entry mid-POI, SL beyond zone + 1x ATR14, TP at BOS/range boundary,
   RR for the top 3 POIs
8. Invalidation levels + tight-range warnings

## Install into Hermes Agent

```bash
# from this repo (owner = your GitHub username / whatever fork)
hermes skills install <owner>/smc-ctc-skill/smc-ctc-analysis
```

Or add this repo as a tap, then install by name:

```bash
hermes skills tap add <owner>/smc-ctc-skill
hermes skills install smc-ctc-analysis
```

Or just clone it manually into your skills directory:

```bash
git clone https://github.com/<owner>/smc-ctc-skill.git
cp -r smc-ctc-skill/smc-ctc-analysis ~/.hermes/skills/research/
```

## Using it

In any Hermes chat (CLI, Telegram, Discord, ...):

```
smc btc           # BTC-USDT, M5 (defaults)
smc eth 15m       # ETH-USDT, M15
smc doge          # DOGE-USDT
smc PEPE-USDT     # raw OKX instId also accepted
```

Valid timeframes: `1m 3m 5m 15m 30m 1H 2H 4H 6H 12H 1D 1W`.
~40 popular coin aliases built in; anything else gets `-USDT` appended.

## Standalone script (no Hermes needed)

```bash
python3 smc-ctc-analysis/scripts/smc_ctc.py btc
python3 smc-ctc-analysis/scripts/smc_ctc.py eth 15m
```

## XAUUSD / gold support

- **Analysis on demand:** `smc xau` in chat runs `smc_xau_adapter.py`
  (COMEX futures via Yahoo — good structure proxy) and/or
  `smc_ctc.py XAUT-USDT` (Tether Gold, tracks spot XAU/USD within ~$3 —
  the closest thing to an MT4/MT5 spot feed that works without a broker
  connection).
- **Watcher (`scripts/smc_watch_xau.py`):** cron-able Telegram alerter for
  XAUUSD M30, spot-based (XAUT-USDT). Emits SETUP BARU / MENDEKAT / IDM
  TERSAPU / ENTRY / SETUP BATAL / POST-ENTRY messages, stays silent on
  weekends, and keeps state across ticks. stdlib-only.

  ```bash
  # one-shot debug (prints summary + sends alerts if any)
  SMC_WATCH_DEBUG=1 python3 smc-ctc-analysis/scripts/smc_watch_xau.py

  # cron it (example: every 5 min); set TELEGRAM_BOT_TOKEN + TG_CHAT_ID
  # in the environment, or run it via a Hermes no-agent cron job
  */5 * * * * SMC_WATCH_STATE=$HOME/xau_state.json python3 .../smc_watch_xau.py
  ```

  If no Telegram config is present, alerts go to stdout (useful for
  piping into ntfy/ntfy.sh, Discord webhooks, etc.).

  Why XAUT and not futures? COMEX `GC=F` carries a +$30–40 futures basis —
  M5 structure can differ completely from a broker's spot XAUUSD chart.
  XAUT is 1 XAUT = 1 oz LBMA gold, verified to track GLD×10 within ~$3.

## Method notes & honest limitations

- Only closed candles are used for structure (OKX `confirm=1`); the live
  candle is shown as CMP only.
- Multiple fresh POIs: there is **no objective rule** for choosing which one
  gets taken — the CTC creator says this himself. Layering is the practical
  answer.
- M5 structure goes stale in 15–30 minutes — re-run rather than reuse numbers.
- A single sample trade is not proof of edge; validate with a 50–100 trade
  backtest.

## Credits

Method: the ["Candle to Candle"](https://www.youtube.com/watch?v=QKWafO7F6Mg)
YouTube channel. Skill author: Drilesmana. MIT license.

> Not financial advice. Levels shift between exchanges — always align with
> your own chart.
