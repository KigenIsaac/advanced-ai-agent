"""Named provider credentials for the local workspace."""
import asyncio
import base64
import json
import os
import sqlite3
import uuid
from contextlib import closing
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from main import MODEL_PROVIDERS, PROVIDER_DEFAULT_BASE_URLS, PROVIDER_DEFAULT_MODELS
from .credentials import safe_endpoint


class ModelSettings:
    def __init__(self, app):
        self.app = app
        self.path = app.home / "model-settings.sqlite3"
        self.endpoints = dict(PROVIDER_DEFAULT_BASE_URLS)
        self.endpoints[app.settings["provider"]] = app.settings["base_url"].rstrip("/")
        self.cipher = None
        self.lock = asyncio.Lock()
        with closing(self.connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vault (id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB, check_token BLOB);
                CREATE TABLE IF NOT EXISTS keys (id TEXT PRIMARY KEY, provider TEXT, endpoint TEXT, label TEXT, token BLOB,
                    UNIQUE(provider,endpoint,label));
                CREATE TABLE IF NOT EXISTS models (provider TEXT, model TEXT, PRIMARY KEY(provider,model));
                CREATE TABLE IF NOT EXISTS selection (id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT, model TEXT, key_id TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def state(self):
        with closing(self.connect()) as db:
            selection = db.execute("SELECT provider,model,key_id FROM selection").fetchone()
            keys = [dict(r) for r in db.execute("SELECT id,provider,endpoint,label FROM keys ORDER BY label")]
            history = {}
            for r in db.execute("SELECT provider, model FROM models ORDER BY model"):
                history.setdefault(r["provider"], []).append(r["model"])
            initialized = bool(db.execute("SELECT 1 FROM vault").fetchone())
        return dict(providers={p: dict(label=label, model=PROVIDER_DEFAULT_MODELS[p], history=history.get(p, []), endpoint=self.endpoints[p],
                                      keyless=p in {"local","ollama"} and urlsplit(self.endpoints[p]).hostname in {"localhost","127.0.0.1","::1"})
                               for p,label in MODEL_PROVIDERS.items()}, keys=keys,
                    selected=dict(selection) if selection else None, unlocked=self.cipher is not None,
                    initialized=initialized)

    def unlock(self, password):
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            raise ValueError("Enter the vault passphrase.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM vault").fetchone()
            if not row and len(password) < 12:
                raise ValueError("Use at least 12 characters for a new vault passphrase.")
            salt = row["salt"] if row else os.urandom(16)
            kdf = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536)
            cipher = Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode())))
            if row:
                try:
                    cipher.decrypt(row["check_token"])
                except InvalidToken:
                    raise ValueError("Incorrect vault passphrase.") from None
            else:
                db.execute("INSERT INTO vault VALUES(1,?,?)", (salt, cipher.encrypt(b"workspace vault")))
        self.cipher = cipher

    def save(self, provider, label, value):
        endpoint = self.endpoints.get(provider)
        if not endpoint or not safe_endpoint(endpoint):
            raise ValueError("Choose a supported provider endpoint.")
        if not self.cipher:
            raise ValueError("Unlock the key vault first.")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise ValueError("Enter a key name of up to 80 characters.")
        if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not all(33 <= ord(c) <= 126 for c in value):
            raise ValueError("Enter an API key without spaces.")
        key_id = uuid.uuid4().hex
        token = self.cipher.encrypt(json.dumps([key_id, provider, endpoint, value]).encode())
        with closing(self.connect()) as db, db:
            try:
                db.execute("INSERT INTO keys VALUES(?,?,?,?,?)", (key_id, provider, endpoint, label.strip(), token))
            except sqlite3.IntegrityError:
                raise ValueError("That provider already has a key with this name.") from None
        return key_id

    def resolve(self, provider, key_id):
        endpoint = self.endpoints.get(provider)
        if not endpoint or not safe_endpoint(endpoint):
            raise ValueError("Invalid provider endpoint.")
        if key_id == "local" and provider in {"ollama","local"} and urlsplit(endpoint).hostname in {"localhost","127.0.0.1","::1"}:
            return "local-no-api-key"
        if not self.cipher:
            raise ValueError("Unlock your saved model key in Settings.")
        with closing(self.connect()) as db:
            row = db.execute("SELECT token FROM keys WHERE id=? AND provider=? AND endpoint=?", (key_id,provider,endpoint)).fetchone()
        if row is None:
            raise ValueError("Choose a saved key for this provider.")
        try:
            identifier, vendor, destination, secret = json.loads(self.cipher.decrypt(row["token"]))
        except (InvalidToken, ValueError):
            raise ValueError("The saved key could not be opened.") from None
        if (identifier,vendor,destination) != (key_id,provider,endpoint):
            raise ValueError("The saved key does not match this endpoint.")
        return secret

    def select(self, provider, model, key_id):
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("Enter the provider's model ID.")
        self.resolve(provider, key_id)
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO models VALUES(?,?)", (provider, model.strip()))
            db.execute("INSERT OR REPLACE INTO selection VALUES(1,?,?,?)", (provider,model.strip(),key_id))

    def selection(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT provider,model,key_id FROM selection").fetchone()
        if row:
            return (row["provider"], self.endpoints[row["provider"]], row["model"],
                    self.resolve(row["provider"], row["key_id"]))
