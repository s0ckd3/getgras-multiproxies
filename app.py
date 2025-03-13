import os
import json
import asyncio
import time
import random
import uuid
import base64
import logging
import aiohttp
import websockets

# CẤU HÌNH CHUNG
PING_INTERVAL = 2 * 60         # tính bằng giây (2 phút)
CHROME_PING_INTERVAL = 6       # tính bằng giây
DIRECTOR_SERVER = 'https://director.getgrass.io'
RECONNECT_INTERVAL = 1         # tính bằng giây
HEADERS_TO_REPLACE = [
    'origin',
    'referer',
    'access-control-request-headers',
    'access-control-request-method',
    'access-control-allow-origin',
    'cookie',
    'date',
    'dnt',
    'trailer',
    'upgrade'
]

SETTINGS_FILE = os.path.join(os.getcwd(), 'data', 'setting.json')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36'

def load_settings():
    settings = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf8') as f:
                settings = json.load(f)
        except Exception as e:
            logging.error("Error parsing setting.json: %s", e)
    global USER_AGENT
    if settings.get("user_agent") and isinstance(settings["user_agent"], list) and settings["user_agent"]:
        USER_AGENT = random.choice(settings["user_agent"])
    if not settings.get("extension_id"):
        settings["extension_id"] = 'lkbnfiajjmbhnfledhphioinpickokdi'
    if not settings.get("version"):
        settings["version"] = '5.1.1'
    return settings

settings = load_settings()

def sleep(ms):
    return asyncio.sleep(ms / 1000)

def get_unix_timestamp():
    return int(time.time())

def is_uuid(id_str):
    return isinstance(id_str, str) and len(id_str) == 36

def generate_random_number():
    return random.randint(0, 100000000)

# Các key lưu trữ
BROWSER_ID_KEY = 'wynd:browser_id'
USER_ID_KEY = 'wynd:user_id'
JWT_KEY = 'wynd:jwt'
STATUS_KEY = 'wynd:status'
DEVICE_KEY = 'wynd:device'
USER_KEY = 'wynd:user'
AUTHENTICATED_KEY = 'wynd:authenticated'
SETTINGS_KEY = 'wynd:settings'
POPUP_STATE_KEY = 'wynd:popup'
PERMISSIONS_KEY = 'wynd:permissions'
ACCESS_TOKEN_KEY = 'accessToken'
REFRESH_TOKEN_KEY = 'refreshToken'
USERNAME_KEY = 'username'
EMAIL_KEY = 'email'
IS_CONNECT_BUTTON_ACTIVE_KEY = 'isConnectButtonActive'

# Một lớp đơn giản mô phỏng EventEmitter của Node.js
class EventEmitter:
    def __init__(self):
        self._listeners = {}
    def on(self, event, callback):
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)
    def emit(self, event, *args, **kwargs):
        if event in self._listeners:
            for callback in self._listeners[event]:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(*args, **kwargs))
                else:
                    callback(*args, **kwargs)

# Lớp ChromeStorage – lưu và đọc file JSON
class ChromeStorage:
    def __init__(self, file):
        self.file = file
        self.data = {}
        self.load()
    def load(self):
        if os.path.exists(self.file):
            try:
                with open(self.file, 'r', encoding='utf8') as f:
                    self.data = json.load(f)
            except Exception as e:
                logging.error("Error loading storage file: %s", e)
                self.data = {}
    def save(self):
        with open(self.file, 'w', encoding='utf8') as f:
            json.dump(self.data, f, indent=2)
    async def get(self, key):
        return self.data.get(key, None)
    async def set(self, key, value):
        self.data[key] = value
        self.save()
    async def remove(self, key):
        if key in self.data:
            del self.data[key]
            self.save()

# Lớp ChromeRuntime – mô phỏng đối tượng runtime của extension
class ChromeRuntime(EventEmitter):
    def get_manifest(self):
        return {
            "version": settings["version"],
            "id": settings["extension_id"],
        }

