import time
import datetime
import pytz
import pyotp
import requests
import base64
import pandas as pd
import numpy as np
from urllib.parse import parse_qs, urlparse
from fyers_apiv3 import fyersModel

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

def get_ist_now():
    return datetime.datetime.now(IST)

def encode_str(string):
    return base64.b64encode(str(string).encode("ascii")).decode("ascii")

# ==============================================================================
# 3. 100% DYNAMIC SYMBOL & EXPIRY FINDER (NO HARDCODED DATES)
# ==============================================================================
def get_active_nifty_contracts():
    while True:
        try:
            url = "https://fyers.in"
            df_sym = pd.read_csv(url, header=None)
            nifty_df = df_sym[df_sym == "NIFTY"].copy()
            now_ts = int(time.time())
            nifty_df = nifty_df[nifty_df > now_ts].sort_values(by=8)
            
            fut_df = nifty_df[nifty_df == "FUTIDX"]
            active_fut_symbol = fut_df.iloc
            
            opt_df = nifty_df[nifty_df == "OPTIDX"]
            nearest_opt_expiry = opt_df.iloc
            current_week_opts = opt_df[opt_df == nearest_opt_expiry]
            
            print(f"Auto-Detected Active Future: {active_fut_symbol}")
            return active_fut_symbol, current_week_opts
        except Exception as e:
            print(f"Retrying Symbol Master Download: {e}")
            time.sleep(5)

def find_exact_option_symbol(opt_df, strike_price, opt_type):
    match = opt_df[(opt_df == float(strike_price)) & (opt_df == opt_type)]
    if not match.empty:
        return match.iloc
    return None

# ==============================================================================
# 4. AUTOMATIC TOTP LOGIN FUNCTION
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
        
        payload = {"fyers_id": FY_ID, "app_id": CLIENT_ID.split("-"), "redirect_uri": REDIRECT_URI, "appType": "100", "code_challenge": "", "state": "None", "scope": "", "nonce": "", "response_type": "code", "create_cookie": True}
        r4 = ses.post("https://fyers.in", json=payload).json()
        auth_code = parse_qs(urlparse(r4['Url']).query)['auth_code']
        
        session = fyersModel.SessionModel(client_id=CLIENT_ID, secret_key=SECRET_KEY, redirect_uri=REDIRECT_URI, response_type="code", grant_type="authorization_code")
        session.set_token(auth_code)
        token_resp = session.generate_token()
        
        if "access_token" in token_resp:
            print(f"[{get_ist_now().strftime('%H:%M:%S')}] Auto-Login Successful! Mode: {'PAPER TRADE' if PAPER_TRADE_MODE else 'REAL LIVE'}")
            return fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token_resp["access_token"], log_path="")
    except Exception as e:
        print(f"Auto-Login Failed: {e}")
    return None

# ==============================================================================
# 5. TECHNICAL INDICATORS ENGINE (5-MINUTE TIMEFRAME)
# ==============================================================================
def calculate_indicators(df):
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema15'] = df['close'].ewm(span=15, adjust=False).mean()
    
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(10).mean()
    hl2 = (df['high'] + df['low']) / 2
    lowerband = hl2 - (2 * atr)
    df['st_green'] = df['close'] > lowerband
    return df

