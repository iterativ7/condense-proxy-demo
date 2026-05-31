from __future__ import annotations

from typing import Any


def transform_dolly_row(
    item: dict[str, Any],
    *,
    case_id: str,
    model: str,
) -> dict[str, Any]:
    """Map one Dolly row into canonical benchmark JSONL shape."""
    instruction = str(item.get("instruction") or "").strip()
    context = str(item.get("context") or "").strip()
    category = str(item.get("category") or "unknown").strip() or "unknown"
    expected = str(item.get("response") or "").strip()

    if context:
        prompt = (
            "Follow the instruction using the context. "
            "Be concise and accurate.\n\n"
            f"Instruction:\n{instruction}\n\n"
            f"Context:\n{context}"
        )
    else:
        prompt = (
            "Follow the instruction. "
            "Be concise and accurate.\n\n"
            f"Instruction:\n{instruction}"
        )

    reference: dict[str, Any] = {"scoring": "text"}
    if expected:
        reference["final_answer"] = expected

    return {
        "id": case_id,
        "request": {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        "reference": reference,
        "metadata": {
            "source": "databricks/databricks-dolly-15k",
            "split": "train",
            "category": category,
            "has_context": bool(context),
        },
    }