# Mutex đơn giản sử dụng asyncio.Lock
class Mutex:
    def __init__(self):
        self._lock = asyncio.Lock()
    async def run_exclusive(self, callback):
        async with self._lock:
            return await callback()
    async def acquire(self):
        await self._lock.acquire()
        return self._lock.release

# CustomStorage với thời gian hết hạn
class CustomStorage:
    def __init__(self, default_expire_ms=10*60*1000):
        self.default_expire_ms = default_expire_ms
        self.storage = {}
        asyncio.create_task(self._clear_expired_loop())
    async def _clear_expired_loop(self):
        while True:
            self.clear_expired()
            await asyncio.sleep(60)
    def get(self, key):
        self.check_key_expired(key)
        return self.storage.get(key, {}).get("value", None)
    def set(self, key, value, ex_ms=None):
        expiration = ex_ms if ex_ms is not None else self.default_expire_ms
        self.storage[key] = {"value": value, "expire_at": time.time()*1000 + expiration}
    def del_key(self, key):
        if key in self.storage:
            del self.storage[key]
    def exists(self, key):
        self.check_key_expired(key)
        return key in self.storage
    def clear_expired(self):
        for key in list(self.storage.keys()):
            self.check_key_expired(key)
    def check_key_expired(self, key):
        if key not in self.storage or time.time()*1000 > self.storage[key]["expire_at"]:
            self.storage.pop(key, None)

# ResponseProcessor – sử dụng asyncio.Future để chờ dữ liệu
class ResponseProcessor:
    def __init__(self):
        self.cookie_futures = {}
        self.cookie_storage = {}
        self.redirect_futures = {}
        self.redirect_storage = {}
    async def get_response_cookies(self, request_id, timeout_ms):
        if request_id in self.cookie_storage:
            return self.cookie_storage[request_id]
        fut = asyncio.get_event_loop().create_future()
        self.cookie_futures[request_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout_ms/1000)
        except asyncio.TimeoutError:
            self.cookie_futures.pop(request_id, None)
            raise Exception(f"Timeout for request {request_id}")
    def set_response_cookies(self, request_id, cookies):
        if request_id in self.cookie_futures:
            fut = self.cookie_futures.pop(request_id)
            if not fut.done():
                fut.set_result(cookies)
        else:
            self.cookie_storage[request_id] = cookies
    async def get_redirect_data(self, request_id, timeout_ms):
        if request_id in self.redirect_storage:
            return self.redirect_storage[request_id]
        fut = asyncio.get_event_loop().create_future()
        self.redirect_futures[request_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout_ms/1000)
        except asyncio.TimeoutError:
            self.redirect_futures.pop(request_id, None)
            raise Exception(f"Timeout for redirect data {request_id}")
    def set_redirect_data(self, request_id, redirect_data):
        if request_id in self.redirect_futures:
            fut = self.redirect_futures.pop(request_id)
            if not fut.done():
                fut.set_result(redirect_data)
        else:
            self.redirect_storage[request_id] = redirect_data
    async def register_on_error_occurred_event(self, request_id):
        self.set_response_cookies(request_id, '')

# Một đối tượng toàn cục ResponseProcessor
RESPONSE_PROCESSOR = ResponseProcessor()

# RequestFetcher – thực hiện HTTP request sử dụng aiohttp
class RequestFetcher:
    def __init__(self, proxy=None):
        self.proxy = proxy
        self.used_request_ids = set()
        self.lock = asyncio.Lock()
    async def fetch(self, url, request_options):
        method = request_options.get("method", "GET")
        headers = request_options.get("headers", {})
        data = request_options.get("data") or request_options.get("body")
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, headers=headers, data=data, allow_redirects=False, proxy=self.proxy) as response:
                    request_id = str(generate_random_number())
                    self.used_request_ids.add(request_id)
                    return request_id, response
        except Exception as e:
            return None, None

