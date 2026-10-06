import asyncio
import json
import uuid
from typing import Any, Optional

import httpx


DEFAULT_MAX_TOKENS = 16_384
_DEFAULT_RETRY_CAP = 60.0
_TRANSIENT_NET_ERRORS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


class ProviderAdapter:
    def __init__(self, client: Any):
        self.client = client

    @property
    def provider(self) -> str:
        return str(getattr(self.client, "provider", "openai") or "openai").lower()

    @property
    def model(self) -> str:
        return getattr(self.client, "model", "")

    @property
    def max_retries(self) -> int:
        return max(0, getattr(self.client, "_max_retries", 0))

    @property
    def retry_cap(self) -> float:
        return getattr(self.client, "_retry_cap", _DEFAULT_RETRY_CAP)

    @property
    def on_retry(self):
        return getattr(self.client, "_on_retry", None)

    @property
    def pool(self):
        return getattr(self.client, "_pool", None)

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 30.0

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, str):
                    pieces.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        pieces.append(str(text))
            return "\n".join(pieces)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _sanitize_text(s: Any) -> str:
        if not isinstance(s, str):
            return s
        return "".join(
            ch for ch in s
            if ch not in ("\ufffd", "\ufffe", "\uffff")
            and not (0xD800 <= ord(ch) <= 0xDFFF)
            and ord(ch) != 0
        )

    @classmethod
    def sanitize_obj(cls, obj: Any) -> Any:
        if isinstance(obj, str):
            return cls._sanitize_text(obj)
        if isinstance(obj, dict):
            return {
                (cls._sanitize_text(k) if isinstance(k, str) else k): cls.sanitize_obj(v)
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [cls.sanitize_obj(v) for v in obj]
        return obj

    @staticmethod
    def _normalize_message_for_api(msg: dict) -> dict:
        if not isinstance(msg, dict):
            return msg
        if msg.get("role") != "assistant":
            return msg
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg

        def parse_arguments(raw: Any) -> str:
            if raw is None:
                return "{}"
            if isinstance(raw, (dict, list)):
                return json.dumps(raw, ensure_ascii=False)
            if isinstance(raw, str):
                return raw
            return json.dumps(raw, ensure_ascii=False)

        new_calls: list[dict] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            new_fn = dict(fn)
            new_fn["arguments"] = parse_arguments(fn.get("arguments"))
            new_tc = dict(tc)
            new_tc["function"] = new_fn
            new_calls.append(new_tc)

        new_msg = dict(msg)
        new_msg["tool_calls"] = new_calls
        return new_msg

    @classmethod
    def normalize_messages(cls, messages: list[dict]) -> list[dict]:
        return [cls._normalize_message_for_api(m) for m in messages]

    def _headers_for(self, api_key: str, *, stream: bool = True) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "agnes-agent/1.0",
        }

    def _build_payload(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]],
        thinking: bool,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        safe_messages = self.sanitize_obj(self.normalize_messages(messages))
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": safe_messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        return payload

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        raise NotImplementedError

    async def _send_openai_like(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        payload = self._build_payload(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )
        attempt = 0
        while True:
            entry = await self.pool.acquire()
            self.client.last_key_label = entry.label()
            client = self.client._clients[entry.key]
            request = client.build_request("POST", "/chat/completions", json=payload)
            try:
                response = await client.send(request, stream=True)
            except _TRANSIENT_NET_ERRORS as e:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"network error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1
                wait = min(self.retry_cap, 2.0 ** attempt)
                if self.on_retry is not None:
                    try:
                        self.on_retry(attempt, wait, f"net: {type(e).__name__}")
                    except Exception:
                        pass
                await asyncio.sleep(wait)
                continue

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response)
                await response.aread()
                await response.aclose()
                wait = retry_after if retry_after is not None else min(self.retry_cap, 2.0 ** attempt)
                entry.set_cooldown(wait)
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Rate limited after {attempt + 1} attempts (key={entry.label()}, tier={getattr(self.client, 'tier', 'default')}).")
                attempt += 1
                if self.on_retry is not None:
                    try:
                        self.on_retry(attempt, wait, f"429 on {entry.label()}")
                    except Exception:
                        pass
                continue

            if response.status_code >= 400:
                body = await response.aread()
                await response.aclose()
                raise RuntimeError(f"Provider API error {response.status_code}: {body.decode(errors='replace')[:2000]}")

            yielded_any = False
            try:
                if not stream:
                    data = await response.aread()
                    await response.aclose()
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError as e:
                        raise RuntimeError(f"Malformed non-streaming response: {e}") from e
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        return
                    try:
                        parsed = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    yielded_any = True
                    yield parsed
            except _TRANSIENT_NET_ERRORS as e:
                if yielded_any:
                    raise RuntimeError(f"stream broke mid-response: {type(e).__name__}: {str(e) or '(no detail)'}")
                if attempt >= self.max_retries:
                    raise RuntimeError(f"stream error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1
                wait = min(self.retry_cap, 2.0 ** attempt)
                if self.on_retry is not None:
                    try:
                        self.on_retry(attempt, wait, f"stream: {type(e).__name__}")
                    except Exception:
                        pass
                await asyncio.sleep(wait)
                continue
            finally:
                try:
                    await response.aclose()
                except Exception:
                    pass
            return
