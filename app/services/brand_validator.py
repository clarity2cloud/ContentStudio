# app/services/brand_validator.py
"""
Brand Profile Completeness Validator & Knowledge Builder

The brand is the umbrella. A shallow brand profile = shallow content.
This module:
  1. Scores brand profile completeness (0-100)
  2. Identifies missing high-impact fields
  3. Builds a rich brand context block for LLM prompts using ALL available data
  4. Optionally enforces a minimum completeness threshold before generation

Used by:
  - app/api/v1/brand.py            (expose score in responses)
  - app/services/ai_service.py     (richer brand block)
  - app/services/campaign_pipeline.py
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.sanitize import neutralize_prompt_injection, neutralize_terms


# ── Field weights (sum = 100) ──────────────────────────────────────────────
FIELD_WEIGHTS: Dict[str, int] = {
    "name": 5,
    "industry": 5,
    "delivery_tone": 8,   # replaces generic `tone` — now set via Delivery Tone picker
    "voice": 10,
    "positioning": 12,
    "target_audience": 12,
    "vocabulary": 8,
    "avoid_words": 6,
    "cta_examples": 8,
    "brand_story": 14,
    "goals": 12,
}

MIN_SCORE_FOR_GENERATION = 0  # set to >0 to block generation; default = open

# ── Suggested next-best field given current state ──────────────────────────
NEXT_BEST_HINT = {
    "voice": "Add a 1-2 sentence brand voice description.",
    "positioning": "Add your one-line positioning statement.",
    "target_audience": "Describe your ICP — title, industry, pain point.",
    "goals": "List your brand's top goals (e.g. grow pipeline, reduce churn).",
    "brand_story": "Add the founding story or company narrative.",
    "cta_examples": "Add 3-5 CTAs you've found work for your audience.",
    "vocabulary": "Add 5-10 words/phrases your brand always uses.",
    "avoid_words": "Add words/phrases your brand never uses.",
    "delivery_tone": "Pick a delivery tone (Provocative, Authoritative, Insider Leak, or Confessional).",
    "industry": "Tell us your industry so we calibrate the voice.",
}


def _is_filled(value: Any, min_items: int = 1, min_chars: int = 3) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return len(value.strip()) >= min_chars
    if isinstance(value, list):
        if len(value) < min_items:
            return False
        return any(
            (isinstance(x, str) and x.strip()) or (isinstance(x, dict) and x)
            for x in value
        )
    if isinstance(value, dict):
        return len(value) >= min_items
    return bool(value)


def score_completeness(brand: Dict[str, Any]) -> Dict[str, Any]:
    """Score a brand dict and return {score, filled, missing, next_best}."""
    score = 0
    filled: List[str] = []
    missing: List[str] = []
    for field, weight in FIELD_WEIGHTS.items():
        val = brand.get(field)
        if _is_filled(val):
            score += weight
            filled.append(field)
        else:
            missing.append(field)

    score = min(100, score)
    next_best_field: Optional[str] = None
    if missing:
        next_best_field = max(missing, key=lambda f: FIELD_WEIGHTS.get(f, 0))

    return {
        "score": score,
        "tier": _tier(score),
        "filled": filled,
        "missing": missing,
        "next_best": {
            "field": next_best_field,
            "hint": NEXT_BEST_HINT.get(
                next_best_field,
                "Fill more brand fields to improve generation quality.") if next_best_field else None,
        } if next_best_field else None,
    }


def _tier(score: int) -> str:
    if score >= 85:
        return "world_class"
    if score >= 70:
        return "strong"
    if score >= 50:
        return "decent"
    if score >= 30:
        return "shallow"
    return "skeletal"


def require_minimum_score(brand: Dict[str, Any]) -> Tuple[bool, Dict]:
    """If MIN_SCORE_FOR_GENERATION > 0, gate generation here."""
    report = score_completeness(brand)
    if MIN_SCORE_FOR_GENERATION <= 0:
        return True, report
    return report["score"] >= MIN_SCORE_FOR_GENERATION, report


# ── Build a rich brand context block for LLM prompts ───────────────────────
def build_brand_block(brand: Dict[str, Any], max_chars: int = 4500) -> str:
    """
    Build a structured brand-context block to inject into LLM prompts.
    Psychology directives are placed FIRST so the LLM sees them before
    any other context — this maximises their influence on generation.
    """
    if not brand:
        return ""

    parts: List[str] = []

    # ── Content psychology — placed FIRST for maximum LLM attention ──────────
    # Read both fields before the main block so we can hoist them to the top.
    # These are user-supplied; neutralize + cap (they should be short tokens).
    _psychological_trigger = neutralize_prompt_injection(
        brand.get("psychological_trigger"), max_chars=40).strip().lower()
    _delivery_tone = neutralize_prompt_injection(
        brand.get("delivery_tone"), max_chars=40).strip().lower()

    _trigger_instructions = {
        "fear": "Paint what the reader RISKS LOSING if they do nothing. Every line must amplify loss-aversion. Inaction must feel costly and painful.",
        "curiosity": "Open INFORMATION GAPS the reader cannot resist. Tease the answer — never reveal it upfront. Each sentence should make them hunger for the next.",
        "controversy": "Take a BOLD CONTRARIAN STANCE that challenges a widely-held belief. Spark debate. The reader should feel compelled to agree or argue.",
        "ego": "Make the reader feel SMART, ELITE, and AHEAD OF THE CURVE. Validate their identity. Treat them as the informed minority who gets it.",
        "desire": "Paint a VIVID, SENSORY picture of the transformation the reader craves. Name the specific outcome — the feeling, the life after the change. Make the aspiration feel real, close, and achievable RIGHT NOW.",
    }
    _delivery_instructions = {
        "provocative": "Open with a BOLD, POLARISING statement that stops the scroll. Use short, punchy sentences. Challenge the reader's assumptions head-on.",
        "authoritative": "Write as a RECOGNISED EXPERT. Every claim is specific and confident. Zero hedging language — no 'might', 'could', or 'perhaps'.",
        "insider_leak": "Frame every line as EXCLUSIVE, BEHIND-THE-SCENES intelligence. Use 'what most people don't know…' and 'insider' framing throughout.",
        "confessional": "Write in RAW FIRST-PERSON. Open with a personal admission or vulnerability. Maintain exposed, honest voice from the first word to the last — this is a confession, not a marketing message.",
    }

    if _psychological_trigger or _delivery_tone:
        psych_lines = [
            "⚠️  MANDATORY CONTENT PSYCHOLOGY — APPLY TO EVERY SENTENCE:"]
        if _psychological_trigger:
            instr = _trigger_instructions.get(
                _psychological_trigger,
                f"Apply the '{_psychological_trigger.title()}' psychological trigger throughout.")
            psych_lines.append(
                f"  TRIGGER → {_psychological_trigger.upper()}: {instr}")
        if _delivery_tone:
            instr = _delivery_instructions.get(
                _delivery_tone,
                f"Apply the '{_delivery_tone.replace('_', ' ').title()}' delivery style throughout."
            )
            psych_lines.append(
                f"  DELIVERY → {_delivery_tone.replace('_',' ').upper()}: {instr}")
        parts.append("\n".join(psych_lines))

    # ── Brand identity ──────────────────────────────────────────────────────
    # Every value below is user-supplied and flows into the LLM prompt, so each
    # is run through neutralize_prompt_injection() before interpolation.
    name = neutralize_prompt_injection(
        brand.get("name"), max_chars=120).strip()
    if name:
        parts.append(f"BRAND: {name}")

    # Website URL — include so LLM can naturally mention/link it in content
    website_url = neutralize_prompt_injection(
        brand.get("website_url"), max_chars=300).strip()
    if website_url:
        parts.append(f"WEBSITE: {website_url}")

    company_name = neutralize_prompt_injection(
        brand.get("company_name"), max_chars=120).strip()
    if company_name and company_name.lower() != name.lower():
        parts.append(f"COMPANY: {company_name}")

    industry = neutralize_prompt_injection(
        brand.get("industry"), max_chars=120).strip()
    if industry:
        parts.append(f"INDUSTRY: {industry}")

    tone = neutralize_prompt_injection(
        brand.get("tone"), max_chars=120).strip()
    voice = neutralize_prompt_injection(
        brand.get("voice"), max_chars=400).strip()
    delivery_tone = _delivery_tone  # already neutralized + lowercased above
    # Use explicit tone if set; fall back to delivery_tone label so the LLM
    # always gets a clear tone descriptor in the VOICE line.
    _delivery_labels = {
        "provocative": "provocative",
        "authoritative": "authoritative",
        "insider_leak": "insider/exclusive",
        "confessional": "confessional/honest",
    }
    effective_tone = tone or _delivery_labels.get(delivery_tone, "")
    if effective_tone or voice:
        bits = []
        if effective_tone:
            bits.append(f"tone={effective_tone}")
        if voice:
            bits.append(f"voice={voice}")
        parts.append("VOICE: " + ", ".join(bits))

    positioning = neutralize_prompt_injection(
        brand.get("positioning"), max_chars=400).strip()
    if positioning:
        parts.append(f"POSITIONING: {positioning}")

    audience = neutralize_prompt_injection(
        brand.get("target_audience"), max_chars=600).strip()
    if audience:
        parts.append(f"TARGET AUDIENCE: {audience}")

    story = neutralize_prompt_injection(
        brand.get("brand_story"),
        max_chars=2000).strip()
    if story:
        parts.append(f"BRAND STORY: {story}")

    goals = neutralize_terms(brand.get("goals"), max_items=10, max_chars=200)
    if goals:
        parts.append("BRAND GOALS: " + ", ".join(goals))

    vocab = neutralize_terms(
        brand.get("vocabulary"),
        max_items=25,
        max_chars=120)
    if vocab:
        parts.append("ALWAYS USE: " + ", ".join(vocab))

    avoid = neutralize_terms(
        brand.get("avoid_words"),
        max_items=25,
        max_chars=120)
    if avoid:
        parts.append("NEVER USE: " + ", ".join(avoid))

    ctas = neutralize_terms(
        brand.get("cta_examples"),
        max_items=10,
        max_chars=200)
    if ctas:
        parts.append("APPROVED CTAs: " + " | ".join(ctas))

    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[:max_chars] + "\n...[truncated]"
    return blob


def is_world_class(brand: Dict[str, Any]) -> bool:
    return score_completeness(brand)["score"] >= 85