# Hàm chuyển đổi bytes sang base64
def array_buffer_to_base64(buffer_bytes):
    return base64.b64encode(buffer_bytes).decode('utf8')

# Lớp AppInstance – môi trường độc lập cho mỗi "extension"
class AppInstance:
    def __init__(self, options=None):
        if options is None:
            options = {}
        self.proxy = options.get("proxy")
        self.storage_file = options.get("storageFileOverride", os.path.join(os.getcwd(), 'data', 'settingStorage.json'))
        self.chrome_storage = ChromeStorage(self.storage_file)
        self.proxy_agent = self.proxy  # Sử dụng URL proxy trực tiếp
        self.chrome_runtime = ChromeRuntime()
        self.chrome = {
            "storage": {"local": self.chrome_storage},
            "runtime": self.chrome_runtime,
            "webRequest": {
                "onBeforeRequest": {"addListener": lambda x: None},
                "onBeforeRedirect": {"addListener": lambda x: None},
                "onCompleted": {"addListener": lambda x: None},
                "onErrorOccurred": {"addListener": lambda x: None}
            },
            "declarativeNetRequest": {
                "updateSessionRules": lambda rules: print("Update rules:", rules)
            },
            "permissions": {
                "getAll": lambda callback: callback({"origins": ["<all_urls>"]}),
                "onAdded": {"addListener": lambda x: None},
                "onRemoved": {"addListener": lambda x: None}
            }
        }
        self.request_fetcher = RequestFetcher(self.proxy_agent)
        self.websocket = None
        self.last_live_connection_timestamp = get_unix_timestamp()
        self.last_checkin_timestamp = 0
        self.user_agent = USER_AGENT
        self.RPCTable = {
            "HTTP_REQUEST": self.perform_http_request,
            "AUTH": self.authenticate,
            "PONG": lambda data: None
        }
        self._register_runtime_listeners()
        self._register_storage_listeners()

    async def update_browser_id(self):
        old_browser_id = await self.chrome_storage.get(BROWSER_ID_KEY)
        if not old_browser_id or (isinstance(old_browser_id, str) and old_browser_id.strip() == ""):
            new_browser_id = str(uuid.uuid4())
            await self.chrome_storage.set(BROWSER_ID_KEY, new_browser_id)

    async def perform_http_request(self, params):
        # Thay đổi header nếu cần
        replaced_request_headers = []
        for header_key, value in params.get("headers", {}).items():
            if header_key.lower() in HEADERS_TO_REPLACE:
                replaced_request_headers.append({
                    "header": header_key,
                    "operation": "set",
                    "value": value
                })
        new_rule_ids = []
        if replaced_request_headers:
            new_rule_id = generate_random_number()
            new_rule_ids.append(new_rule_id)
            new_rule = {
                "id": new_rule_id,
                "priority": 1,
                "action": {
                    "type": "modifyHeaders",
                    "requestHeaders": replaced_request_headers
                },
                "condition": {
                    "urlFilter": params.get("url", "").rstrip("/"),
                    "tabIds": [-1]  # mô phỏng chrome.tabs.TAB_ID_NONE
                }
            }
            self.chrome["declarativeNetRequest"]["updateSessionRules"]({
                "addRules": [new_rule]
            })
        request_options = {
            "method": params.get("method", "GET"),
            "headers": params.get("headers", {})
        }
        if "body" in params and params["body"]:
            try:
                body_bytes = base64.b64decode(params["body"])
                request_options["data"] = body_bytes
            except Exception as e:
                request_options["data"] = params["body"]
        request_id, response = await self.request_fetcher.fetch(params.get("url"), request_options)
        if response is None:
            return {
                "url": params.get("url"),
                "status": 400,
                "status_text": "Bad Request",
                "headers": {},
                "body": ""
            }
        if new_rule_ids:
            self.chrome["declarativeNetRequest"]["updateSessionRules"]({
                "removeRuleIds": new_rule_ids
            })
        # Xử lý redirect nếu status 3xx
        if 300 <= response.status < 400:
            if not request_id:
                return None
            try:
                redirect_data = await RESPONSE_PROCESSOR.get_redirect_data(request_id, 5000)
                response_metadata = json.loads(redirect_data)
                if "Set-Cookie" in response_metadata.get("headers", {}):
                    response_metadata["headers"]["Set-Cookie"] = json.loads(response_metadata["headers"]["Set-Cookie"])
                return {
                    "url": str(response.url),
                    "status": response_metadata.get("statusCode"),
                    "status_text": "Redirect",
                    "headers": response_metadata.get("headers", {}),
                    "body": ""
                }
            except Exception as e:
                return None
        headers = {}
        for key, value in response.headers.items():
            if key.lower() != "content-encoding":
                headers[key] = value
        if request_id:
            try:
                response_cookies = await RESPONSE_PROCESSOR.get_response_cookies(request_id, 5000)
                if response_cookies != '':
                    cookies = json.loads(response_cookies)
                    if cookies:
                        headers["Set-Cookie"] = cookies
            except Exception as e:
                logging.error("Error processing response cookies: %s", e)
        body_bytes = await response.read()
        return {
            "url": str(response.url),
            "status": response.status,
            "status_text": response.reason,
            "headers": headers,
            "body": array_buffer_to_base64(body_bytes)
        }

    async def authenticate(self, data=None):
        browser_id = await self.chrome_storage.get(BROWSER_ID_KEY)
        user_id = await self.chrome_storage.get(USER_ID_KEY)
        manifest = self.chrome_runtime.get_manifest()
        version = manifest.get("version")
        extension_id = manifest.get("id")
        if not is_uuid(browser_id):
            return None
        return {
            "browser_id": browser_id,
            "user_id": user_id,
            "user_agent": self.user_agent,
            "timestamp": get_unix_timestamp(),
            "device_type": "extension",
            "version": version,
            "extension_id": extension_id
        }

    async def checkin(self):
        browser_id = await self.chrome_storage.get(BROWSER_ID_KEY)
        has_permissions = await self.chrome_storage.get(PERMISSIONS_KEY)
        user_id = await self.chrome_storage.get(USER_ID_KEY)
        manifest = self.chrome_runtime.get_manifest()
        version = manifest.get("version")
        extension_id = manifest.get("id")
        user_agent = self.user_agent
        if not browser_id or not has_permissions or not user_id:
            logging.warning("[CHECKIN] Missing BrowserID, Permissions or UserID")
            await self.chrome_storage.set(STATUS_KEY, "DISCONNECTED")
            return
        now = get_unix_timestamp()
        if now - self.last_checkin_timestamp <= 60:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{DIRECTOR_SERVER}/checkin", json={
                    "browserId": browser_id,
                    "userId": user_id,
                    "version": version,
                    "extensionId": extension_id,
                    "userAgent": user_agent,
                    "deviceType": "extension"
                }, proxy=self.proxy_agent) as res:
                    if res.status == 407:
                        return
                    self.last_checkin_timestamp = now
                    if res.status == 200:
                        data = await res.json()
                        return data
        except Exception as err:
            if "407" in str(err):
                return
            logging.error("Checkin error: %s", err)

    async def get_local_storage(self, key):
        value = await self.chrome_storage.get(key)
        try:
            return json.loads(value) if isinstance(value, str) else value
        except:
            return value

    async def set_local_storage(self, key, value):
        await self.chrome_storage.set(key, json.dumps(value))

    async def connect(self):
        data = await self.checkin()
        await self.chrome_storage.set(STATUS_KEY, "CONNECTING")
        if not data:
            return
        logging.info(f"[CHECKIN] [{self.proxy}] OK.")
        destinations = data.get("destinations", [])
        token = data.get("token")
        destinations = [f"ws://{dest}" for dest in destinations]
        max_destinations = len(destinations) - 1
        retries = 0
        async def initialize_websocket():
            nonlocal retries
            ws_url = f"{destinations[retries]}?token={token}"
            try:
                async with websockets.connect(ws_url, proxy=self.proxy_agent) as ws:
                    self.websocket = ws
                    logging.info(f"[WEBSOCKET] [{self.proxy}] connected.")
                    self.last_live_connection_timestamp = get_unix_timestamp()
                    await self.chrome_storage.set(STATUS_KEY, "CONNECTED")
                    asyncio.create_task(self._start_socket_ping())
                    asyncio.create_task(self._start_socket_liveness_check())
                    async for message in ws:
                        self.last_live_connection_timestamp = get_unix_timestamp()
                        try:
                            parsed = json.loads(message)
                        except Exception as err:
                            continue
                        action = parsed.get("action")
                        if action in self.RPCTable:
                            if action != "PONG":
                                logging.info(f"[WEBSOCKET RPC] message {action}")
                            try:
                                result = await self.RPCTable[action](parsed.get("data"))
                                response_msg = json.dumps({
                                    "id": parsed.get("id"),
                                    "origin_action": action,
                                    "result": result
                                })
                                await ws.send(response_msg)
                            except Exception as e:
                                logging.error("RPC error: %s", e)
            except websockets.exceptions.ConnectionClosed as e:
                if e.code == 1000:
                    logging.info(f"[WEBSOCKET] Close, code={e.code} reason={e.reason}")
                else:
                    logging.info("[WEBSOCKET] Disconnect, retry...")
                    await asyncio.sleep(RECONNECT_INTERVAL)
                    retries += 1
                    if retries > max_destinations:
                        await self.chrome_storage.set(STATUS_KEY, "DEAD")
                    else:
                        await initialize_websocket()
            except Exception as e:
                logging.error("[WEBSOCKET] error: %s", e)
                await asyncio.sleep(RECONNECT_INTERVAL)
                retries += 1
                if retries > max_destinations:
                    await self.chrome_storage.set(STATUS_KEY, "DEAD")
                    return
                await initialize_websocket()
        await initialize_websocket()

    async def _start_socket_ping(self):
        while self.websocket and self.websocket.open:
            try:
                ping_msg = json.dumps({
                    "id": str(uuid.uuid4()),
                    "version": "1.0.0",
                    "action": "PING",
                    "data": {}
                })
                await self.websocket.send(ping_msg)
            except Exception as e:
                logging.error("Error sending ping: %s", e)
            await asyncio.sleep(CHROME_PING_INTERVAL)

    async def _start_socket_liveness_check(self):
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if self.websocket:
                if self.websocket.open:
                    await self.chrome_storage.set(STATUS_KEY, "CONNECTED")
                else:
                    await self.chrome_storage.set(STATUS_KEY, "DISCONNECTED")
            is_connect_button_active = await self.chrome_storage.get(IS_CONNECT_BUTTON_ACTIVE_KEY)
            if is_connect_button_active is None:
                await self.chrome_storage.set(IS_CONNECT_BUTTON_ACTIVE_KEY, True)
                is_connect_button_active = True
            if not self.websocket or not self.websocket.open or not is_connect_button_active:
                logging.info("[WEBSOCKET] Socket not ready for liveness check...")
                continue
            current_timestamp = get_unix_timestamp()
            seconds_since_last_live = current_timestamp - self.last_live_connection_timestamp
            if seconds_since_last_live > 129 or not self.websocket.open:
                logging.error("[WEBSOCKET] Socket appears dead! Reconnecting...")
                try:
                    await self.websocket.close()
                except Exception as err:
                    logging.error(err)
                await asyncio.sleep(RECONNECT_INTERVAL)
                asyncio.create_task(self.connect())
                break

    def _register_runtime_listeners(self):
        self.chrome_runtime.on("message", self._on_runtime_message)
        self.chrome_runtime.on("messageExternal", self._on_message_external)
        self.chrome_runtime.on("connect", self._on_connect)

    def _on_runtime_message(self, message, sender=None, send_response=None):
        if message and "type" in message:
            t = message["type"]
            if t == "ping":
                if send_response:
                    send_response("pong")
            elif t == "connect":
                asyncio.create_task(self.connect())
            elif t == "disconnect":
                try:
                    if self.websocket:
                        asyncio.create_task(self.websocket.close())
                except Exception as err:
                    logging.error(err)
                asyncio.create_task(self.chrome_storage.set(IS_CONNECT_BUTTON_ACTIVE_KEY, False))
                asyncio.create_task(self.chrome_storage.set(STATUS_KEY, "DISCONNECTED"))
                if send_response:
                    send_response("Disconnected...")
            elif t == "getWebsocketReadyState":
                if send_response:
                    ready_state = self.websocket.open if self.websocket else -1
                    send_response({"readyState": ready_state})
            elif t == "getCurrentVersion":
                if send_response:
                    send_response({"version": self.chrome_runtime.get_manifest().get("version")})
            else:
                asyncio.create_task(self.chrome_storage.set(JWT_KEY, message))
                if self.websocket and self.websocket.open:
                    asyncio.create_task(self.websocket.send(json.dumps({
                        "jwt": message,
                        "action": "VALIDATE_JWT"
                    })))
                if send_response:
                    send_response({"success": True})

    def _on_message_external(self, request, sender=None, send_response=None):
        if request and "type" in request:
            t = request["type"]
            if t == "setAccessToken":
                asyncio.create_task(self.chrome_storage.set(ACCESS_TOKEN_KEY, request.get("payload")))
            elif t == "setRefreshToken":
                asyncio.create_task(self.chrome_storage.set(REFRESH_TOKEN_KEY, request.get("payload")))
            elif t == "getBrowserId":
                if send_response:
                    asyncio.create_task(send_response(asyncio.create_task(self.chrome_storage.get(BROWSER_ID_KEY))))
            elif t == "getUserId":
                if send_response:
                    asyncio.create_task(send_response(asyncio.create_task(self.chrome_storage.get(USER_ID_KEY))))
            elif t == "setUserId":
                asyncio.create_task(self.chrome_storage.set(USER_ID_KEY, request.get("payload")))
            elif t == "setIsAuthenticated":
                asyncio.create_task(self.chrome_storage.set(AUTHENTICATED_KEY, request.get("payload")))
            elif t == "updateUsername":
                asyncio.create_task(self.chrome_storage.set(USERNAME_KEY, request.get("payload")))
            elif t == "clearStorage":
                asyncio.create_task(self.chrome_storage.set(USER_KEY, None))
                asyncio.create_task(self.chrome_storage.set(USERNAME_KEY, ""))
                asyncio.create_task(self.chrome_storage.set(EMAIL_KEY, ""))
                asyncio.create_task(self.chrome_storage.set(AUTHENTICATED_KEY, False))
                asyncio.create_task(self.chrome_storage.set(DEVICE_KEY, None))
                asyncio.create_task(self.chrome_storage.set(SETTINGS_KEY, None))
                asyncio.create_task(self.chrome_storage.set(ACCESS_TOKEN_KEY, ""))
                asyncio.create_task(self.chrome_storage.set(REFRESH_TOKEN_KEY, ""))
                if send_response:
                    asyncio.create_task(send_response("Storage cleared"))

    def _on_connect(self, port):
        if port and port.get("name") == "popup":
            asyncio.create_task(self.chrome_storage.set(POPUP_STATE_KEY, True))
            # Mô phỏng onDisconnect nếu cần

    async def start(self):
        await self.update_browser_id()
        self.chrome_runtime.emit("message", {"type": "connect"})

# Hàm exported start_app: tạo và khởi chạy instance mới
async def start_app(options=None):
    instance = AppInstance(options)
    await instance.start()
    return instance
