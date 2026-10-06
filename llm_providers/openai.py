from typing import Optional

from .base import ProviderAdapter


class OpenAICompatibleProvider(ProviderAdapter):
    async def stream_chat(
        self,
        messages,
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 16_384,
        stream: bool = True,
    ):
        async for item in self._send_openai_like(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item