def fetch_5m_candles(fyers_client, symbol):
    today_str = get_ist_now().strftime("%Y-%m-%d")
    yesterday_str = (get_ist_now() - datetime.timedelta(days=4)).strftime("%Y-%m-%d")
    data = {"symbol": symbol, "resolution": "5", "date_format": "1", "range_from": yesterday_str, "range_to": today_str, "cont_flag": "1"}
    res = fyers_client.history(data=data)
    if res and res.get('s') == 'ok' and len(res['candles']) > 0:
        df = pd.DataFrame(res['candles'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
        return df
    return None

def get_option_ltp(fyers_client, opt_symbol):
    res = fyers_client.quotes(data={"symbols": opt_symbol})
    if res and res.get('s') == 'ok' and len(res['d']) > 0:
        return float(res['d']['v']['lp'])
    return 0.0

def execute_fyers_order(fyers_client, opt_symbol, side, qty, reason):
    if not opt_symbol:
        return False
    t_now = get_ist_now().strftime("%H:%M:%S")
    ltp = get_option_ltp(fyers_client, opt_symbol)
    print(f"[{t_now} IST] {side} | {opt_symbol} | Qty: {qty} | Option LTP: {ltp} | Reason: {reason}")
    
    if PAPER_TRADE_MODE:
        return True
        
    order_data = {"symbol": opt_symbol, "qty": qty, "type": 2, "side": 1 if side == "BUY" else -1, "productType": "INTRADAY", "limitPrice": 0, "stopPrice": 0, "validity": "DAY", "disclosedQty": 0, "offlineOrder": False}
    resp = fyers_client.place_order(data=order_data)
    print(f"Broker Order Response: {resp}")
    return resp.get('s') == 'ok'

# ==============================================================================
# 6. 24/7 CLOUD BACKGROUND ENGINE (IST SYNCHRONIZED)
# ==============================================================================
fyers = None
last_login_date = None
nifty_fut_symbol = ""
weekly_opt_df = None

position = None
active_option = ""
trade_count = 0
lot1_exited = False
range_5m_locked = False
first_5m_high, first_5m_low = 0.0, 0.0
prev_day_high, prev_day_low = 0.0, 0.0
is_gap_day = False

print("Cloud Engine Started! 100% Dynamic Auto-Expiry Mode Active...")

while True:
    now_dt = get_ist_now()
    now = now_dt.time()
    today_date = now_dt.date()

    if now_dt.weekday() >= 5:
        time.sleep(60)
        continue

    if now >= datetime.time(9, 5) and last_login_date != today_date and now < datetime.time(15, 15):
        fyers = fyers_auto_login()
        if fyers:
            nifty_fut_symbol, weekly_opt_df = get_active_nifty_contracts()
            last_login_date = today_date
            position = None
            trade_count = 0
            range_5m_locked = False
        else:
            time.sleep(30)
            continue

    if fyers is None or now < datetime.time(9, 15) or now > datetime.time(15, 20):
        time.sleep(10)
        continue

    try:
        df = fetch_5m_candles(fyers, nifty_fut_symbol)
        if df is None or len(df) < 30:
            time.sleep(5)
            continue

        df = calculate_indicators(df)
        today_df = df[df['datetime'].dt.date == today_date]
        prev_df = df[df['datetime'].dt.date < today_date]

        if len(today_df) < 2 or len(prev_df) == 0:
            time.sleep(5)
            continue

        if not range_5m_locked and now >= datetime.time(9, 20):
            last_prev_date = prev_df['datetime'].dt.date.max()
            yest_df = prev_df[prev_df['datetime'].dt.date == last_prev_date]
            prev_day_high = float(yest_df['high'].max())
            prev_day_low = float(yest_df['low'].min())

            first_candle = today_df.iloc
            first_5m_high = float(first_candle['high'])
            first_5m_low = float(first_candle['low'])
            day_open = float(first_candle['open'])

            is_gap_day = (day_open > prev_day_high) or (day_open < prev_day_low)
            range_5m_locked = True
            print(f"[09:20 IST] Range Locked | 5m High: {first_5m_high} | 5m Low: {first_5m_low} | Gap Day: {is_gap_day}")

        curr = today_df.iloc[-1]
        prev = today_df.iloc[-2]
        fut_ltp = float(curr['close'])

        if position is None and trade_count < MAX_DAILY_TRADES and range_5m_locked and datetime.time(9, 20) <= now < datetime.time(15, 0):
            rounded_strike = int(round(fut_ltp / 100.0) * 100)
            
            if trade_count == 0:
                if not is_gap_day:
