# jevtrade

A swing trading bot for **spot BTC/USDT, ETH/USDT and SOL/USDT** on Binance.
**Rules** in code find opportunities, **Jev AI** gives a second opinion
(it can only cancel a trade or make it smaller), and **risk management** is strict.

> ⚠️ **Phase: paper trading.** The bot only uses virtual money.
> There is no API key, so it is technically impossible for it to place a real order.

---

## Contents

1. [The big picture](#1-the-big-picture)
2. [Folder structure](#2-folder-structure)
3. [Setup and running](#3-setup-and-running)
4. [① data — market data](#4--data--market-data)
5. [② indicators — technical indicators](#5--indicators--technical-indicators)
6. [③ regime — the market's "mood"](#6--regime--the-markets-mood)
7. [Safety rules](#7-safety-rules)
8. [Glossary](#8-glossary)
9. [Where we are](#9-where-we-are)

---

## 1. The big picture

Every hour, when a 1h candle closes, the bot goes through 7 "stations".
Each station is a folder inside `src/jevtrade/`:

```
 ① data/         Fetch market data from Binance              ✅
       ↓
 ② indicators/   Compute indicators (EMA, RSI, ...)          ✅
       ↓
 ③ regime/       What "mood" is the market in?              ✅
       ↓
 ④ strategies/   Is there a buying opportunity?             ⬜
       ↓
 ⑤ jev/          What does Jev say? (second opinion)        ⬜
       ↓
 ⑥ risk/         Is it allowed? How much do we buy?         ⬜
       ↓
 ⑦ execution/    Buy (virtually) and monitor the position   ⬜
```

**The philosophy:** the bot does not try to guess where the price will go.
It patiently waits for specific situations where the odds are slightly in our favour,
enters with small risk and exits with strict rules.
**Most hours it does nothing — and that is correct.**

---

## 2. Folder structure

```
JEV-Trading-Bot/
├── pyproject.toml              ← project identity + required libraries
├── .gitignore                  ← what is NOT pushed to GitHub (.venv, .env with secrets)
├── README.md                   ← this file
├── src/jevtrade/               ← the bot's code (Python package)
│   ├── data/
│   │   └── candles.py          ← ① fetches candles from Binance
│   ├── indicators/
│   │   └── technical.py        ← ② computes the indicators
│   └── regime/
│       └── detector.py         ← ③ decides the market's "mood"
├── config/                     ← (later) configuration files
├── scripts/                    ← (later) helper scripts
└── docs/                       ← (later) notes
```

**Why `src/jevtrade/` and not just `jevtrade/`?**
This is the *src layout*: it forces the code to run as an **installed package**,
exactly as it will run inside Docker. It avoids "works on my machine" surprises.

**What are the `__init__.py` files?** Empty files that tell Python "this folder is a package",
so we can write `from jevtrade.indicators.technical import add_ema`.

---

## 3. Setup and running

### Libraries

| Library | What it does |
|---|---|
| **ccxt** | Talks to Binance (and 100+ other exchanges) through one common interface |
| **pandas** | Data tables — like Excel inside Python |
| **TA-Lib** | Computes the indicators (EMA, RSI, ATR, ...) — the industry standard |

### First time

```bash
python -m venv .venv                 # isolated "box" for the project's libraries
source .venv/Scripts/activate        # Git Bash   (PowerShell: .venv\Scripts\Activate.ps1)
pip install -e .                     # install the project + its libraries
```

`-e` (editable) means every code change takes effect immediately, without reinstalling.

### Running each station (demo)

Each file ends with a small demo (`if __name__ == "__main__":`) that runs on live data:

```bash
python -m jevtrade.data.candles          # ① the last 10 hourly BTC candles
python -m jevtrade.indicators.technical  # ② a "dashboard" with all BTC indicators
python -m jevtrade.regime.detector       # ③ the regime for BTC, ETH, SOL
```

> 💡 Run with `python -m ...`, **not** with VS Code's ▶ button.
> That way, if a file is in the wrong folder, you notice immediately.

### Known issue: corporate VPN

If you see `SSL: CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain`,
the **corporate VPN** is the cause: it inspects encrypted connections and re-signs them with a
company certificate that Python does not trust.
**Fix:** disconnect the VPN. **Never** disable SSL verification in the code.

---

## 4. ① `data` — market data

📄 [`src/jevtrade/data/candles.py`](src/jevtrade/data/candles.py)

### What a candle is (OHLCV)

The price movement over a time period (e.g. 1 hour), compressed into 6 values:

| Field | Meaning | Example (1h BTC) |
|---|---|---|
| **timestamp** | When the candle started (in **UTC**) | 14:00 |
| **open** | Price at the start of the hour | 84,050 |
| **high** | Highest price during the hour | 84,480 |
| **low** | Lowest price during the hour | 83,950 |
| **close** | Price at the end of the hour | 84,400 |
| **volume** | How many BTC changed hands | 720 |

- **Green candle:** `close > open` → the price went up during the hour 🟢
- **Red candle:** `close < open` → the price went down 🔴

### Timeframes — the "zoom levels"

| Timeframe | Role | Example question |
|---|---|---|
| **1d** (daily) | The big picture | "Is this month's trend up?" |
| **4h** | The trend | "Are we in a TREND?" |
| **1h** | The moment of decision | "Is now the right time to enter?" |

### Functions

#### `create_exchange()`
Creates the "connection" to Binance.
- **No API key** → it can only **read** public data; it cannot buy anything.
- `enableRateLimit: True` → waits a little between requests so Binance does not block us.

#### `fetch_candles(exchange, symbol, timeframe, limit)`
Fetches the last `limit` **closed** candles and returns them as a pandas table (oldest first).

| Parameter | Meaning | Example |
|---|---|---|
| `symbol` | The trading pair | `"BTC/USDT"` (BTC priced in "digital dollars") |
| `timeframe` | Length of each candle | `"1h"`, `"4h"`, `"1d"` |
| `limit` | How many candles | `1000` (Binance's maximum per request) |

**Why only closed candles?** At 14:37 the 14:00 candle is still running — its `close` keeps changing.
A decision based on it could see a "green candle" that turns red by 15:00.
So we request `limit + 1` candles and drop the one that is still open.

**Why UTC?** The whole bot works in UTC so we never mix up local time and daylight saving.
(14:00 UTC = 17:00 in Greece in summer.)

**How much time 1,000 candles cover:**

| Timeframe | 1,000 candles ≈ |
|---|---|
| 1h | ~41 days |
| 4h | ~166 days |
| 1d | ~2.7 years |

That is enough for every indicator and for the regime's "compare with the last month" checks.

---

## 5. ② `indicators` — technical indicators

📄 [`src/jevtrade/indicators/technical.py`](src/jevtrade/indicators/technical.py)

Each `add_*` function takes the candle table and **adds one or more new columns**
with the indicator's value **for every candle**. TA-Lib does the maths locally,
with no extra network requests.

```
before:  open  high  low  close  volume
after:   open  high  low  close  volume  ema_20  rsi_14  atr_14  ...
```

> ⚠️ **Rule:** first add **all** indicators to the table, **then** take the last row
> (`df.iloc[-1]`). `iloc[-1]` is a "snapshot" — it does not update if you add a column
> afterwards (→ `KeyError`).

### Summary

| Function | Columns | Question it answers | Used in |
|---|---|---|---|
| `add_ema` | `ema_20`, `ema_50` | "Which way is the price going?" | TREND regime, Strategy 1 |
| `add_rsi` | `rsi_14` | "How 'hot' is the move?" | Strategies 1, 2 |
| `add_atr` | `atr_14` | "How much does the price move?" | Stop loss, position size |
| `add_adx` | `adx_14` | "How strong is the trend?" | TREND / RANGE regime |
| `add_bollinger` | `bb_upper`, `bb_middle`, `bb_lower`, `bb_width` | "What are the limits of normal movement?" | SQUEEZE regime, Strategy 2 |
| `add_volume_ma` | `volume_ma_20` | "Is anyone interested?" | Strategy 3, scoring |

---

### EMA — Exponential Moving Average

**What it is:** the average price of the last N candles, where **recent candles count more**.
On a chart it looks like a **smooth line** that filters out noise.

**Analogy:** the temperature goes up and down every day, but the weekly average tells you
whether the weather is getting warmer.

**Example:** prices 100, 101, 102, 103, 110 → simple average 103.2 · EMA ≈ 105
(the recent jump to 110 counts more).

| | EMA20 (1h) | EMA50 (4h) |
|---|---|---|
| Looks at | Recent candles — "fast" | Further back — "slow", stable |
| Question | "Did the price dip down to the 'step'?" | "Is the market going up overall?" |
| Rule | Price touches EMA20 → possible entry | Price **above** EMA50 → uptrend |

> **EMA50 = *whether* it's worth buying. EMA20 = *when*.**

---

### RSI — Relative Strength Index

**What it is:** a number **0–100**: "over the last 14 hours, did the price rise or fall more?"

**Example:** over 14 hours the up-hours added +$700 and the down-hours −$300
→ of $1,000 total movement, $700 was upward → **RSI ≈ 70**.

| RSI | Meaning |
|---|---|
| 70+ | Rose a lot, fast — "expensive" |
| 50 | Neutral |
| 30− | Fell a lot, fast — "cheap" |

**Use:** Strategy 1 wants RSI **40–55** (a mild dip) · Strategy 2 wants RSI **< 35**.

---

### ATR — Average True Range

**What it is:** the **average move of one candle, in dollars** (last 14 candles).
"True" because it also counts gaps between candles, not just `high − low`.

**Example:** ATR = $600 → in a normal hour BTC "travels" about $600.

**Use — the stop loss:** `stop = entry price − 1.5 × ATR`.
- Nervous market (large ATR) → stop further away, so normal noise does not hit it.
- Quiet market (small ATR) → stop closer.

**Example:** price $84,867, ATR $243 → stop = 84,867 − 365 = **$84,502**.

---

### ADX — Average Directional Index

**What it is:** a number **0–100** showing how **consistently** the price moves in one direction.

> ⚠️ It measures **strength, not direction.** A strong *fall* also gives a high ADX.
> That is why we always combine it with EMA50.

| ADX | Meaning | Like... |
|---|---|---|
| < 20 | No trend, sideways | A boat on a lake |
| 20–40 | There is a trend | A boat on a river |
| > 40 | Very strong trend | White-water rapids |

**ATR vs ADX:** ATR says how **big** the moves are; ADX says how **steadily** they go one way.
A market can be quiet (small ATR) yet rise steadily (ADX > 20) —
like a car driving slowly but straight ahead.

---

### Bollinger Bands

**What they are:** 3 lines around the price — a "channel" of normal movement.

```
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾  bb_upper   → "unusually high"
     /\    /\
  --/--\--/--\--/--------  bb_middle  → 20-candle average
   /    \/    \/
  ________________________  bb_lower   → "unusually low"
```

- **Middle:** simple average of 20 candles.
- **Upper/lower:** middle ± 2 "standard deviations" (the usual distance of the price from its average).
- About 95% of the time the price stays **inside** the bands.

**`bb_width` — the width:** how wide the channel is, as % of the price.
Example: upper 85,300, lower 84,300, middle 84,800 → (1,000 / 84,800) × 100 = **1.18%**.

**Why the width matters:** the market "breathes" — **after quiet comes an explosion**.
When the bands get very narrow (a squeeze), the market is "building pressure", like a pressure cooker.
We don't know **which way** it will break, but we know **something big is coming**.

---

### Volume moving average

**What it is:** the average volume of the last 20 candles — how much activity a "normal" hour has.

**Why it matters:** volume is like a **vote**. A $500 rise on 1,200 BTC of volume
(2.2× the average) is reliable; the same rise on 300 BTC (0.5×) is fragile.

**Use:** Strategy 3 (Breakout) needs volume **≥ 1.5×** the average · Scoring: volume > average → +1.

---

## 6. ③ `regime` — the market's "mood"

📄 [`src/jevtrade/regime/detector.py`](src/jevtrade/regime/detector.py)

Answers **one question**, for each coin, every hour:
**"What mood is the market in right now?"** — and that decides **which strategy** is allowed to run.

> The regime **does not compute** indicators — it only **reads** them. Station ② computes them.
> **Jev is not in here**: the detector is rules only. Jev will be added later as a separate
> step that *confirms* the regime (if it disagrees → no position). That way we can measure
> whether Jev actually helps.

### `Regime` — the 5 possible answers

| Regime | In plain words | What the bot does |
|---|---|---|
| 🌪️ **CHAOS** | Storm — BTC crashed | **Nothing** — stays out |
| 🗜️ **SQUEEZE** | Spring — the market has tightened | Strategy 3: **Breakout** |
| 📈 **TREND** | A staircase going up | Strategy 1: **Trend Pullback** |
| ↔️ **RANGE** | A ball bouncing between floor and ceiling | Strategy 2: **Range** |
| ⛔ **NONE** | Nothing suitable (e.g. a strong downtrend) | **Nothing** — waits |

It is a `StrEnum` — a closed list. A typo (e.g. `Regime.CHOAS`) fails immediately.

### `RegimeParams` — the "tuning knobs"

All thresholds in **one place**. To change behaviour, you change one line.

| Setting | Value | Meaning | If you increase it... |
|---|---|---|---|
| `chaos_drop_pct` | 5.0 | BTC drop > 5% = crash | ...CHAOS happens less often |
| `chaos_lookback_hours` | 4 | ...measured within 4 hours | |
| `chaos_cooldown_hours` | 12 | After a crash, stay out for 12 hours | ...longer "quarantine" |
| `squeeze_lookback_hours` | 720 | Compare with the last 30 days | |
| `squeeze_percentile` | 10.0 | Bands in the narrowest 10% = SQUEEZE | ...SQUEEZE happens more often |
| `trend_adx_min` | 20.0 | ADX > 20 = a real trend | ...TREND happens less often |
| `range_adx_max` | 20.0 | ADX < 20 = no clear direction | ...RANGE happens more often |
| `range_lookback_hours` | 48 | Channel over the last 48 hours | |
| `range_max_width_pct` | BTC 6 · ETH 8 · SOL 10 | Max channel height per coin | ...wider channels accepted |

`frozen=True` → values cannot be changed by accident while the bot runs.

---

### 🌪️ CHAOS — `btc_drop_pct()` + `is_chaos()`

**Question:** "Did BTC crash?"

1. **`btc_drop_pct`** — for every hour: "how many % below the **highest point of the last 4 hours** is the price?"
   (`rolling(4).max()` = a 4-candle "window" sliding over the table.)
2. **`is_chaos`** — "was there **at least one** hour in the last 12 with a drop > 5%?"

| Time | high | close | 4h high | Drop |
|---|---|---|---|---|
| 10:00 | 85,000 | 84,800 | 85,000 | 0.2% |
| 11:00 | 84,900 | 83,500 | 85,000 | 1.8% |
| 12:00 | 83,600 | 81,000 | 85,000 | 4.7% |
| 13:00 | 81,500 | **80,500** | 85,000 | **5.3%** 🌪️ |

→ CHAOS from 13:00 until 01:00, **even if the price calms down** (the "quarantine" is automatic —
the bot sees it in the candles themselves; it does not need to "remember" anything).

**Why from the high and not "4 hours ago → now"?** If BTC falls 85,000 → 80,000 and bounces to 81,500,
"before/now" gives −4.1% (not CHAOS) — but the crash **did happen**.

**Checks only BTC, applies to all coins.** When BTC crashes, it drags everything else down.

> In the ~41 days up to 2026-10-03 the largest drop was **3.08%** — the 5% threshold never fired.
> That is intended: CHAOS is for real crashes, not normal dips.

---

### 🗜️ SQUEEZE — `bb_width_percentile()` + `is_squeeze()`

**Question:** "Is the market unusually tight?"

1. **`bb_width_percentile`** — "of the 720 hours of the last month, what **percentage** had **narrower** bands than now?"
   → **0** = the narrowest of the month, **100** = the widest.
2. **`is_squeeze`** — "is it below 10?"

**Example:** only 36 of 720 hours had narrower bands → 5/100 → **SQUEEZE**.

**Each coin is compared with itself.** On 2026-10-03 SOL had a width of 1.35%
(more than double BTC's) but ranked 4/100 — for SOL, which normally moves a lot,
that was extremely narrow. A fixed threshold (e.g. "< 1%") would have missed it.

**What the bot does in SQUEEZE:** it does **not** buy. It waits for the price to close **above the ceiling**
of the quiet period with volume ≥ 1.5× (Strategy 3). If it breaks down → nothing (long only).

---

### 📈 TREND — `is_trend()`

**Question:** "Is the market going up, and seriously?" (on the **4h** chart)

| | Direction | Strength |
|---|---|---|
| Check | `close > ema_50` | `adx_14 > 20` |

**Both** must be true.
**Example:** BTC +0.89% above EMA50, ADX 25.6 → **TREND**.

---

### ↔️ RANGE — `channel_width_pct()` + `is_range()`

**Question:** "Is it moving sideways inside a channel?"

1. **`channel_width_pct`** — "how tall is the 48-hour channel?" = (highest high − lowest low) / low.
2. **`is_range`** — "ADX (4h) **< 20** (no direction) **and** channel **< the coin's limit**?"

**Example:** ETH with ADX 19.2 and a 4.77% channel (limit 8%) → **RANGE**.

**Why a different limit per coin:** SOL naturally moves about 2× more than BTC.
With a shared 6%, SOL would almost never be in RANGE.

---

### `detect_regime()` — the final decision

A **ladder** of questions: it asks in order and **stops at the first "yes"**.

```
           ┌─ CHAOS?   ── yes → 🌪️ CHAOS
           │     no
           ├─ SQUEEZE? ── yes → 🗜️ SQUEEZE
  coin ────┤     no
           ├─ TREND?   ── yes → 📈 TREND
           │     no
           ├─ RANGE?   ── yes → ↔️ RANGE
           │     no
           └─────────────────→ ⛔ NONE
```

**Why this order:**
1. **CHAOS first** — safety above everything.
2. **SQUEEZE before TREND** — it is rare and warns of a big move; also, in a squeeze the ATR is so
   small that Strategy 1's stop would be very close.
3. **TREND before RANGE** — a real trend matters more than a simple channel.
4. **NONE last** — e.g. a **strong downtrend** (price < EMA50, ADX > 20). Since we only go long, we wait.

**The order resolves "double answers":** on 2026-10-06 ETH was **both** SQUEEZE **and** RANGE →
the ladder found SQUEEZE first → result **SQUEEZE**.

**Inputs it needs:**

| Table | Must contain | For |
|---|---|---|
| `btc_1h` | Hourly **BTC** candles | CHAOS |
| `df_1h` | The coin's hourly candles + `bb_width` | SQUEEZE, RANGE channel |
| `df_4h` | The coin's 4h candles + `ema_50`, `adx_14` | TREND, RANGE's ADX |

**Example output** (`python -m jevtrade.regime.detector`, 2026-10-06):

```
BTC/USDT  chaos=False  squeeze=False  trend=True   range=False  => TREND
ETH/USDT  chaos=False  squeeze=True   trend=False  range=True   => SQUEEZE
SOL/USDT  chaos=False  squeeze=False  trend=True   range=False  => TREND
```

---

## 7. Safety rules

These always apply — no part of the code (not even Jev) can override them.

| Rule | Value |
|---|---|
| Type | **Spot** only, **long** only (buying). No leverage, no shorting |
| Risk per trade | **1%** of the account (**0.5%** for Strategy 4) |
| Max open positions | **3** |
| Max total risk | **2%** |
| −3% loss within a day | Stops until the next day |
| −8% loss within a month | Stops completely until manually restarted |
| Paper trading fees | **0.1%** per side + slippage |

---

## 8. Glossary

| Term | In plain words |
|---|---|
| **Spot** | You buy the actual coin with your own money (no borrowing) |
| **Long** | You buy hoping the price will rise |
| **Paper trading** | Trading with virtual money, for testing |
| **Stop loss** | The price where we sell to limit the loss |
| **Take profit** | The target price where we sell with a profit |
| **R** | The unit of risk: distance from entry to stop. Stop $900 below → 1R = $900, 2R = $1,800 |
| **Breakeven** | After a 1R profit, the stop moves up to the entry price → we can no longer lose |
| **Slippage** | The small difference between the price you see and the price you actually get |
| **Pullback** | A small dip inside an uptrend |
| **Breakout** | The price "breaks" above a level that was holding it down |
| **Squeeze** | A period of unusual calm — the market is "building pressure" |
| **Funding rate** | A fee leveraged traders pay each other. Very positive = "everyone bought with borrowed money" → risky |
| **Fear & Greed** | A 0–100 index of market sentiment. < 20 = extreme fear |
| **UTC** | The global reference time — the whole bot works in UTC |

---

## 9. Where we are

| # | Step | Status |
|---|---|---|
| 1 | Data (candles from Binance) | ✅ |
| 2 | Indicators (EMA, RSI, ATR, ADX, Bollinger, volume) | ✅ |
| 3 | Regime detector (CHAOS, SQUEEZE, TREND, RANGE, NONE) | ✅ |
| 4 | Strategy 1 — Trend Pullback | ⬜ next |
| 5 | Risk manager + paper trading | ⬜ |
| 6 | Jev (mock first, then the real API) | ⬜ |
| 7 | Strategies 2, 3, 4 | ⬜ |
| 8 | Extra data (funding rate, Fear & Greed, news) | ⬜ |
| 9 | Scoring and exit rules | ⬜ |
| 10 | Storage (PostgreSQL) + Docker | ⬜ |
| 11 | Metrics: "rules only" vs "rules + Jev" | ⬜ |
| 12 | Telegram alerts, Grafana | ⬜ |

### Open decisions

- **Position larger than the account:** in a quiet market (small ATR → very close stop), the sizing
  maths can produce a position bigger than the available money. → Step 5.
- **Open positions at −3% / −8%:** are they closed, or left with their stops? → Step 5.
- **"Touched EMA20":** how close counts as "touched"? → Step 4.
