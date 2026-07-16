"""
Sanitization helpers.

Two distinct concerns live here:

1. OUTPUT sanitization (`sanitize_html`, `sanitize_dict`) — HTML-escapes
   user-supplied text to prevent XSS when content is rendered in a browser.

2. PROMPT-INJECTION hardening (`neutralize_prompt_injection`, `neutralize_terms`)
   — defangs user-supplied text BEFORE it is interpolated into an LLM prompt.
   Brand profile fields, campaign briefs and viral keywords are all attacker-
   controllable and flow straight into generation prompts. Natural language
   cannot be "escaped" the way SQL is, so the defense is layered:
     a. Strip mechanical break-out vectors (control chars, model chat-template
        tokens) that never legitimately appear in brand/marketing copy.
     b. Collapse the long blank-line runs injections use to fake a new section.
     c. Cap length so a single field can't blow the prompt budget.
     d. Callers keep the data in clearly-labelled blocks and the trusted system
        prompt instructs the model to treat that context as DATA, never as
        instructions (see _BASE_SYSTEM in ai_service.py).
"""

import html
import re
from typing import Any, Dict, List, Optional


def sanitize_html(text: Optional[str]) -> Optional[str]:
    """HTML-escape a string. Returns None if input is None."""
    if text is None:
        return None
    return html.escape(str(text), quote=True)


def sanitize_dict(data: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """
    Return a shallow copy of `data` with the specified string fields HTML-escaped.
    Non-string values and missing keys are left untouched.
    """
    result = dict(data)
    for field in fields:
        if field in result and isinstance(result[field], str):
            result[field] = html.escape(result[field], quote=True)
    return result


# ── Prompt-injection hardening ───────────────────────────────────────────────

# Chat-template / role-control tokens across model families (Llama, ChatML,
# Mistral, etc.). These never appear in legitimate brand copy, so removing them
# is zero-false-positive.
_CONTROL_TOKEN_RE = re.compile(
    # <|im_start|>, <|eot_id|>, <|system|>
    r"<\|[^>]*?\|>"
    r"|\[/?INST\]"                             # [INST] [/INST]
    r"|<</?\s*SYS\s*>>"                        # <<SYS>> <</SYS>>
    r"|</?\s*(?:system|assistant|user)\s*>",
    # <system> </assistant> pseudo-tags
    re.IGNORECASE,
)

# ASCII control chars except tab (\x09), newline (\x0a), carriage return
# (\x0d).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def neutralize_prompt_injection(text: Any, max_chars: int = 2000) -> str:
    """
    Defang user-supplied text before it is interpolated into an LLM prompt.

    Conservative by design: it strips mechanical injection vectors without
    rewriting legitimate marketing copy (so generation quality is unaffected).
    Always returns a plain string ("" for None/empty).
    """
    if text is None:
        return ""
    s = str(text)
    s = _CONTROL_CHARS_RE.sub("", s)
    s = _CONTROL_TOKEN_RE.sub(" ", s)
    s = _MULTI_NEWLINE_RE.sub("\n\n", s)
    s = s.strip()
    if len(s) > max_chars:
        s = s[:max_chars].rstrip() + " …"
    return s


def neutralize_terms(
        values: Any,
        max_items: int = 25,
        max_chars: int = 120) -> List[str]:
    """
    Neutralize a list of short user-supplied terms (vocabulary, avoid_words,
    cta_examples, goals…). Drops empties, defangs each entry, and caps both the
    item count and per-item length. Returns a list of clean strings.
    """
    if not values:
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    out: List[str] = []
    for v in values:
        clean = neutralize_prompt_injection(v, max_chars=max_chars)
        if clean:
            out.append(clean)
        if len(out) >= max_items:
            break
    return out
