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

# True = Virtual Paper Trading Mode (Safe Testing)
# False = Live Real Money Execution Mode
PAPER_TRADE_MODE = True

# ==============================================================================
# 2. MASTER STRATEGY SETTINGS (LOT SIZE 65 | MAX 4 TRADES | IST TIMEZONE)
# ==============================================================================
IST = pytz.timezone('Asia/Kolkata')
LOT_SIZE = 65
TOTAL_QTY = LOT_SIZE * 2
MAX_DAILY_TRADES = 4

app = Flask(__name__)

@app.route('/')
def status_page():
    return "FYERS Master Algo Engine is LIVE and Running 24/7!"

def get_ist_now():
    return datetime.datetime.now(IST)

def encode_str(string):
    return base64.b64encode(str(string).encode("ascii")).decode("ascii")

# ==============================================================================
# 3. 100% DYNAMIC SYMBOL & EXPIRY FINDER (PURE PYTHON - NO PANDAS)
# ==============================================================================
def get_active_nifty_contracts():
    while True:
        try:
            url = "https://fyers.in"
            resp = requests.get(url, timeout=15)
            reader = csv.reader(io.StringIO(resp.text))
            
            now_ts = int(time.time())
            futures = []
            options = []
            
            for row in reader:
                if len(row) > 16 and row[13] == "NIFTY":
                    exp_ts = int(row[8])
                    if exp_ts > now_ts:
                        if row[3] == "FUTIDX":
                            futures.append((exp_ts, row[9]))
                        elif row[3] == "OPTIDX":
                            options.append((exp_ts, float(row[15]), row[16], row[9]))
                            
            futures.sort(key=lambda x: x[0])
            active_fut_symbol = futures[0][1]
            
            options.sort(key=lambda x: x[0])
            nearest_opt_exp = options[0][0]
            current_week_opts = [opt for opt in options if opt[0] == nearest_opt_exp]
            
            print(f"Auto-Detected Active Future: {active_fut_symbol}")
            return active_fut_symbol, current_week_opts
        except Exception as e:
            print(f"Retrying Symbol Master Download: {e}")
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
    try:
        ses = requests.Session()
        r1 = ses.post("https://fyers.in", json={"fy_id": encode_str(FY_ID), "app_id": "2"}).json()
        req_key = r1["request_key"]
        
        totp = pyotp.TOTP(TOTP_KEY).now()
        r2 = ses.post("https://fyers.in", json={"request_key": req_key, "otp": totp}).json()
        req_key2 = r2["request_key"]
        
        r3 = ses.post("https://fyers.in", json={"request_key": req_key2, "identity_type": "pin", "identifier": encode_str(PIN)}).json()
        ses.headers.update({'authorization': f"Bearer {r3['data']['access_token']}"})
        
        app_prefix = CLIENT_ID.split("-")[0]
        payload = {"fyers_id": FY_ID, "app_id": app_prefix, "redirect_uri": REDIRECT_URI, "appType": "100", "code_challenge": "", "state": "None", "scope": "", "nonce": "", "response_type": "code", "create_cookie": True}
        r4 = ses.post("https://fyers.in", json=payload).json()
        auth_code = parse_qs(urlparse(r4['Url']).query)['auth_code'][0]
        
        app_id_hash = hashlib.sha256(f"{CLIENT_ID}:{SECRET_KEY}".encode()).hexdigest()
        token_resp = requests.post("https://fyers.in", json={
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code
        }).json()
        
        if "access_token" in token_resp:
            print(f"[{get_ist_now().strftime('%H:%M:%S')}] Auto-Login Successful! Mode: {'PAPER TRADE' if PAPER_TRADE_MODE else 'REAL LIVE'}")
            return token_resp["access_token"]
        else:
            print(f"Token Error: {token_resp}")
    except Exception as e:
        print(f"Auto-Login Failed: {e}")
    return None

