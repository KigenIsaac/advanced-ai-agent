import json
from typing import Optional

from .base import ProviderAdapter


class GeminiProvider(ProviderAdapter):
    async def stream_chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 16_384,
        stream: bool = True,
    ):
        contents: list[dict[str, object]] = []
        system_prompt = "\n".join(
            self._message_text(m.get("content"))
            for m in messages if m.get("role") == "system"
        )
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue
            text = self._message_text(msg.get("content"))
            if not text:
                continue
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": text}],
            })

        payload: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if tools:
            payload["tools"] = [{
                "functionDeclarations": [
                    {
                        "name": (t.get("function") or {}).get("name", "tool"),
                        "description": (t.get("function") or {}).get("description", ""),
                        "parameters": (t.get("function") or {}).get("parameters") or {"type": "object", "properties": {}},
                    }
                    for t in tools
                ]
            }]
        if thinking:
            payload["generationConfig"]["thinkingConfig"] = {"includeThoughts": True}

        attempt = 0
        while True:
            entry = await self.pool.acquire()
            self.client.last_key_label = entry.label()
            client = self.client._clients[entry.key]
            request = client.build_request("POST", f"/{self.model}:generateContent?key={entry.key}", json=payload)
            try:
                response = await client.send(request)
            except Exception as e:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"network error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1
                wait = min(self.retry_cap, 2.0 ** attempt)
                if self.on_retry is not None:
                    try:
                        self.on_retry(attempt, wait, f"net: {type(e).__name__}")
                    except Exception:
                        pass
                await __import__('asyncio').sleep(wait)
                continue

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response)
                await response.aread(); await response.aclose()
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
                body = await response.aread(); await response.aclose()
                raise RuntimeError(f"Gemini API error {response.status_code}: {body.decode(errors='replace')[:2000]}")

            data = await response.aread(); await response.aclose()
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Malformed Gemini response: {e}") from e
            pieces: list[str] = []
            for cand in parsed.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if isinstance(part, dict):
                        pieces.append(part.get("text", ""))
            text = "".join(pieces)
            usage = parsed.get("usageMetadata") or {}
            yield {
                "id": f"gemini-{__import__('uuid').uuid4().hex[:12]}",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("promptTokenCount", 0),
                    "completion_tokens": usage.get("candidatesTokenCount", 0),
                },
            }
            return
