from .agnes import AgnesProvider
from .anthropic import AnthropicProvider
from .cohere import CohereProvider
from .deepseek import DeepSeekProvider
from .fireworks import FireworksProvider
from .gemini import GeminiProvider
from .groq import GroqProvider
from .local import LocalModelProvider
from .mistral import MistralProvider
from .ollama import OllamaProvider
from .openai import OpenAICompatibleProvider
from .openrouter import OpenRouterProvider
from .perplexity import PerplexityProvider
from .together import TogetherProvider
from .xai import XAIProvider
from .base import ProviderAdapter

PROVIDER_CLASSES = {
    "agnes": AgnesProvider,
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "local": LocalModelProvider,
    "openrouter": OpenRouterProvider,
    "deepseek": DeepSeekProvider,
    "groq": GroqProvider,
    "mistral": MistralProvider,
    "together": TogetherProvider,
    "cohere": CohereProvider,
    "ollama": OllamaProvider,
    "perplexity": PerplexityProvider,
    "fireworks": FireworksProvider,
    "xai": XAIProvider,
}


def get_provider_adapter(client: object) -> ProviderAdapter:
    provider_name = str(getattr(client, "provider", "openai") or "openai").lower()
    provider_cls = PROVIDER_CLASSES.get(provider_name, OpenAICompatibleProvider)
    return provider_cls(client)


__all__ = [
    "ProviderAdapter",
    "AgnesProvider",
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "LocalModelProvider",
    "OpenRouterProvider",
    "DeepSeekProvider",
    "GroqProvider",
    "MistralProvider",
    "TogetherProvider",
    "CohereProvider",
    "OllamaProvider",
    "PerplexityProvider",
    "FireworksProvider",
    "XAIProvider",
    "get_provider_adapter",
    "PROVIDER_CLASSES",
]
