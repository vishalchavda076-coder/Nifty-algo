import os
import time
import datetime
import threading
import csv
import io
import pytz
import pyotp
import requests
import base64
import hashlib
from operator import itemgetter
from urllib.parse import parse_qs, urlparse
from flask import Flask

# ==============================================================================
# 1. FYERS AUTO-LOGIN CREDENTIALS
# ==============================================================================
FY_ID = "FAA22422"
PIN = "5747"
TOTP_KEY = "6L7JXLEFKGAU562T7MGCU5NLZZ5ITBXR"

CLIENT_ID = "FAA22422-100"
SECRET_KEY = "XCFJZS7OFF"
REDIRECT_URI = "https://fyers.in"

PAPER_TRADE_MODE = True

# ==============================================================================
# 2. MASTER STRATEGY SETTINGS (LOT SIZE 65 | MAX 4 TRADES | IST TIMEZONE)
# ==============================================================================
IST = pytz.timezone('Asia/Kolkata')
LOT_SIZE = 65
TOTAL_QTY = LOT_SIZE * 2
MAX_DAILY_TRADES = 4

app = Flask(__name__)
live_status_logs = list()

def add_log(msg):
    t_str = datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
    full_msg = f"[{t_str} IST] {msg}"
    print(full_msg, flush=True)
    live_status_logs.append(full_msg)
    if len(live_status_logs) > 60:
        live_status_logs.pop(0)

@app.route('/')
def status_page():
    logs_html = "<br>".join(reversed(live_status_logs)) if live_status_logs else "Engine Starting... Refresh in 10 seconds."
    return f"<html><body style='font-family:monospace; padding:15px; background:#111; color:#0f0;'><h3>🚀 FYERS Master Algo Live Dashboard</h3><hr>{logs_html}</body></html>"

def get_ist_now():
    return datetime.datetime.now(IST)

def encode_str(string):
    return base64.b64encode(str(string).encode("ascii")).decode("ascii")

# ==============================================================================
# 3. 100% DYNAMIC SYMBOL & EXPIRY FINDER
# ==============================================================================
def get_active_nifty_contracts():
    get_inst = itemgetter(3)
    get_exp = itemgetter(8)
    get_sym = itemgetter(9)
    get_und = itemgetter(13)
    get_strk = itemgetter(15)
    get_opt = itemgetter(16)
    first = itemgetter(0)
    second = itemgetter(1)

    while True:
        try:
            url = "https://fyers.in"
            resp = requests.get(url, timeout=15)
            reader = csv.reader(io.StringIO(resp.text))
            
            now_ts = int(time.time())
            futures = list()
            options = list()
            
            for row in reader:
                if len(row) > 16 and get_und(row) == "NIFTY":
                    exp_ts = int(get_exp(row))
                    if exp_ts > now_ts:
                        inst = get_inst(row)
                        sym = get_sym(row)
                        if inst == "FUTIDX":
                            futures.append((exp_ts, sym))
                        elif inst == "OPTIDX":
                            options.append((exp_ts, float(get_strk(row)), get_opt(row), sym))
                            
            futures.sort(key=first)
            active_fut_symbol = second(first(futures))
            
            options.sort(key=first)
            nearest_opt_exp = first(first(options))
            current_week_opts = list(filter(lambda x: first(x) == nearest_opt_exp, options))
            
            add_log(f"📅 Active Future Selected: {active_fut_symbol}")
            return active_fut_symbol, current_week_opts
        except Exception as e:
            add_log(f"⚠️ Retrying Symbol Master: {e}")
            time.sleep(5)

def find_exact_option_symbol(opt_list, strike_price, opt_type):
    if not opt_list:
        return None
    for exp_ts, strike, o_type, sym in opt_list:
        if strike == float(strike_price) and o_type == opt_type:
            return sym
    return None

# ==============================================================================
# 4. DIRECT FYERS API AUTO-LOGIN
# ==============================================================================
def fyers_auto_login():
    first = itemgetter(0)
    try:
        ses = requests.Session()
        r1 = ses.post("https://fyers.in", json={"fy_id": encode_str(FY_ID), "app_id": "2"}).json()
        req_key = r1.get("request_key")
        
        totp = pyotp.TOTP(TOTP_KEY).now()
        r2 = ses.post("https://fyers.in", json={"request_key": req_key, "otp": totp}).json()
        req_key2 = r2.get("request_key")
        
        r3 = ses.post("https://fyers.in", json={"request_key": req_key2, "identity_type": "pin", "identifier": encode_str(PIN)}).json()
        ses.headers.update({'authorization': f"Bearer {r3.get('data').get('access_token')}"})
        
        app_prefix = first(CLIENT_ID.split("-"))
        payload = {"fyers_id": FY_ID, "app_id": app_prefix, "redirect_uri": REDIRECT_URI, "appType": "100", "code_challenge": "", "state": "None", "scope": "", "nonce": "", "response_type": "code", "create_cookie": True}
        r4 = ses.post("https://fyers.in", json=payload).json()
        auth_code = first(parse_qs(urlparse(r4.get('Url')).query).get('auth_code'))
        
        app_id_hash = hashlib.sha256(f"{CLIENT_ID}:{SECRET_KEY}".encode()).hexdigest()
        token_resp = requests.post("https://fyers.in", json={
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code
        }).json()
        
        if "access_token" in token_resp:
            add_log(f"✅ Auto-Login Successful! Mode: {'PAPER TRADE' if PAPER_TRADE_MODE else 'REAL LIVE'}")
            return token_resp.get("access_token")
        else:
            add_log(f"❌ Token Error: {token_resp}")
    except Exception as e:
        add_log(f"❌ Auto-Login Failed: {e}")
    return None

