# app/services/content_validator.py
#
# Post-processing validator for AI-generated content
# Removes meta-commentary, hallucinated stats, fluff, and other quality issues
#

import re
from typing import Dict, Any


class ContentValidator:
    """Validates and cleans AI-generated content before returning to user."""

    # PHASE 1 FIX: Expanded meta-commentary patterns
    META_PATTERNS = [
        # Original patterns
        r'\*\*Note on Compliance.*?\*\*.*?(?=\n\n|\Z)',  # Compliance notes
        r'\*\*Revised.*?\*\*.*?(?=\n\n|\Z)',  # Revision notes
        r'Note:.*?(?=\n|$)',  # Generic notes
        r'Meta-commentary:.*?(?=\n|$)',  # Meta-commentary
        r'\(As an AI.*?\)',  # AI apologies
        r'I\'ve crafted.*?(?=\n|$)',  # Meta-writing phrases
        r'Certainly[!.]*(?=\n|$)',  # Filler phrases
        r'Sure[!.]*(?=\n|$)',  # Filler phrases

        # PHASE 1 NEW PATTERNS
        # Section word count labels: **(Word Count for Section X: XXX)**
        r'\*\*Word Count.*?\*\*',
        # Total word count statements: **TOTAL WORD COUNT: XXX**
        r'\*\*TOTAL\s+WORD\s+COUNT:.*?\*\*',
        # Bolded bracketed meta-statements: **[Greeting Omitted...]**
        r'\*\*\[.*?\]\*\*',
        # Observation and Adjustment headers
        r'\*\*Observation\s+and\s+Adjustment.*?\*\*',
        # Adjusted content headers
        r'\*\*Adjusted\s+.*?for Brand Context.*?\*\*',
        # "Avoiding Manipulative" meta-sections
        r'\*\*Avoiding\s+[^*]+Tactics.*?\*\*',
        # Word Count statements without bolding
        r'^Word Count[:\s].*$',
        r'^TOTAL WORD COUNT:.*$',
        # Bolded "Removed as per formatting rules"
        r'\*\*Removed as per formatting rules.*?\*\*',
        # Bolded rewriting/compliance notes
        r'\*\*Rewritten to comply with.*?\*\*',

        # PHASE 3 NEW PATTERNS - Additional meta-commentary
        # Compliance checklists and sections
        r'\*\*COMPLIANCE\s+CHECKLIST\*\*.*?(?=\n\n|$)',
        r'\*\*FINAL\s+ADJUSTMENT.*?\*\*.*?(?=\n\n|$)',
        r'\*\*ADJUSTMENT\s+FOR\s+BRAND.*?\*\*.*?(?=\n\n|$)',
        # NOTE sections (meta-commentary)
        r'^\*\*NOTE\*\*:.*?(?=\n\n|$)',
        r'^NOTE:.*?(?=\n\n|$)',
        # Metadata-like sections at end of content
        r'---\s*\n.*?(?=\Z)',
        # Separator + everything after (metadata sections)
        # "ADJUSTMENT FOR" sections (unbolded)
        r'^\*\*ADJUSTMENT\s+FOR\s+.*?\*\*',
        # "Since the reader's name" meta explanations
        r'Since\s+the\s+reader.*?(?=\n\n|$)',
    ]

    # PHASE 1 FIX: Expanded hallucination patterns
    HALLUCINATION_PATTERNS = [
        # Original patterns
        r'\b(\d{1,3})%\s+of\s+(startups|marketers|businesses|entrepreneurs|companies|founders|consumers|managers|people)',

        # PHASE 1 NEW PATTERNS - catch "X%
        # increase/boost/improvement/reduction"
        r'\b(\d{1,3})%\s+(increase|boost|improvement|growth|reduction|jump|spike|rise|surge|bump|uptick)',
        # "saw a X% increase" pattern
        r'saw\s+a\s+(\d{1,3})%\s+(increase|boost|improvement|reduction)',
        # "resulted in a X% increase" pattern
        r'resulted?\s+in\s+a\s+(\d{1,3})%\s+(increase|boost|improvement|reduction)',
        # "by X% increase" pattern
        r'by\s+(\d{1,3})%\s+(increase|boost|improvement|reduction)',
        # Generic percentage claims (catches 400%, extreme numbers)
        r'(\d{2,3})%\s+(revenue|sales|traffic|engagement|growth|ROI)',
        # Specific unverified amounts: $X million/billion
        r'\$\d+[,.]?\d*\s+(million|billion|k)',
        # Original patterns for compatibility
        # exclude legitimate discounts
        r'increase\s+(by\s+)?(\d{1,3})%(?!\s+(?:discount|off))',
        r'boost\s+(by\s+)?(\d{1,3})%',
        r'growth\s+(of\s+)?(\d{1,3})%',
        r'(up\s+to|around|approximately)\s+(\d+)%',
    ]

    # PHASE 1 NEW: Extreme claim detection
    EXTREME_CLAIMS = [
        r'(\d{3,})%\s+(increase|growth|revenue|sales)',  # 100%+ claims
        # Quick extreme growth
        r'within\s+(\d+)\s+(days|weeks|months).*?(\d{3,})%',
        # Extreme + short timeframe
        r'(\d{3,})%\s+.*?in\s+just\s+(\d+)\s+(days|weeks|months)',
    ]

    # Fluff phrases to reduce
    FLUFF_PHRASES = [
        'in today\'s fast-paced world',
        'at the end of the day',
        'needless to say',
        'think outside the box',
        'move the needle',
        'best practices',
        'value-add',
        'pain points',
        'leverage',
        'synergy',
        'revolutionize',
        'game-changer',
        'cutting-edge',
        'empower',
        'seamless',
        'robust',
        'scalable',
        'paradigm',
        'disruptive',
        'transform',
        'unlock',
        'harness',
        'supercharge',
        'skyrocket',
        'groundbreaking',
        'innovative',
        'comprehensive',
        'utilize',
        'facilitate',
        'spearhead',
        'foster',
        'catalyze',
        'impactful',
        'actionable',
        'holistic',
        'ecosystem',
        'thought leader',
        'AI-powered',
        'next-level',
        'state-of-the-art',
        'best-in-class',
        'world-class',
        'industry-leading',
    ]

    @staticmethod
    def remove_meta_commentary(content: str) -> str:
        """Remove internal notes, compliance explanations, meta-commentary."""
        cleaned = content
        for pattern in ContentValidator.META_PATTERNS:
            cleaned = re.sub(
                pattern,
                '',
                cleaned,
                flags=re.DOTALL | re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def flag_hallucinated_stats(content: str) -> Dict[str, Any]:
        """Identify potential hallucinated statistics and extreme claims."""
        flags = []

        # Check standard hallucination patterns
        for pattern in ContentValidator.HALLUCINATION_PATTERNS:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                flags.append({
                    'type': 'unverified_stat',
                    'match': match.group(),
                    'position': match.start(),
                    'recommendation': 'Replace with verified data or remove if unverified'
                })

        # PHASE 1 NEW: Check for extreme claims (400%+ growth, extreme ROI,
        # etc.)
        for pattern in ContentValidator.EXTREME_CLAIMS:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                flags.append({
                    'type': 'extreme_claim',
                    'match': match.group(),
                    'position': match.start(),
                    'recommendation': 'Extreme percentage claim detected. Verify with sources or remove. (Claims over 100% require strong evidence.)'
                })

        return {
            'hallucinations_found': len(flags),
            'details': flags,
            'unverified_count': len([f for f in flags if f['type'] == 'unverified_stat']),
            'extreme_count': len([f for f in flags if f['type'] == 'extreme_claim']),
        }

    @staticmethod
    def reduce_fluff(content: str) -> str:
        """Reduce overuse of fluff phrases."""
        cleaned = content
        for phrase in ContentValidator.FLUFF_PHRASES:
            # Case-insensitive replacement
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            cleaned = pattern.sub('', cleaned)

        # Clean up extra whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    @staticmethod
    def remove_repeated_lines(content: str, threshold: int = 3) -> str:
        """Remove or consolidate repeated lines/phrases."""
        lines = content.split('\n')
        seen = {}
        cleaned = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if not cleaned or cleaned[-1].strip():  # Keep single blank lines
                    cleaned.append(line)
            else:
                # Count similar lines
                normalized = ' '.join(stripped.lower().split())
                if normalized in seen:
                    seen[normalized] += 1
                    # Skip if we've seen this line too many times
                    if seen[normalized] > 1:
                        continue
                else:
                    seen[normalized] = 1
                cleaned.append(line)

        return '\n'.join(cleaned).strip()

    @staticmethod
    def validate_structure(content: str, content_type: str) -> Dict[str, Any]:
        """Validate content matches expected structure for type."""
        issues = []

        if content_type in ['email', 'newsletter']:
            # PHASE 1 FIX: Better greeting detection (expanded patterns)
            greeting_pattern = r'(^dear|^hello|^hi|^greetings|^subject:|^\*\*greeting|opening hook)'
            if not re.search(
                    greeting_pattern,
                    content,
                    re.IGNORECASE | re.MULTILINE):
                issues.append('Missing greeting/opening')

            # PHASE 1 FIX: Check closing in last 500 chars (not just 200) to
            # handle longer content
            closing_pattern = r'(best|regards|cheers|sincerely|warm regards|thanks|thank you|best regards|yours|signature)'
            last_500_chars = content[-500:] if len(content) > 500 else content
            if not re.search(closing_pattern, last_500_chars, re.IGNORECASE):
                issues.append('Missing proper closing')

        if content_type in [
            'blog',
            'landing_page',
            'linkedin_article',
                'linkedin_post']:
            # PHASE 1 FIX: Better title detection (matches both "Title:" and
            # "**Title:**" formats)
            title_pattern = r'(^\s*title:\s*|^\s*\*\*title:\s*|^\s*#+\s+title)'
            if not re.search(
                    title_pattern,
                    content,
                    re.IGNORECASE | re.MULTILINE):
                issues.append('Missing title section')

        return {'structure_valid': len(issues) == 0, 'issues': issues}

    @staticmethod
    def check_brand_consistency(
            content: str, brand_context: str = None) -> Dict[str, Any]:
        """PHASE 2: Check for brand/product type mismatches."""
        issues = []

        if not brand_context:
            return {'brand_consistent': True, 'issues': []}

        # Extract brand type hints from context (prioritize retail > saas >
        # service)
        context_lower = brand_context.lower()
        is_retail = any(
            keyword in context_lower for keyword in [
                'retail',
                'shoes',
                'footwear',
                'fashion',
                'apparel',
                'products',
                'store',
                'shop'])
        is_saas = any(
            keyword in context_lower for keyword in [
                'saas',
                'software',
                'platform',
                'tool',
                'analytics',
                'api',
                'dashboard']) and not is_retail
        is_service = any(
            keyword in context_lower for keyword in [
                'service',
                'consulting',
                'agency']) and not (
            is_retail or is_saas)

        # Check for mismatched language (mutually exclusive)
        detected_type = 'unknown'

        if is_retail:
            detected_type = 'retail'
            # Retail brand should NOT use SaaS language
            saas_indicators = [
                r'(premium\s+features|analytics\s+dashboard|priority\s+support)',
                r'(subscription|tier(?!\s+material)|upgrade(?!\s+to|d\s+to)|addon|integration)',
                r'(api\s+access|api\s+key|deployment)',
            ]
            for pattern in saas_indicators:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    issues.append(
                        f'Retail brand using SaaS language: "{match.group()}"')

        elif is_saas:
            detected_type = 'saas'
            # SaaS brand should NOT use retail language
            retail_indicators = [
                r'(shoe\b|footwear|apparel|clothing)',
                r'(store\b|retail|boutique|inventory)',
            ]
            for pattern in retail_indicators:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    issues.append(
                        f'SaaS brand using retail language: "{match.group()}"')

        elif is_service:
            detected_type = 'service'

        return {
            'brand_consistent': len(issues) == 0,
            'issues': issues,
            'detected_type': detected_type
        }

    @staticmethod
    def check_tone_consistency(
            content: str, requested_tone: str = None) -> Dict[str, Any]:
        """Check for tone inconsistencies and tone-inappropriate emojis."""
        issues = []
        emoji_count = len(re.findall(r'[😀-🙏🌀-🗿🚀-🛿]', content))

        if requested_tone and requested_tone.lower() == 'professional':
            if emoji_count > 0:
                issues.append(
                    f'Found {emoji_count} emojis in professional tone content (should be 0)')

            # Check for casual language
            casual_phrases = [
                'lol',
                'haha',
                'btw',
                'imho',
                'gonna',
                'wanna',
                'kinda',
                'sorta']
            for phrase in casual_phrases:
                if re.search(rf'\b{phrase}\b', content, re.IGNORECASE):
                    issues.append(
                        f'Found casual phrase "{phrase}" in professional tone')

        elif requested_tone and requested_tone.lower() in ['formal', 'authoritative']:
            if emoji_count > 0:
                issues.append(
                    f'Found {emoji_count} emojis in formal tone (should be 0)')

        elif requested_tone and requested_tone.lower() in ['casual', 'friendly']:
            # These can have emojis, but check for sudden formality shifts
            if re.search(
                r'hereby|thereby|furthermore|aforementioned',
                content,
                    re.IGNORECASE):
                issues.append(
                    'Found overly formal language in casual/friendly tone')

        return {
            'tone_consistent': len(issues) == 0,
            'issues': issues,
            'emoji_count': emoji_count,
        }

    # ── PLATFORM HARD CONSTRAINTS (per channel) ──────────────────────────
    @staticmethod
    def check_platform_constraints(
            content: str, platform: str) -> Dict[str, Any]:
        """
        Hard length / format checks per platform.
        Returns {'passes': bool, 'violations': [..], 'metrics': {...}, 'must_regenerate': bool}.

        These map to PLATFORM_PROFILES.constraints in app/services/platform_personas.py.
        """
        try:
            from app.services.platform_personas import get_constraints, normalize_platform
        except Exception:
            return {
                'passes': True,
                'violations': [],
                'metrics': {},
                'must_regenerate': False}

        p = normalize_platform(platform)
        cons = get_constraints(p) or {}
        if not cons or not content:
            return {
                'passes': True,
                'violations': [],
                'metrics': {},
                'must_regenerate': False}

        violations = []
        metrics = {
            'char_count': len(content),
            'word_count': len(content.split()),
            'hashtag_count': len(re.findall(r'#\w+', content)),
        }
        # Char limits (HARD)
        if 'char_max' in cons and metrics['char_count'] > cons['char_max']:
            violations.append(
                f"Exceeds char_max: {metrics['char_count']} > {cons['char_max']}")
        if 'char_min' in cons and metrics['char_count'] < cons['char_min']:
            violations.append(
                f"Below char_min: {metrics['char_count']} < {cons['char_min']}")
        # Word limits (SOFT — flag only)
        if 'word_max' in cons and metrics['word_count'] > cons['word_max'] * 1.20:
            violations.append(
                f"Significantly exceeds word_max: {metrics['word_count']} > {int(cons['word_max'] * 1.20)}")
        if 'word_min' in cons and metrics['word_count'] < int(
                cons['word_min'] * 0.70):
            violations.append(
                f"Significantly below word_min: {metrics['word_count']} < {int(cons['word_min'] * 0.70)}")
        # Hashtag limits
        if 'hashtag_max' in cons and metrics['hashtag_count'] > cons['hashtag_max']:
            violations.append(
                f"Too many hashtags: {metrics['hashtag_count']} > {cons['hashtag_max']}")
        if 'hashtag_min' in cons and metrics['hashtag_count'] < cons['hashtag_min']:
            violations.append(
                f"Too few hashtags: {metrics['hashtag_count']} < {cons['hashtag_min']}")
        # Title char check (look for explicit 'Title:' line)
        if 'title_max_chars' in cons:
            m = re.search(
                r'^\s*title\s*:\s*(.+?)$',
                content,
                re.IGNORECASE | re.MULTILINE)
            if m:
                tlen = len(m.group(1).strip())
                if tlen > cons['title_max_chars']:
                    violations.append(
                        f"Title exceeds {cons['title_max_chars']} chars: {tlen}")
                metrics['title_chars'] = tlen

        # Hard regen if SMS or Tweet over limit, or headline too long
        must_regen = any(
            ("Exceeds char_max" in v) or ("Title exceeds" in v)
            for v in violations
        )
        return {
            'passes': len(violations) == 0,
            'violations': violations,
            'metrics': metrics,
            'must_regenerate': must_regen,
            'constraints': cons,
        }

    @staticmethod
    def enforce_hard_limits(content: str, platform: str) -> str:
        """
        DEPRECATED truncation path — kept as a no-op so callers don't break.

        We DO NOT truncate user content anymore (truncation mid-sentence is worse
        than slightly-over-limit). The regen loop handles compliance instead,
        and if the model still produces over-limit content we return it whole.
        """
        return content

    @staticmethod
    def clean_and_validate(content: str,
                           content_type: str = 'blog',
                           tone: str = None,
                           brand_context: str = None,
                           platform: str = None) -> Dict[str,
                                                         Any]:
        """
        Complete cleaning and validation pipeline.
        Returns cleaned content + validation report.

        `platform` enables platform-specific HARD constraint checks (SMS ≤160, Tweet ≤280, etc.)
        """
        # 1. Remove meta-commentary
        cleaned = ContentValidator.remove_meta_commentary(content)

        # 2. Check for hallucinations (flag but don't remove - user should
        # review)
        hallucination_report = ContentValidator.flag_hallucinated_stats(
            cleaned)

        # 3. Remove repeated lines
        cleaned = ContentValidator.remove_repeated_lines(cleaned)

        # 4. Validate structure
        structure_report = ContentValidator.validate_structure(
            cleaned, content_type)

        # 5. Check tone consistency
        tone_report = ContentValidator.check_tone_consistency(cleaned, tone)

        # 6. Brand consistency
        brand_report = ContentValidator.check_brand_consistency(
            cleaned, brand_context)

        # 7. NEW: Platform hard constraints (SMS ≤160, Tweet ≤280, etc.)
        platform_key = platform or content_type
        platform_report = ContentValidator.check_platform_constraints(
            cleaned, platform_key)

        return {
            'cleaned_content': cleaned,
            'quality_report': {
                'meta_commentary_removed': True,
                'hallucinations_flagged': hallucination_report['hallucinations_found'] > 0,
                'hallucinations_count': hallucination_report['hallucinations_found'],
                'unverified_stats': hallucination_report.get('unverified_count', 0),
                'extreme_claims': hallucination_report.get('extreme_count', 0),
                'hallucination_details': hallucination_report['details'][:5],
                'structure_valid': structure_report['structure_valid'],
                'structure_issues': structure_report['issues'],
                'tone_consistent': tone_report['tone_consistent'],
                'tone_issues': tone_report['issues'],
                'brand_consistent': brand_report['brand_consistent'],
                'brand_issues': brand_report['issues'],
                'platform_valid': platform_report['passes'],
                'platform_violations': platform_report['violations'],
                'platform_metrics': platform_report['metrics'],
                'must_regenerate': platform_report['must_regenerate'],
                'overall_quality_score': ContentValidator._calculate_quality_score(
                    hallucination_report, structure_report, tone_report, brand_report
                )
            }
        }

    @staticmethod
    def _calculate_quality_score(
            hallucination_report,
            structure_report,
            tone_report,
            brand_report) -> str:
        """Calculate overall quality score."""
        issues = 0

        if hallucination_report['hallucinations_found'] > 5:
            issues += 3
        elif hallucination_report['hallucinations_found'] > 0:
            issues += 1

        if not structure_report['structure_valid']:
            issues += 1

        if not tone_report['tone_consistent']:
            issues += 1

        if not brand_report['brand_consistent']:
            issues += 2

        if issues == 0:
            return 'excellent'
        elif issues <= 1:
            return 'good'
        elif issues <= 2:
            return 'fair'
        else:
            return 'needs_review'
