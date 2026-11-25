"""Credentials for a single browser workspace. Never serialize secret values."""
import asyncio
import hashlib
import sys
from urllib.parse import urlsplit


def native_vault():
    # Select the native backend explicitly. Third-party plaintext backends are
    # unsuitable even when configured as the user's default keyring.
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
    elif sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring as Keyring
    elif sys.platform.startswith("linux"):
        from keyring.backends.SecretService import Keyring
    else:
        raise RuntimeError("Unsupported credential store")
    return Keyring()


def safe_endpoint(url):
    try:
        endpoint = urlsplit(url)
        return bool(endpoint.hostname and not endpoint.username and not endpoint.password
                    and not endpoint.query and not endpoint.fragment and
                    (endpoint.scheme == "https" or
                     (endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "::1", "localhost"})))
    except ValueError:
        return False


class VaultUnavailable(Exception):
    pass


class WorkspaceCredentials:
    def __init__(self, app):
        self.app = app
        self.memory = {}
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.waiting = False
        scope = str(app.home.resolve()).encode()
        self.service = "Agnes workspace " + hashlib.sha256(scope).hexdigest()

    def target(self, name):
        if name == "search":
            return "SerpAPI", "https://serpapi.com"
        if name == "provider":
            settings = self.app.settings
            return settings["provider"], settings["base_url"].rstrip("/")
        raise ValueError("Unknown credential")

    def account(self, name):
        label, endpoint = self.target(name)
        if not safe_endpoint(endpoint):
            raise ValueError("Use HTTPS or a loopback endpoint without embedded credentials or query parameters.")
        return hashlib.sha256(repr((name, label, endpoint)).encode()).hexdigest()

    async def disk(self, operation, account, *args):
        def run():
            return getattr(native_vault(), operation)(self.service, account, *args)
        try:
            return await asyncio.to_thread(run)
        except Exception:
            raise VaultUnavailable() from None

    async def read(self, name):
        async with self.lock:
            account = self.account(name)
            if account in self.memory:
                return self.memory[account]
            try:
                return await self.disk("get_password", account)
            except VaultUnavailable:
                return None

    async def status(self, name):
        label, endpoint = self.target(name)
        account = self.account(name)
        async with self.lock:
            available = True
            try:
                stored = bool(await self.disk("get_password", account))
            except VaultUnavailable:
                available, stored = False, False
            return dict(label=label, endpoint=endpoint, persistent=stored, available=available,
                        session=account in self.memory, waiting=self.waiting and name == "provider")

    async def update(self, name, endpoint, label, action, value):
        async with self.lock:
            if (label, endpoint) != self.target(name):
                raise ValueError("The credential destination changed. Reopen Settings.")
            account = self.account(name)
            if name == "provider" and action == "session" and not self.waiting:
                raise ValueError("This provider session has already started.")
            if action in {"session", "save"}:
                if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not all(33 <= ord(c) <= 126 for c in value):
                    raise ValueError("Enter one key without spaces or control characters.")
                if action == "save":
                    await self.disk("set_password", account, value)
                    self.memory.pop(account, None)
                else:
                    self.memory[account] = value
            elif action == "forget":
                if await self.disk("get_password", account):
                    await self.disk("delete_password", account)
                self.memory.pop(account, None)
            elif action == "clear":
                self.memory.pop(account, None)
            else:
                raise ValueError("Unknown operation")
            self.changed.set()

    async def await_provider(self):
        self.waiting = True
        self.app.ui.dirty()
        try:
            while True:
                self.changed.clear()
                value = await self.read("provider")
                if value:
                    return value
                await self.changed.wait()
        finally:
            self.waiting = False
            self.app.ui.dirty()