# ==============================================================================
# 5. PURE PYTHON INDICATORS & API FUNCTIONS
# ==============================================================================
def calc_ema(prices, period):
    ema = list()
    k = 2 / (period + 1)
    prev_ema = 0.0
    for i, p in enumerate(prices):
        if i == 0:
            prev_ema = p
        else:
            prev_ema = p * k + prev_ema * (1 - k)
        ema.append(prev_ema)
    return ema

def calculate_indicators(candles):
    closes = list(map(itemgetter(4), candles))
    highs = list(map(itemgetter(2), candles))
    lows = list(map(itemgetter(3), candles))
    vols = list(map(itemgetter(5), candles))
    
    ema9 = calc_ema(closes, 9)
    ema15 = calc_ema(closes, 15)
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd = list(map(lambda x: x - x, zip(ema12, ema26)))
    signal = calc_ema(macd, 9)
    
    cum_pv, cum_v, vwap = 0.0, 0.0, list()
    for c, v in zip(closes, vols):
        cum_pv += c * v
        cum_v += v
        vwap.append(cum_pv / cum_v if cum_v > 0 else c)
        
    trs = list()
    prev_c = 0.0
    for i, (h, l, c) in enumerate(zip(highs, lows, closes)):
        if i == 0:
            trs.append(h - l)
        else:
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        prev_c = c
            
    st_green = list()
    tr_window = list()
    for h, l, c, tr in zip(highs, lows, closes, trs):
        tr_window.append(tr)
        if len(tr_window) > 10:
            tr_window.pop(0)
        if len(tr_window) < 10:
            st_green.append(True)
        else:
            atr = sum(tr_window) / 10.0
            hl2 = (h + l) / 2.0
            st_green.append(c > (hl2 - 2 * atr))
            
    return ema9, ema15, macd, signal, vwap, st_green

def fetch_5m_candles(token, symbol):
    today_str = get_ist_now().strftime("%Y-%m-%d")
    yesterday_str = (get_ist_now() - datetime.timedelta(days=4)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"{CLIENT_ID}:{token}"}
    params = {"symbol": symbol, "resolution": "5", "date_format": "1", "range_from": yesterday_str, "range_to": today_str, "cont_flag": "1"}
    res = requests.get("https://fyers.in", headers=headers, params=params).json()
    if res and res.get('s') == 'ok' and len(res.get('candles', list())) > 0:
        return res.get('candles')
    return None

def get_option_ltp(token, opt_symbol):
    first = itemgetter(0)
    headers = {"Authorization": f"{CLIENT_ID}:{token}"}
    res = requests.get("https://fyers.in", headers=headers, params={"symbols": opt_symbol}).json()
    if res and res.get('s') == 'ok' and len(res.get('d', list())) > 0:
        first_item = first(res.get('d'))
        return float(first_item.get('v').get('lp'))
    return 0.0

def execute_fyers_order(token, opt_symbol, side, qty, reason):
    if not opt_symbol:
        return False
    ltp = get_option_ltp(token, opt_symbol)
    add_log(f"🎯 {side} | {opt_symbol} | Qty: {qty} | Option LTP: ₹{ltp} | {reason}")
    
    if PAPER_TRADE_MODE:
        return True
        
    headers = {"Authorization": f"{CLIENT_ID}:{token}", "Content-Type": "application/json"}
    order_data = {"symbol": opt_symbol, "qty": qty, "type": 2, "side": 1 if side == "BUY" else -1, "productType": "INTRADAY", "limitPrice": 0, "stopPrice": 0, "validity": "DAY", "disclosedQty": 0, "offlineOrder": False}
    resp = requests.post("https://fyers.in", headers=headers, json=order_data).json()
    add_log(f"Broker Response: {resp}")
    return resp.get('s') == 'ok'

# ==============================================================================
# 6. BACKGROUND TRADING ENGINE
# ==============================================================================
def run_algo_engine():
    first = itemgetter(0)
    last = itemgetter(-1)
    second_last = itemgetter(-2)
    get_high = itemgetter(2)
