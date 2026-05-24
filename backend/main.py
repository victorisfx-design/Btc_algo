"""
BTC Options Breakout Algo — Backend Engine
Delta Exchange | Python FastAPI + WebSocket
Strategy: 2 strikes | 10 min confirmation | TP=user-defined | SL=50% of entry
Auto-detects Delta Exchange account on startup.
Run: python main.py
"""

import asyncio, json, os, sqlite3, time, hmac, hashlib, random
from datetime import datetime, timezone
from typing import Optional
import aiohttp, websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# ── CONFIGURATION ─────────────────────────────────────────────────────────
API_KEY    = "iiu3aJNuen38GAPAbvMccMjrpYDJ6e"
API_SECRET = "SQp0jPormIyaxUIc1qGf545zt5LNijVZm2R0cL7tekJ6DDZeVtP9PxmY9pA4"

DELTA_REST_URL  = "https://api.delta.exchange"
DELTA_WS_URL    = "wss://socket.delta.exchange"
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR    = os.path.join(BASE_DIR, "..", "frontend")
DATA_DIR        = os.path.join(BASE_DIR, "..", "data")
DB_PATH         = os.path.join(DATA_DIR, "trades.db")
CONFIRM_SECONDS = 10 * 60   # 10 minutes confirmation
SL_MULTIPLIER   = 0.5       # SL = 50% of entry price

app = FastAPI(title="BTC Options Algo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── DATABASE ──────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT,
            strike       TEXT,
            side         TEXT,
            entry_price  REAL,
            exit_price   REAL,
            tp_price     REAL,
            sl_price     REAL,
            points       REAL,
            result       TEXT,
            balance_after REAL,
            notes        TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id      INTEGER PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 100000
        )
    """)
    c.execute("INSERT OR IGNORE INTO account (id, balance) VALUES (1, 100000)")
    conn.commit()
    conn.close()


def get_balance():
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute("SELECT balance FROM account WHERE id=1").fetchone()
    conn.close()
    return row[0] if row else 100000.0


def update_balance(b):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE account SET balance=? WHERE id=1", (b,))
    conn.commit()
    conn.close()


def save_trade(t):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO trades
            (timestamp, strike, side, entry_price, exit_price,
             tp_price, sl_price, points, result, balance_after, notes)
        VALUES
            (:timestamp, :strike, :side, :entry_price, :exit_price,
             :tp_price, :sl_price, :points, :result, :balance_after, :notes)
    """, t)
    conn.commit()
    conn.close()


def get_trades(limit=100):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    cols = [
        "id", "timestamp", "strike", "side", "entry_price", "exit_price",
        "tp_price", "sl_price", "points", "result", "balance_after", "notes"
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_stats():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT result, points FROM trades").fetchall()
    conn.close()
    total = len(rows)
    wins  = sum(1 for r in rows if r[0] == "PROFIT")
    pts   = sum(r[1] for r in rows if r[1] is not None)
    return {
        "total":        total,
        "wins":         wins,
        "losses":       total - wins,
        "win_rate":     round(wins / total * 100, 1) if total else 0,
        "total_points": round(pts, 2)
    }


# ── DELTA ACCOUNT DETECTION ───────────────────────────────────────────────
account_info = {
    "name": "Unknown", "email": "",
    "delta_balance": None, "detected": False
}


def make_signature(method: str, path: str, payload: str = "") -> dict:
    ts  = str(int(time.time()))
    msg = f"{method}{ts}{path}{payload}"
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "api-key":      API_KEY,
        "timestamp":    ts,
        "signature":    sig,
        "Content-Type": "application/json"
    }


