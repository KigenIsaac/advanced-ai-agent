"""Session-bound skills expose their inputs through the normal catalog."""
import asyncio
import inspect
import json

from . import document_tasks, project_tasks, web_tasks

HANDLERS = {}
DESCRIPTIONS = {}
for module, prefix in [(web_tasks,"web_"), (document_tasks,"document_"), (project_tasks,"project_")]:
    for name, handler in vars(module).items():
        if name.startswith(prefix) and inspect.isfunction(handler):
            HANDLERS[name] = handler
            params = list(inspect.signature(handler).parameters.values())[1:]
            DESCRIPTIONS[name] = f"{handler.__doc__} Options: {', '.join(map(str, params)) or '(none)'}."


async def execute(registry, skill, options):
    if not isinstance(options, dict) or len(json.dumps(options, ensure_ascii=True)) > 100_000:
        return {"error": "invalid_arguments"}
    fn = HANDLERS[skill]
    if skill.startswith("web_"):
        backend = registry.web
        required = ("web_search",) if skill == "web_search_sources" else (("web_browser",) if "web_browser" in registry.names() else ("browser",))
    else:
        backend = getattr(registry.code, "ws", None)
        required = ("workspace",)
        if skill.startswith("project_") and skill not in {"project_public_api","project_api_diff"}:
            required = ("git", "workspace")
    if backend is None or not all(registry.is_enabled(n) for n in required):
        return {"error": "backend_unavailable"}
    try:
        bound = inspect.signature(fn).bind(backend, **options)
        for name, value in options.items():
            if name not in {"first","last","limit","index","checked"} and not isinstance(value, str):
                raise ValueError(f"{name} must be text")
        if inspect.iscoroutinefunction(fn):
            result = await fn(*bound.args, **bound.kwargs)
        else:
            result = await asyncio.to_thread(fn, *bound.args, **bound.kwargs)
        if isinstance(result,dict) and "error" in result:
            return result
        if len(json.dumps(result, ensure_ascii=False)) > 24000:
            return {"error":"result_too_large","message":"Select a smaller page range or document."}
        return {"skill":skill,"result":result}
    except Exception as exc:
        return {"error":"workflow_failed","type":type(exc).__name__,"message":str(exc)[:400]}