# ==============================================================================
# 5. PURE PYTHON INDICATORS & API FUNCTIONS
# ==============================================================================
def calc_ema(prices, period):
    ema = []
    k = 2 / (period + 1)
    for i, p in enumerate(prices):
        if i == 0:
            ema.append(p)
        else:
            ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calculate_indicators(candles):
    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    vols = [c[5] for c in candles]
    
    ema9 = calc_ema(closes, 9)
    ema15 = calc_ema(closes, 15)
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd = [m - s for m, s in zip(ema12, ema26)]
    signal = calc_ema(macd, 9)
    
    cum_pv, cum_v, vwap = 0.0, 0.0, []
    for c, v in zip(closes, vols):
        cum_pv += c * v
        cum_v += v
        vwap.append(cum_pv / cum_v if cum_v > 0 else c)
        
    trs = []
    for i in range(len(candles)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
            
    st_green = []
    for i in range(len(candles)):
        if i < 10:
            st_green.append(True)
        else:
            atr = sum(trs[i-9:i+1]) / 10.0
            hl2 = (highs[i] + lows[i]) / 2.0
            st_green.append(closes[i] > (hl2 - 2 * atr))
            
    return ema9, ema15, macd, signal, vwap, st_green

def fetch_5m_candles(token, symbol):
    today_str = get_ist_now().strftime("%Y-%m-%d")
    yesterday_str = (get_ist_now() - datetime.timedelta(days=4)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"{CLIENT_ID}:{token}"}
    params = {"symbol": symbol, "resolution": "5", "date_format": "1", "range_from": yesterday_str, "range_to": today_str, "cont_flag": "1"}
    res = requests.get("https://fyers.in", headers=headers, params=params).json()
    if res and res.get('s') == 'ok' and len(res.get('candles', [])) > 0:
        return res['candles']
    return None

def get_option_ltp(token, opt_symbol):
    headers = {"Authorization": f"{CLIENT_ID}:{token}"}
    res = requests.get("https://fyers.in", headers=headers, params={"symbols": opt_symbol}).json()
    if res and res.get('s') == 'ok' and len(res.get('d', [])) > 0:
        return float(res['d'][0]['v']['lp'])
    return 0.0

def execute_fyers_order(token, opt_symbol, side, qty, reason):
    if not opt_symbol:
        return False
    t_now = get_ist_now().strftime("%H:%M:%S")
    ltp = get_option_ltp(token, opt_symbol)
    print(f"[{t_now} IST] {side} | {opt_symbol} | Qty: {qty} | Option LTP: {ltp} | Reason: {reason}")
    
    if PAPER_TRADE_MODE:
        return True
        
    headers = {"Authorization": f"{CLIENT_ID}:{token}", "Content-Type": "application/json"}
    order_data = {"symbol": opt_symbol, "qty": qty, "type": 2, "side": 1 if side == "BUY" else -1, "productType": "INTRADAY", "limitPrice": 0, "stopPrice": 0, "validity": "DAY", "disclosedQty": 0, "offlineOrder": False}
    resp = requests.post("https://fyers.in", headers=headers, json=order_data).json()
    print(f"Broker Order Response: {resp}")
    return resp.get('s') == 'ok'

# ==============================================================================
# 6. BACKGROUND TRADING ENGINE
# ==============================================================================
def run_algo_engine():
    access_token = None
    last_login_date = None
    nifty_fut_symbol = ""
    weekly_opt_list = None

    position = None
    active_option = ""
    trade_count = 0
    lot1_exited = False
    range_5m_locked = False
    first_5m_high, first_5m_low = 0.0, 0.0
    prev_day_high, prev_day_low = 0.0, 0.0
    is_gap_day = False

    print("Cloud Engine Started! Ultra-Fast Pure Python Mode Active...")

    while True:
        now_dt = get_ist_now()
        now = now_dt.time()
        today_date = now_dt.date()

        if now_dt.weekday() >= 5:
            time.sleep(60)
            continue

        if now >= datetime.time(9, 5) and last_login_date != today_date and now < datetime.time(15, 15):
            access_token = fyers_auto_login()
            if access_token:
                nifty_fut_symbol, weekly_opt_list = get_active_nifty_contracts()
                last_login_date = today_date
                position = None
                trade_count = 0
                range_5m_locked = False
            else:
                time.sleep(30)
                continue

        if access_token is None or now < datetime.time(9, 15) or now > datetime.time(15, 20):
            time.sleep(10)