async def detect_account():
    global account_info

    # --- Profile ---
    try:
        path    = "/v2/profile"
        headers = make_signature("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(
                DELTA_REST_URL + path, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    data = (await r.json()).get("result", {})
                    account_info["name"]     = (
                        data.get("name") or data.get("username") or "Trader"
                    )
                    account_info["email"]    = data.get("email", "")
                    account_info["detected"] = True
                    print(f"✅ Account: {account_info['name']} ({account_info['email']})")
    except Exception as e:
        print(f"[ACCOUNT] {e}")

    # --- Wallet balance ---
    try:
        path    = "/v2/wallet/balances"
        headers = make_signature("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(
                DELTA_REST_URL + path, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    result = (await r.json()).get("result", [])
                    for asset in result:
                        sym = asset.get("asset_symbol", "")
                        if sym in ("USDT", "USD", "INR"):
                            bal = float(asset.get("available_balance", 0) or 0)
                            if bal > 0:
                                account_info["delta_balance"] = bal
                                update_balance(bal)
                                print(f"✅ Balance synced: {sym} {bal:,.2f}")
                                break
    except Exception as e:
        print(f"[WALLET] {e}")


# ── ALGO STATE ────────────────────────────────────────────────────────────
class AlgoState:
    def reset(self):
        self.running       = False
        self.status        = "IDLE"
        self.config        = {}
        self.prices        = {"strike1": None, "strike2": None}
        self.contracts     = {"strike1": None, "strike2": None}
        self.breakout_side = None
        self.breakout_time = None
        self.position      = None
        self.logs          = []

    def __init__(self):
        self.reset()

    def log(self, msg):
        self.logs.insert(0, {
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "msg":  msg
        })
        self.logs = self.logs[:100]


state   = AlgoState()
clients: list[WebSocket] = []


async def broadcast(data):
    dead = []
    for ws in clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for d in dead:
        clients.remove(d)


async def push_state():
    elapsed = int(time.time() - state.breakout_time) if state.breakout_time else 0
    await broadcast({
        "type":              "state",
        "status":            state.status,
        "running":           state.running,
        "prices":            state.prices,
        "config":            state.config,
        "position":          state.position,
        "balance":           get_balance(),
        "breakout_side":     state.breakout_side,
        "confirm_elapsed":   elapsed,
        "confirm_remaining": max(0, CONFIRM_SECONDS - elapsed),
        "confirm_total":     CONFIRM_SECONDS,
        "logs":              state.logs[:20],
        "stats":             get_stats(),
        "account":           account_info,
        "ts":                datetime.now(timezone.utc).isoformat()
    })


# ── DELTA EXCHANGE HELPERS ────────────────────────────────────────────────
async def fetch_contract(strike, opt_type):
    url = (
        f"{DELTA_REST_URL}/v2/products"
        f"?contract_type=put_options,call_options&state=live&page_size=200"
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    products = (await r.json()).get("result", [])
                    ctype = "call_options" if opt_type.upper() == "CE" else "put_options"
                    sv    = int(float(strike))
                    for p in products:
                        if (
                            p.get("contract_type") == ctype
                            and "BTC" in p.get("underlying_asset", {}).get("symbol", "")
                            and int(float(p.get("strike_price", 0))) == sv
                        ):
                            return p
    except Exception as e:
        print(f"[API] fetch_contract: {e}")
    return None


async def place_order(product_id, side):
    path    = "/v2/orders"
    payload = json.dumps({
        "product_id": product_id,
        "size":       1,
        "side":       side,
        "order_type": "market_order"
    })
    headers = make_signature("POST", path, payload)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                DELTA_REST_URL + path, headers=headers, data=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                return await r.json()
    except Exception as e:
        return {"error": str(e)}


# ── PRICE SIMULATION (fallback when no live contracts) ────────────────────
async def simulate_feed():
    cfg  = state.config
    b1   = float(cfg.get("level1", 575))
    b2   = float(cfg.get("level2", 547))
    tick = 0
    while state.running:
        tick += 1
        if tick % 45 == 0:
            b1 += random.choice([-1, 1]) * random.uniform(15, 50)
            b2 += random.choice([-1, 1]) * random.uniform(15, 50)
        else:
            b1 += random.uniform(-3, 3)
            b2 += random.uniform(-3, 3)
        b1 = max(5, b1)
        b2 = max(5, b2)
        state.prices["strike1"] = round(b1, 1)
        state.prices["strike2"] = round(b2, 1)
        await check_breakout()
        await push_state()
        await asyncio.sleep(2)


# ── LIVE WEBSOCKET PRICE FEED ─────────────────────────────────────────────
async def live_feed():
    syms = [c["symbol"] for k, c in state.contracts.items() if c]
    if not syms:
        state.log("⚠️  No live contracts — running simulation mode")
        await simulate_feed()
        return

    sub = {
        "type":    "subscribe",
        "payload": {"channels": [{"name": "ticker", "symbols": syms}]}
    }
    try:
        async with websockets.connect(DELTA_WS_URL, ping_interval=20) as ws:
            await ws.send(json.dumps(sub))
            state.log(f"📡 Live feed connected — {', '.join(syms)}")
            while state.running:
                try:
                    msg   = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    if msg.get("type") == "ticker":
                        sym   = msg.get("symbol", "")
                        price = float(msg.get("mark_price") or msg.get("close") or 0)
                        if price:
                            for k in ("strike1", "strike2"):
                                c = state.contracts.get(k)
                                if c and sym == c.get("symbol"):
                                    state.prices[k] = price
                        await check_breakout()
                        await push_state()
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"[WS] {e} — falling back to simulation")
        state.log("⚠️  WS disconnected — switching to simulation")
        await simulate_feed()


# ── BREAKOUT ENGINE ───────────────────────────────────────────────────────
async def check_breakout():
    if state.status == "TRADING":
        await monitor_position()
        return
    if state.status not in ("MONITORING", "CONFIRMING"):
        return

    cfg = state.config
    p1  = state.prices.get("strike1")
    p2  = state.prices.get("strike2")
    lv1 = float(cfg.get("level1", 0))
    lv2 = float(cfg.get("level2", 0))
    now = time.time()

    if state.status == "MONITORING":
        candidates = [
            ("strike1", p1, lv1, cfg.get("strike1", ""), cfg.get("type1", "")),
            ("strike2", p2, lv2, cfg.get("strike2", ""), cfg.get("type2", ""))
        ]
        for key, price, level, s_strike, s_type in candidates:
            if price and level and abs(price - level) / level > 0.03:
                state.breakout_side = key
                state.breakout_time = now
                state.status        = "CONFIRMING"
                msg = (
                    f"⚡ BREAKOUT — {s_strike} {s_type} @ {price} "
                    f"(Level: {level}). Confirming 10 min..."
                )
                state.log(msg)
                await broadcast({"type": "log", "msg": msg})
                break

    elif state.status == "CONFIRMING":
        bs    = state.breakout_side
        price = p1 if bs == "strike1" else p2
        level = lv1 if bs == "strike1" else lv2

        # Price returned inside level — cancel
        if price and level and abs(price - level) / level < 0.01:
            state.status        = "MONITORING"
            state.breakout_side = None
            state.breakout_time = None
            msg = "↩️  Price returned inside level — cancelled. Back to monitoring."
            state.log(msg)
            await broadcast({"type": "log", "msg": msg})
            return

        # Confirmation timer elapsed — fire trade
        if now - state.breakout_time >= CONFIRM_SECONDS:
            await fire_trade()


async def fire_trade():
    cfg = state.config
    bs  = state.breakout_side

    if bs == "strike1":
        tkey  = "strike2"
        broke = f"{cfg.get('strike1','')} {cfg.get('type1','')}"
        label = f"{cfg.get('strike2','')} {cfg.get('type2','')}"
        ep    = state.prices.get("strike2") or float(cfg.get("level2", 100))
    else:
        tkey  = "strike1"
        broke = f"{cfg.get('strike2','')} {cfg.get('type2','')}"
        label = f"{cfg.get('strike1','')} {cfg.get('type1','')}"
        ep    = state.prices.get("strike1") or float(cfg.get("level1", 100))

    tp = float(cfg.get("tp_price", round(ep * 2.0, 2)))   # User-defined TP
    sl = round(ep * SL_MULTIPLIER, 2)                      # Auto SL = 50%

    order = {"status": "simulation"}
    c     = state.contracts.get(tkey)
    if API_KEY and c:
        order = await place_order(c["id"], "buy")

    state.position = {
        "label":        label,
        "broke_label":  broke,
        "trade_key":    tkey,
        "entry_price":  ep,
        "current_price": ep,
        "tp":           tp,
        "sl":           sl,
        "open_time":    datetime.now(timezone.utc).isoformat(),
        "product_id":   c["id"] if c else None,
        "pnl":          0.0,
        "order":        order
    }
    state.status = "TRADING"
    msg = f"🟢 TRADE PLACED → BUY {label} @ {ep} | TP: {tp} | SL: {sl}"
    state.log(msg)
    await broadcast({"type": "log", "msg": msg})


async def monitor_position():
    pos = state.position
    if not pos:
        return
    current              = state.prices.get(pos["trade_key"]) or pos["entry_price"]
    pos["current_price"] = current
    pos["pnl"]           = round(current - pos["entry_price"], 2)
    if current >= pos["tp"]:
        await exit_trade("TP HIT", current)
    elif current <= pos["sl"]:
        await exit_trade("SL HIT", current)


async def exit_trade(reason, exit_price):
    pos     = state.position
    pts     = round(exit_price - pos["entry_price"], 2)
    bal     = get_balance()
    new_bal = round(bal + pts, 2)
    update_balance(new_bal)
    result = "PROFIT" if pts >= 0 else "LOSS"

    save_trade({
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "strike":        pos["label"],
        "side":          "BUY",
        "entry_price":   pos["entry_price"],
        "exit_price":    exit_price,
        "tp_price":      pos["tp"],
        "sl_price":      pos["sl"],
        "points":        pts,
        "result":        result,
        "balance_after": new_bal,
        "notes":         f"{reason} | Triggered by: {pos.get('broke_label', '')}"
    })

    if API_KEY and pos.get("product_id"):
        await place_order(pos["product_id"], "sell")

    emoji = "🟢" if pts >= 0 else "🔴"
    msg   = (
        f"{emoji} EXIT [{reason}] {pos['label']} @ {exit_price} "
        f"| Pts: {pts:+.1f} | Bal: ₹{new_bal:,.0f}"
    )
    state.log(msg)
    await broadcast({"type": "log", "msg": msg})

    state.position      = None
    state.breakout_side = None
    state.breakout_time = None
    state.status        = "MONITORING"

    await push_state()
    await broadcast({
        "type":    "history",
        "trades":  get_trades(100),
        "balance": new_bal,
        "stats":   get_stats()
    })


# ── API MODELS ────────────────────────────────────────────────────────────
class AlgoConfig(BaseModel):
    strike1:  str
    type1:    str
    level1:   float
    strike2:  str
    type2:    str
    level2:   float
    tp_price: float
    balance:  Optional[float] = None


# ── ROUTES ────────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.post("/api/start")
async def start(cfg: AlgoConfig):
    if state.running:
        return {"ok": False, "msg": "Already running"}
    state.reset()
    state.config = cfg.dict()
    if cfg.balance:
        update_balance(cfg.balance)
    state.contracts["strike1"] = await fetch_contract(cfg.strike1, cfg.type1)
    state.contracts["strike2"] = await fetch_contract(cfg.strike2, cfg.type2)
    state.running = True
    state.status  = "MONITORING"
    asyncio.create_task(live_feed())
    msg = (
        f"🚀 Started | {cfg.strike1} {cfg.type1} @ {cfg.level1} | "
        f"{cfg.strike2} {cfg.type2} @ {cfg.level2} | TP: {cfg.tp_price}"
    )
    state.log(msg)
    await push_state()
    return {"ok": True, "msg": "Algo started"}


@app.post("/api/stop")
async def stop():
    state.running = False
    state.status  = "IDLE"
    state.log("⛔ Algo stopped by user")
    await push_state()
    return {"ok": True}


@app.get("/api/history")
async def history():
    return {
        "trades":  get_trades(100),
        "balance": get_balance(),
        "stats":   get_stats(),
        "account": account_info
    }


@app.post("/api/balance")
async def set_bal(data: dict):
    update_balance(float(data.get("balance", 100000)))
    return {"ok": True, "balance": get_balance()}


@app.websocket("/ws")
async def ws_ep(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    await push_state()
    await ws.send_json({
        "type":    "history",
        "trades":  get_trades(100),
        "balance": get_balance(),
        "stats":   get_stats()
    })
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)


# ── STARTUP ───────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    await detect_account()
    print("=" * 55)
    print("  BTC Options Algo — Delta Exchange")
    print("=" * 55)
    print(f"  Account  : {account_info['name']} ({account_info['email']})")
    print(f"  Balance  : {get_balance():,.2f}")
    print(f"  Confirm  : 10 minutes")
    print(f"  SL       : 50% of entry (auto)")
    print(f"  TP       : User-defined per trade")
    print(f"  Dashboard: http://localhost:8000")
    print("=" * 55)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
