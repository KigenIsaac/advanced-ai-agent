import json
from typing import Any, Optional

from .base import ProviderAdapter


class AnthropicProvider(ProviderAdapter):
    @staticmethod
    def _tool_schema_to_anthropic(tool: dict) -> dict:
        fn = tool.get("function") or {}
        schema = fn.get("parameters") or {}
        return {
            "name": fn.get("name", "tool"),
            "description": fn.get("description", ""),
            "input_schema": {
                "type": schema.get("type", "object"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        }

    def _anthropic_messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                continue
            if role == "user":
                out.append({"role": "user", "content": self._message_text(content)})
                continue
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                blocks: list[dict] = []
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or "{}"
                        try:
                            parsed = json.loads(args)
                        except Exception:
                            parsed = {"raw": args}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id") or f"call_{__import__('uuid').uuid4().hex[:8]}",
                            "name": fn.get("name", "tool"),
                            "input": parsed,
                        })
                if content:
                    text = self._message_text(content)
                    if text:
                        blocks.append({"type": "text", "text": text})
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
                continue
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id") or f"call_{__import__('uuid').uuid4().hex[:8]}",
                        "content": self._message_text(content),
                    }],
                })
        return out

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
        system_parts = []
        for item in messages:
            if item.get("role") == "system":
                system_parts.append(self._message_text(item.get("content")))

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._anthropic_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if tools:
            payload["tools"] = [self._tool_schema_to_anthropic(t) for t in tools]
        if thinking:
            payload["thinking"] = {"type": "enabled"}

        attempt = 0
        while True:
            entry = await self.pool.acquire()
            self.client.last_key_label = entry.label()
            client = self.client._clients[entry.key]
            request = client.build_request("POST", "/messages", json=payload)
            try:
                response = await client.send(request, stream=stream)
            except (TimeoutError, ConnectionError, OSError) as e:
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
                raise RuntimeError(f"Anthropic API error {response.status_code}: {body.decode(errors='replace')[:2000]}")

            if not stream:
                data = await response.aread(); await response.aclose()
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError as e:
                    raise RuntimeError(f"Malformed non-streaming response: {e}") from e
                text = ""
                for block in parsed.get("content") or []:
                    if block.get("type") == "text":
                        text += block.get("text", "")
                yield {"id": parsed.get("id", "msg_0"), "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": parsed.get("stop_reason") or "stop"}], "usage": {"prompt_tokens": parsed.get("usage", {}).get("input_tokens", 0), "completion_tokens": parsed.get("usage", {}).get("output_tokens", 0)}}
                return

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    return
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event_type = parsed.get("type")
                if event_type == "content_block_delta":
                    delta = parsed.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield {"id": "msg_0", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
                elif event_type == "message_delta":
                    stop_reason = parsed.get("delta", {}).get("stop_reason")
                    if stop_reason:
                        yield {"id": "msg_0", "choices": [{"index": 0, "delta": {}, "finish_reason": stop_reason}]}
            try:
                await response.aclose()
            except Exception:
                pass
            return
