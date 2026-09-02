---
name: smc-ctc-analysis
description: "Trigger 'smc <pair>': SMC BOS/IDM/POI analysis via the Candle-to-Candle method."
version: 1.2.0
author: Drilesmana
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Trading, SMC, Crypto, TechnicalAnalysis, OKX]
---

# SMC Analysis — "Candle to Candle" (CTC) method

## When to use

MAIN TRIGGER: the user types `smc <pair>` — e.g. `smc btc`, `smc eth`,
`smc solana`, `smc doge`. As soon as that pattern appears, RUN THE ANALYSIS
IMMEDIATELY. No confirmation, no clarifying questions.

Other equivalent triggers:
- "analyze BTC with the CTC technique" / "SMC analysis ETH"
- "update the SMC analysis for <pair>"
- "smc btc M15" (timeframe override)
- "is the POI still fresh?" (re-run, report only section [6])

Defaults: pair BTC-USDT, timeframe M5, source OKX spot. Mention another pair
to change it; mention a timeframe to override.

Method derived from the "Candle to Candle" YouTube channel (video QKWafO7F6Mg).

Respond in the user's own language. Keep the trading terms as-is (BOS,
IDM/inducement, POI, valid low/high, SL, TP, RR) — do not translate them.

## How to run

Script: `scripts/smc_ctc.py` inside this skill directory (stdlib-only, no
dependencies beyond `curl` on PATH).

```bash
python3 scripts/smc_ctc.py btc          # default M5
python3 scripts/smc_ctc.py eth 15m      # other timeframe
python3 scripts/smc_ctc.py solana       # alias -> SOL-USDT
python3 scripts/smc_ctc.py PEPE-USDT    # raw instId also accepted
```

Run it from the skill directory (or adjust the relative path). Valid OKX
bars: `1m 3m 5m 15m 30m 1H 2H 4H 6H 12H 1D 1W`. Aliases exist for ~40
popular coins; if not in ALIAS, the script appends `-USDT`.

## Data source

OKX public candles API, no API key needed:
`https://www.okx.com/api/v5/market/candles?instId=<PAIR>&bar=<TF>&limit=300`

If OKX fails, a good fallback is Gate.io
(`api.gateio.ws/api/v4/spot/candlesticks`). Do not assume other exchanges
(Binance, Bybit, Kraken, MEXC, KuCoin, Bitstamp) are reachable from the
user's network — some networks block them. The script retries 4x.

## Analysis flow (8 steps, follow this exact order)

1. **Zoom out → trend.** Direction from the last 4 BOS. Up trend = only
   look for BUY, down trend = only SELL. Follow the trend.
2. **Last BOS must be a BODY break.** Close must clear the swing level.
   Wick-only = swing invalidated, not a BOS. Report the body-break margin;
   if margin < 15% of the candle range, explicitly call it "weak
   confirmation".
3. **Valid low / valid high** = leg extreme point (lowest low between the
   broken swing and the BOS candle for bulls; mirror for bears). Present as
   a zone, not a single line.
4. **Inducement (IDM)** = nearest internal structure inside the leg (new CTC
   standard, video 6YSspaWKkhg). The IDM shifts to the latest internal as the
   leg extends. Check whether it has been swept. This is the execution gate: if IDM is not yet
   swept, the entry is NOT valid — say "WAIT" and name the level that must
   be crossed first.
5. **Trading range** = valid low ↔ valid high.
6. **Fresh POI** = order block (last opposing candle before the impulse)
   aligned with structure, never touched, still in the price path.
7. **Setup**: limit entry at the middle of the POI zone, SL beyond the zone
   + 1x ATR14 padding ("don't be stingy with the SL" — stressed twice in
   the source video), TP at the new BOS / range boundary. Compute RR for
   the top 3 POIs.
8. **Conditions & invalidation**: levels that break the structure, plus a
   note if range < 3x ATR (tight TP room).

## How to report

Do not paste raw script output. Rearrange it into the 8 sections above with
the script's numbers, then ALWAYS add an honest setup-quality assessment:

- Thin body-break margin → weak setup, state the number.
- Range < 3x ATR → RR fragile; noise + spread eat a large portion.
- Multiple POIs → **admit there is no objective rule for picking one.**
  This is a weakness of the method acknowledged by the video's creator
  (10:51): "we won't know which one will be taken". Practical solution:
  layering.
- Bigger-TF context: check RSI / price position in the daily range. A M5
  long while daily RSI is overbought faces headwind — mention it.
- If `FRESH POI ... : 0` → don't force a setup. Say there is no valid setup
  right now and what must be waited for. Common right after a BOS flips
  direction (new structure, POI not yet formed).

Always close with: levels can shift 20–80 points between exchanges; the
user should align with their own chart, not copy numbers blindly.

Do not compute lot size or risk percentage. Provide levels, not sizing —
users manage their own risk.

## Pitfalls

- **Never use the live candle for structure.** OKX field `confirm` = "0"
  means still forming. The script filters these, but still show the live
  candle as CMP.
- **OKX returns newest-first.** Must be reversed. If forgotten, all BOS
  detection inverts and the output looks plausible but is completely wrong.
- **M5 structure changes fast.** Results go stale in 15–30 minutes. If the
  user asks again, re-run — never reuse numbers from a previous answer.
- **All times are UTC.** State that; convert to the user's timezone if known.
- A one-sample trade is not proof of edge. If the user asks to validate the
  method, point to a 50–100 trade backtest, not one good-looking setup.

## Verification

After editing the script, test at least 3 pairs and 1 invalid pair:

```bash
python3 scripts/smc_ctc.py btc && python3 scripts/smc_ctc.py eth 15m && python3 scripts/smc_ctc.py sol
python3 scripts/smc_ctc.py fakecoin123    # must print a clean error, not a traceback
```
