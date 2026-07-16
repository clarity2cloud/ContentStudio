# app/api/v1/templates.py
"""
Content Templates — pre-built, brand-aware prompt templates for the most
common content needs of startups, agencies, and marketing teams.

Templates save users 80% of the setup time by giving them a structured
starting point rather than a blank canvas.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List
from pydantic import BaseModel, Field
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.services.ai_service import ai_service
from app.services.content_validator import ContentValidator
from app.utils.logger import logger

router = APIRouter(prefix="/templates", tags=["Content Templates"])


def _resolve_brand_context(db: AppwriteClient, user_id: str, brand_id: Optional[str]) -> str:
    bid = brand_id
    if not bid:
        res = db.table("brand_profiles").select("id")\
                .eq("user_id", user_id).eq("is_default", True).execute()
        if res.data:
            bid = res.data[0]["id"]
    if not bid:
        return ""
    res = db.table("brand_profiles").select("*").eq("id", bid).execute()
    if not res.data:
        return ""
    b, parts = res.data[0], []
    if b.get("name"):            parts.append(f"Brand: {b['name']}")
    if b.get("industry"):        parts.append(f"Industry: {b['industry']}")
    if b.get("tone"):            parts.append(f"Tone: {b['tone']}")
    if b.get("voice"):           parts.append(f"Voice: {b['voice']}")
    if b.get("positioning"):     parts.append(f"Positioning: {b['positioning']}")
    if b.get("target_audience"): parts.append(f"Audience: {b['target_audience']}")
    if b.get("vocabulary"):      parts.append(f"Always use: {', '.join(b['vocabulary'])}")
    if b.get("avoid_words"):     parts.append(f"Never use: {', '.join(b['avoid_words'])}")
    if b.get("cta_examples"):    parts.append(f"CTA: {', '.join(b['cta_examples'])}")
    return "\n".join(parts)


# ─── Template catalogue ───────────────────────────────────────
TEMPLATES = [
    # ── Product launch ─────────────────────────────────────────
    {
        "id":          "product-launch-blog",
        "name":        "Product Launch Blog",
        "category":    "Launch",
        "description": "A full announcement blog post for a new product or feature.",
        "channels":    ["blog"],
        "variables":   ["product_name", "key_feature", "target_audience", "launch_date"],
        "use_case":    "Startups, SaaS, E-commerce",
    },
    {
        "id":          "product-launch-suite",
        "name":        "Product Launch Suite",
        "category":    "Launch",
        "description": "Full launch content kit: blog + email + LinkedIn + X/Twitter + Instagram.",
        "channels":    ["blog", "email", "linkedin", "twitter", "instagram"],
        "variables":   ["product_name", "key_feature", "target_audience", "launch_date"],
        "use_case":    "Startups, SaaS",
    },
    # ── Thought leadership ──────────────────────────────────────
    {
        "id":          "thought-leadership-linkedin",
        "name":        "Thought Leadership (LinkedIn)",
        "category":    "Thought Leadership",
        "description": "A personal-brand LinkedIn post sharing insight or opinion on an industry topic.",
        "channels":    ["linkedin"],
        "variables":   ["topic", "key_insight", "audience"],
        "use_case":    "Founders, Consultants, Executives",
    },
    {
        "id":          "industry-hot-take",
        "name":        "Industry Hot Take",
        "category":    "Thought Leadership",
        "description": "A provocative, opinion-led post designed to spark conversation.",
        "channels":    ["twitter", "linkedin"],
        "variables":   ["topic", "contrarian_view", "supporting_evidence"],
        "use_case":    "Founders, Agencies",
    },
    # ── Email marketing ─────────────────────────────────────────
    {
        "id":          "welcome-email",
        "name":        "Welcome Email",
        "category":    "Email",
        "description": "Warm onboarding email for new subscribers or customers.",
        "channels":    ["email"],
        "variables":   ["product_name", "key_benefit", "next_step"],
        "use_case":    "SaaS, E-commerce, Newsletters",
    },
    {
        "id":          "newsletter-edition",
        "name":        "Weekly Newsletter",
        "category":    "Email",
        "description": "Structured newsletter with intro, 3 key points, and a CTA.",
        "channels":    ["email"],
        "variables":   ["edition_topic", "tip_1", "tip_2", "tip_3", "cta"],
        "use_case":    "Creators, Agencies, SaaS",
    },
    {
        "id":          "re-engagement-email",
        "name":        "Re-engagement Email",
        "category":    "Email",
        "description": "Win back inactive subscribers or churned customers.",
        "channels":    ["email"],
        "variables":   ["product_name", "incentive", "urgency"],
        "use_case":    "SaaS, E-commerce",
    },
    # ── Social media ────────────────────────────────────────────
    {
        "id":          "weekly-tip-carousel",
        "name":        "Weekly Tip (Social)",
        "category":    "Social Media",
        "description": "A tip-of-the-week post for Instagram, LinkedIn, or Facebook.",
        "channels":    ["instagram", "linkedin", "facebook"],
        "variables":   ["tip", "context", "cta"],
        "use_case":    "Creators, Educators, Coaches",
    },
    {
        "id":          "case-study-post",
        "name":        "Customer Story / Case Study",
        "category":    "Social Media",
        "description": "A social post highlighting a customer win or case study result.",
        "channels":    ["linkedin", "twitter"],
        "variables":   ["customer_name", "problem", "solution", "result"],
        "use_case":    "B2B SaaS, Agencies, Consultants",
    },
    {
        "id":          "behind-the-scenes",
        "name":        "Behind the Scenes",
        "category":    "Social Media",
        "description": "An authentic BTS post that humanises your brand.",
        "channels":    ["instagram", "linkedin"],
        "variables":   ["topic", "insight", "audience"],
        "use_case":    "All brands",
    },
    # ── SEO & content marketing ─────────────────────────────────
    {
        "id":          "seo-pillar-blog",
        "name":        "SEO Pillar Blog Post",
        "category":    "Blog",
        "description": "Long-form SEO-optimised pillar article (1000+ words) targeting a key search term.",
        "channels":    ["blog"],
        "variables":   ["keyword", "audience", "subtopics"],
        "use_case":    "Content marketers, SaaS, Agencies",
    },
    {
        "id":          "listicle-blog",
        "name":        "Listicle Blog Post",
        "category":    "Blog",
        "description": "\"Top 10\" or \"X ways to...\" style article — high CTR, easy to read.",
        "channels":    ["blog"],
        "variables":   ["topic", "number_of_items", "audience"],
        "use_case":    "All industries",
    },
    # ── Growth & acquisition ────────────────────────────────────
    {
        "id":          "referral-campaign",
        "name":        "Referral Campaign",
        "category":    "Growth",
        "description": "Email + social posts to drive referrals from existing users.",
        "channels":    ["email", "twitter", "instagram"],
        "variables":   ["product_name", "incentive", "referral_cta"],
        "use_case":    "SaaS, E-commerce, Apps",
    },
    {
        "id":          "event-announcement",
        "name":        "Event / Webinar Announcement",
        "category":    "Events",
        "description": "Multi-channel announcement for a webinar, workshop, or live event.",
        "channels":    ["email", "linkedin", "twitter"],
        "variables":   ["event_name", "date", "key_benefit", "registration_link"],
        "use_case":    "All brands",
    },
    # ── Educational Content ─────────────────────────────────────
    {
        "id":          "how-to-guide",
        "name":        "How-To Guide / Tutorial",
        "category":    "Educational",
        "description": "Step-by-step tutorial that teaches your audience a valuable skill. Great for SEO, builds authority, drives engagement.",
        "channels":    ["blog", "email"],
        "variables":   ["topic", "target_skill", "difficulty_level", "tools_needed"],
        "use_case":    "Educators, Creators, SaaS, Coaches",
    },
    {
        "id":          "industry-report",
        "name":        "Industry Report / Whitepaper",
        "category":    "Educational",
        "description": "Research-backed report or in-depth guide that establishes thought leadership and generates qualified leads.",
        "channels":    ["blog", "email", "linkedin"],
        "variables":   ["topic", "key_findings", "methodology", "target_industry"],
        "use_case":    "B2B SaaS, Agencies, Consultants, Research firms",
    },
    {
        "id":          "research-summary",
        "name":        "Research Finding Summary",
        "category":    "Educational",
        "description": "Share surprising data or research insights in an easy-to-digest format. Highly shareable and builds credibility.",
        "channels":    ["blog", "twitter", "linkedin"],
        "variables":   ["research_topic", "key_statistic", "implication", "source"],
        "use_case":    "Data analysts, Marketers, Tech companies",
    },
    {
        "id":          "best-practice-guide",
        "name":        "Best Practice / Tips Series",
        "category":    "Educational",
        "description": "Share actionable, proven strategies that help your audience solve real problems. Build trust and loyalty.",
        "channels":    ["email", "linkedin", "blog"],
        "variables":   ["practice_topic", "key_tips", "expected_results"],
        "use_case":    "Coaches, Educators, Agencies, Consultants",
    },
    # ── Podcast ──────────────────────────────────────────────────
    {
        "id":          "podcast-announcement",
        "name":        "Podcast Episode Announcement",
        "category":    "Podcast",
        "description": "Promote new podcast episodes across email & social. Audio content builds loyal audiences and drives repeat listeners.",
        "channels":    ["email", "linkedin", "twitter", "instagram"],
        "variables":   ["episode_title", "guest_name", "key_topic", "podcast_link"],
        "use_case":    "Podcasters, Thought leaders, Brands",
    },
    # ── Community & Engagement ──────────────────────────────────
    {
        "id":          "discussion-starter",
        "name":        "Discussion Starter / Debate Post",
        "category":    "Community",
        "description": "Spark meaningful conversation with a thought-provoking question. High engagement = better algorithm reach.",
        "channels":    ["linkedin", "twitter", "facebook"],
        "variables":   ["question", "context", "audience_segment"],
        "use_case":    "Community builders, Thought leaders, Brands",
    },
    {
        "id":          "ugc-callout",
        "name":        "User-Generated Content Call-Out",
        "category":    "Community",
        "description": "Invite your audience to share stories, photos, reviews. Real user content is 3x more trustworthy than brand content.",
        "channels":    ["instagram", "linkedin", "twitter", "facebook"],
        "variables":   ["campaign_name", "submission_format", "prize_or_incentive"],
        "use_case":    "E-commerce, Brands, Communities, SaaS",
    },
    {
        "id":          "community-spotlight",
        "name":        "Community Member Spotlight",
        "category":    "Community",
        "description": "Celebrate your customers, users, or community members. Recognition builds loyalty and encourages participation.",
        "channels":    ["linkedin", "instagram", "twitter"],
        "variables":   ["member_name", "achievement", "story_highlight"],
        "use_case":    "Communities, SaaS, Brands, Creators",
    },
    {
        "id":          "poll-quiz-announcement",
        "name":        "Poll / Quiz Announcement",
        "category":    "Community",
        "description": "Interactive content drives 5x more engagement than static posts. You collect insights while entertaining your audience.",
        "channels":    ["linkedin", "instagram", "twitter", "facebook"],
        "variables":   ["poll_topic", "answer_options", "incentive"],
        "use_case":    "All brands, Market researchers, Creators",
    },
    # ── Product & Feature ───────────────────────────────────────
    {
        "id":          "feature-spotlight",
        "name":        "Feature Spotlight / Product Update",
        "category":    "Product",
        "description": "Announce new features in benefit-focused language. Helps users understand what's new and why they should care.",
        "channels":    ["blog", "email", "linkedin", "twitter"],
        "variables":   ["feature_name", "problem_solved", "key_benefits", "availability"],
        "use_case":    "SaaS, Apps, Tech companies",
    },
    {
        "id":          "product-comparison",
        "name":        "Product Comparison Post",
        "category":    "Product",
        "description": "Compare your solution to competitors or alternatives. Captures high-intent searchers ready to decide.",
        "channels":    ["blog", "linkedin", "twitter"],
        "variables":   ["competitor_name", "key_differences", "why_better"],
        "use_case":    "SaaS, E-commerce, Tech, Agencies",
    },
    {
        "id":          "changelog-update",
        "name":        "Product Changelog / Release Notes",
        "category":    "Product",
        "description": "Keep customers informed of updates and fixes. Reduces support tickets and shows active development.",
        "channels":    ["blog", "email", "twitter"],
        "variables":   ["version_number", "features_added", "bugs_fixed", "improvements"],
        "use_case":    "SaaS, Software, Apps, Developers",
    },
    {
        "id":          "early-access-beta",
        "name":        "Early Access / Beta Announcement",
        "category":    "Launch",
        "description": "Build hype and get early feedback. Create exclusivity and FOMO to drive sign-ups from engaged audiences.",
        "channels":    ["email", "linkedin", "twitter", "instagram"],
        "variables":   ["product_name", "key_feature", "benefit", "how_to_access"],
        "use_case":    "Startups, SaaS, Tech companies",
    },
    # ── Conversion & Sales ──────────────────────────────────────
    {
        "id":          "limited-offer",
        "name":        "Limited-Time Offer / Flash Sale",
        "category":    "Conversion",
        "description": "Create urgency with time-limited promotions. Drives immediate action and increases conversion rates.",
        "channels":    ["email", "linkedin", "instagram", "twitter"],
        "variables":   ["offer_type", "discount_or_incentive", "deadline", "call_to_action"],
        "use_case":    "E-commerce, SaaS, Agencies, Creators",
    },
    {
        "id":          "demo-consultation",
        "name":        "Demo / Free Consultation Call-To-Action",
        "category":    "Conversion",
        "description": "Offer personalized demos or consultations to high-intent prospects. Low-friction way to move leads down the funnel.",
        "channels":    ["blog", "email", "linkedin"],
        "variables":   ["service_type", "unique_value", "booking_link"],
        "use_case":    "B2B SaaS, Agencies, Consultants, Coaches",
    },
    {
        "id":          "free-resource-leadmagnet",
        "name":        "Free Resource / Lead Magnet",
        "category":    "Lead Generation",
        "description": "Offer valuable free content (checklist, template, guide) to build your email list and establish authority.",
        "channels":    ["blog", "email", "linkedin", "twitter"],
        "variables":   ["resource_type", "resource_benefit", "download_link"],
        "use_case":    "SaaS, Agencies, Coaches, Creators, B2B",
    },
    # ── Social Proof & Testimonials ─────────────────────────────
    {
        "id":          "customer-testimonial",
        "name":        "Customer Testimonial / Quote",
        "category":    "Social Proof",
        "description": "Share authentic customer feedback. Social proof increases trust and reduces purchase hesitation.",
        "channels":    ["linkedin", "instagram", "twitter"],
        "variables":   ["customer_name", "company", "quote", "result"],
        "use_case":    "SaaS, Services, E-commerce, B2B",
    },
    {
        "id":          "customer-success-quick",
        "name":        "Customer Success Story - Quick Version",
        "category":    "Social Proof",
        "description": "Shorter than case studies, easier to produce. Share quick customer wins that prove your value.",
        "channels":    ["linkedin", "instagram", "twitter", "email"],
        "variables":   ["customer_name", "problem", "solution_benefit", "result"],
        "use_case":    "SaaS, Services, Agencies",
    },
    {
        "id":          "case-study-long-form",
        "name":        "Case Study - Long Form",
        "category":    "Social Proof",
        "description": "In-depth case study with numbers, methodology, and takeaways. Drives high-quality leads and establishes credibility.",
        "channels":    ["blog", "email"],
        "variables":   ["customer_name", "challenge", "implementation", "results", "key_learnings"],
        "use_case":    "B2B SaaS, Agencies, Consultants",
    },
    # ── Seasonal & Timely ───────────────────────────────────────
    {
        "id":          "holiday-campaign",
        "name":        "Holiday / Seasonal Campaign",
        "category":    "Seasonal",
        "description": "Capitalize on seasonal buying behavior and emotional connections. Higher engagement and conversion rates.",
        "channels":    ["email", "instagram", "linkedin", "twitter"],
        "variables":   ["holiday_or_season", "offer_or_message", "deadline"],
        "use_case":    "E-commerce, Brands, Agencies, Creators",
    },
    {
        "id":          "trending-commentary",
        "name":        "Trending Topic Commentary",
        "category":    "Thought Leadership",
        "description": "Jump on trending conversations and news while relevant. Real-time content gets 3x more engagement.",
        "channels":    ["twitter", "linkedin", "blog"],
        "variables":   ["trending_topic", "your_perspective", "relevant_insight"],
        "use_case":    "Thought leaders, Journalists, Brands, Creators",
    },
    {
        "id":          "industry-trend-analysis",
        "name":        "Industry Trend Analysis",
        "category":    "Thought Leadership",
        "description": "Analyze emerging trends and explain implications for your audience. Positions you ahead of competition.",
        "channels":    ["blog", "linkedin", "email"],
        "variables":   ["trend_name", "industry_impact", "future_outlook"],
        "use_case":    "Consultants, Agencies, Tech, Thought leaders",
    },
    # ── Partnerships & Collaborations ───────────────────────────
    {
        "id":          "partnership-announcement",
        "name":        "Partnership Announcement",
        "category":    "Partnerships",
        "description": "Announce strategic partnerships or integrations. Expands reach to partner audiences and builds credibility.",
        "channels":    ["blog", "email", "linkedin", "twitter"],
        "variables":   ["partner_name", "partnership_type", "customer_benefit"],
        "use_case":    "SaaS, Tech, Agencies, Brands",
    },
    {
        "id":          "co-marketing-promo",
        "name":        "Co-Marketing Promotion",
        "category":    "Partnerships",
        "description": "Joint promotion with complementary brands. Share audience access and split marketing costs.",
        "channels":    ["email", "linkedin", "instagram", "twitter"],
        "variables":   ["partner_name", "joint_offer", "mutual_benefit"],
        "use_case":    "SaaS, Agencies, E-commerce, Tech",
    },
    {
        "id":          "guest-expert-feature",
        "name":        "Guest Expert Feature",
        "category":    "Partnerships",
        "description": "Feature a guest expert or influencer. Brings fresh perspectives, expands reach, and builds authority.",
        "channels":    ["blog", "linkedin", "email"],
        "variables":   ["expert_name", "expertise", "key_insight", "bio_and_link"],
        "use_case":    "Blogs, Podcasts, Agencies, Creators",
    },
    # ── Social Media Specific ───────────────────────────────────
    {
        "id":          "carousel-post",
        "name":        "Carousel Post Template",
        "category":    "Social Media",
        "description": "Multi-slide posts keep people scrolling longer. 3x more engagement than single-image posts on LinkedIn & Instagram.",
        "channels":    ["linkedin", "instagram"],
        "variables":   ["post_title", "slide_topics", "conclusion_cta"],
        "use_case":    "Educators, Agencies, SaaS, Creators",
    },
    {
        "id":          "announcement-news",
        "name":        "Major News / Milestone Announcement",
        "category":    "News",
        "description": "Celebrate company milestones, achievements, or major announcements. Builds team morale and brand credibility.",
        "channels":    ["blog", "email", "linkedin", "twitter"],
        "variables":   ["announcement_type", "key_details", "significance"],
        "use_case":    "All brands, Startups, Agencies",
    },
]


# ─── Routes ───────────────────────────────────────────────────
@router.get("", summary="Browse all content templates")
async def list_templates(
    category: Optional[str] = Query(None, description="Filter by category: Launch | Email | Social Media | Blog | Growth | Events | Thought Leadership"),
    channel:  Optional[str] = Query(None, description="Filter by channel: blog | email | linkedin | twitter | instagram"),
):
    """
    Returns the full template catalogue.
    Use `category` or `channel` query params to filter.
    Pass a template `id` to `/templates/{template_id}/generate` to use it.
    """
    items = TEMPLATES
    if category:
        items = [t for t in items if t["category"].lower() == category.lower()]
    if channel:
        items = [t for t in items if channel.lower() in [c.lower() for c in t["channels"]]]
    return {"total": len(items), "templates": items}


@router.get("/categories", summary="List template categories")
async def list_categories():
    cats = sorted(set(t["category"] for t in TEMPLATES))
    return {"categories": cats}


@router.get("/{template_id}", summary="Get template details",
    responses={
                404: {"description": "Not found"}
    }
)
async def get_template(template_id: str):
    tmpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tmpl


class UseTemplateRequest(BaseModel):
    variables:   dict        = Field(..., description="Key-value map of template variables")
    campaign_id: Optional[str] = Field(None, description="Attach to campaign (optional)")
    tone_override: Optional[str] = Field(None, description="Override brand tone for this generation")


@router.post("/{template_id}/generate", summary="Generate content from a template",
    responses={
                404: {"description": "Not found"}
    }
)
async def generate_from_template(
    template_id: str,
    request: UseTemplateRequest,
    brand_id: Optional[str] = Query(None, description="Brand profile ID (uses default if blank)"),



    db: AppwriteClient = Depends(get_db),
):
    """
    Fill in a template's variables and generate brand-aware content instantly.

    Example — use the `product-launch-blog` template:
    ```json
    {
      "variables": {
        "product_name": "ContentStudio AI",
        "key_feature": "one-click content repurposing",
        "target_audience": "marketing teams at startups",
        "launch_date": "April 2025"
      }
    }
    ```
    """
    user_id = "demo-user"
    tmpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")

    brand_ctx = _resolve_brand_context(db, user_id, brand_id)
    tone_val  = request.tone_override or "professional"

    # Build variable description for prompts
    var_lines = "\n".join(f"- {k}: {v}" for k, v in request.variables.items())

    results = {}
    for channel in tmpl["channels"]:
        try:
            # Call appropriate ai_service method based on channel
            if channel == "blog":
                content = await ai_service.generate_blog_post(
                    topic=request.variables.get("topic") or request.variables.get("product_name", ""),
                    keywords=request.variables.get("keywords", []),
                    tone=tone_val.lower(),
                    word_count=800,
                    brand_context=brand_ctx,
                    custom_instructions=f"Context:\n{var_lines}" if var_lines else None,
                )
                gen_content = content.get("content", "")

            elif channel == "email":
                content = await ai_service.generate_email(
                    subject=request.variables.get("subject") or request.variables.get("product_name", ""),
                    purpose=request.variables.get("purpose") or var_lines,
                    tone=tone_val.lower(),
                    recipient_name=request.variables.get("recipient_name"),
                    brand_context=brand_ctx,
                    custom_instructions=None,
                )
                gen_content = content.get("content", "")

            elif channel == "twitter":
                content = await ai_service.generate_tweet(
                    topic=request.variables.get("topic") or request.variables.get("product_name", ""),
                    tone=tone_val.lower(),
                    include_hashtags=True,
                    include_emojis=True,
                    brand_context=brand_ctx,
                    custom_instructions=f"Context:\n{var_lines}" if var_lines else None,
                )
                gen_content = content.get("content", "")

            elif channel in ["instagram", "facebook", "linkedin"]:
                content = await ai_service.generate_caption(
                    platform=channel,
                    context=var_lines,
                    tone=tone_val.lower(),
                    include_hashtags=channel != "facebook",
                    include_emojis=True,
                    brand_context=brand_ctx,
                    custom_instructions=None,
                )
                gen_content = content.get("content", "")

            else:
                # Fallback for unmapped channels
                gen_content = await ai_service._call_qwen(
                    f"Template: {tmpl['name']}\nChannel: {channel}\n"
                    f"Brand: {brand_ctx}\nTone: {tone_val}\n\n"
                    f"Variables:\n{var_lines}\n\nWrite content for {channel}."
                )

            # Validate and clean content
            validation_result = ContentValidator.clean_and_validate(
                gen_content,
                content_type=channel,
                tone=tone_val,
                brand_context=brand_ctx
            )

            cleaned_content = validation_result.get("cleaned_content", gen_content)
            quality_report = validation_result.get("quality_report", {})

            # Determine title
            title_var = request.variables.get("product_name") or request.variables.get("topic")
            title = f"[{tmpl['name']}] {title_var}" if title_var else f"[{tmpl['name']}] {channel}"

            # Save to content library with validation report
            metadata = {"template_id": template_id, "template_name": tmpl["name"]}
            if quality_report:
                metadata["validation"] = quality_report

            saved = db.table("content").insert({
                "user_id":         user_id,
                "tenant_id":       tenant_id,
                "campaign_id":     request.campaign_id,
                "brand_id":        brand_id,
                "title":           title,
                "content":         cleaned_content,
                "content_type":    channel,
                "status":          "draft",
                "metadata":        metadata,
                "quality_score":   quality_report.get("overall_quality_score", "fair"),
            }).execute()

            content_id = saved.data[0]["id"] if saved.data else None

            results[channel] = {
                "content":            cleaned_content,
                "content_id":         content_id,
                "validation":         quality_report,
                "quality_score":      quality_report.get("overall_quality_score", "fair"),
                "hallucinations_flagged": quality_report.get("hallucinations_flagged", False),
                "hallucinations_count":   quality_report.get("hallucinations_count", 0),
                "structure_valid":    quality_report.get("structure_valid", True),
                "tone_consistent":    quality_report.get("tone_consistent", True),
                "brand_consistent":   quality_report.get("brand_consistent", True),
            }

        except Exception as e:
            logger.warning(f"Template gen failed for {channel}: {e}")
            results[channel] = {
                "error": str(e),
            }

    return {
        "template_id":   template_id,
        "template_name": tmpl["name"],
        "channels":      list(results.keys()),
        "results":       results,
        "summary": {
            "quality_issues": sum(
                1 for r in results.values()
                if r.get("hallucinations_flagged", False) or not r.get("brand_consistent", True)
            ),
        }
    }
