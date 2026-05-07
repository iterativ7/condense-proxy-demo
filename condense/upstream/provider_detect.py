"""Detect LLM provider from model name."""


def detect_provider(model: str) -> str:
    """Detect the LLM provider from the model name string.

    Args:
        model: The model identifier (e.g., "gpt-4o", "claude-3-sonnet").

    Returns:
        Provider name: "anthropic", "openai", "deepseek", "google", or "unknown".
    """
    model_lower = model.lower()
    if any(x in model_lower for x in ["claude", "anthropic"]):
        return "anthropic"
    if any(x in model_lower for x in ["gpt", "o1", "o3", "chatgpt"]):
        return "openai"
    if any(x in model_lower for x in ["deepseek"]):
        return "deepseek"
    if any(x in model_lower for x in ["gemini", "palm"]):
        return "google"
    return "unknown"
