from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import RedirectResponse
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
import uuid, hashlib, base64, os, httpx

from app.models.social_account import (
    PostTweetRequest, DisconnectSocialAccountRequest,
    SocialAccountResponse, PostTweetResponse,
)
from app.services.twitter_service import twitter_service
from app.services.instagram_service import instagram_service
from app.services.linkedin_service import linkedin_service
from app.services.facebook_service import facebook_service
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger
from app.utils.encryption import encrypt, decrypt
from app.utils.audit import audit_log
from app.config import settings

# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _make_pkce() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) for PKCE."""
    verifier  = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge

META_GRAPH  = "https://graph.facebook.com/v18.0"
LI_OAUTH    = "https://www.linkedin.com/oauth/v2"

_CALLBACK_BASE = settings.API_BASE_URL

router = APIRouter(prefix="/social", tags=["Social Media"])

# ==================== SCHEMAS ====================

class ConnectInstagramRequest(BaseModel):
    access_token: str = Field(..., description="Instagram/Facebook access token")
    instagram_account_id: str = Field(..., description="Instagram Business Account ID")
    username: Optional[str] = None

class PostInstagramRequest(BaseModel):
    content_id: Optional[str] = None
    image_url: str = Field(..., description="Public URL of the image")
    caption: str = Field(..., description="Image caption")

class ConnectLinkedInRequest(BaseModel):
    access_token: str = Field(..., description="LinkedIn access token")

class PostLinkedInRequest(BaseModel):
    content_id: Optional[str] = None
    text: str = Field(..., description="Post text")
    article_url: Optional[str] = None

class ConnectFacebookRequest(BaseModel):
    page_access_token: str = Field(..., description="Facebook Page access token")
    page_id: str = Field(..., description="Facebook Page ID")

class PostFacebookRequest(BaseModel):
    content_id: Optional[str] = None
    message: str = Field(..., description="Post message")
    link: Optional[str] = None


# ==================== OAUTH CALLBACK ENDPOINTS ====================
# These endpoints handle OAuth redirects from social media platforms.
# Register these URLs in your platform's OAuth settings:
#   Twitter:    https://api.contentstudio.ai/api/v1/social/auth/twitter/callback
#   Instagram:  https://api.contentstudio.ai/api/v1/social/auth/instagram/callback
#   LinkedIn:   https://api.contentstudio.ai/api/v1/social/auth/linkedin/callback
#   Facebook:   https://api.contentstudio.ai/api/v1/social/auth/facebook/callback
# ====================================================================

# ══════════════════════════════════════════════════════════════════════════════
# OAUTH CALLBACKS
# Registered redirect URIs in each developer portal:
#   X (Twitter) : https://api.contentstudio.thq.digital/api/v1/social/auth/twitter/callback
#   Instagram   : https://api.contentstudio.thq.digital/api/v1/social/auth/instagram/callback
#   LinkedIn    : https://api.contentstudio.thq.digital/api/v1/social/auth/linkedin/callback
#   Facebook    : https://api.contentstudio.thq.digital/api/v1/social/auth/facebook/callback
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/auth/twitter/callback", summary="X (Twitter) OAuth 2.0 PKCE callback")
async def twitter_callback(
    code:  str           = Query(..., description="Authorization code from X"),
    state: Optional[str] = Query(None, description="CSRF state (encodes user_id + code_verifier)"),
    error: Optional[str] = Query(None),
):
    """
    X OAuth 2.0 PKCE callback.

    X redirects here after the user approves your app.
    The `state` parameter encodes `{user_id}:{code_verifier}` (set by the auth-url endpoint).
    This endpoint redirects to the frontend with the code so the frontend can call
    POST /social/twitter/connect with code + code_verifier to complete the exchange.

    X Developer Portal → App Settings → User authentication settings → Callback URI:
      https://api.contentstudio.thq.digital/api/v1/social/auth/twitter/callback
    """
    if error:
        return RedirectResponse(url=f"{getattr(settings, 'FRONTEND_URL', '/')}/social/connect?error={error}&platform=twitter")

    logger.info(f"X OAuth callback: code={code[:8]}… state={state}")
    # Redirect to frontend with code + state so it can call /social/twitter/connect
    frontend = getattr(settings, "FRONTEND_URL", "")
    return RedirectResponse(
        url=f"{frontend}/social/connect?platform=twitter&code={code}&state={state or ''}"
    )


@router.get("/auth/instagram/callback", summary="Instagram (Meta) OAuth 2.0 callback")
async def instagram_callback(
    code:  str           = Query(..., description="Authorization code from Meta"),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db:    AppwriteClient = Depends(get_db),
):
    """
    Instagram Graph API OAuth 2.0 callback.

    Meta redirects here. We immediately exchange the code for a short-lived token,
    then upgrade to a long-lived token (60-day), then fetch the Instagram Business
    Account linked to the Page and save everything to `social_accounts`.

    Meta App Dashboard → Facebook Login → Valid OAuth Redirect URIs:
      https://api.contentstudio.thq.digital/api/v1/social/auth/instagram/callback

    Required permissions: instagram_basic, instagram_content_publish, pages_show_list
    """
    if error:
        return RedirectResponse(url=f"{getattr(settings, 'FRONTEND_URL', '/')}/social/connect?error={error}&platform=instagram")

    app_id     = getattr(settings, "META_APP_ID",     None)
    app_secret = getattr(settings, "META_APP_SECRET", None)
    callback   = f"{_CALLBACK_BASE}/social/auth/instagram/callback"
    user_id    = state  # we pass user_id as state

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Short-lived token
            r = await client.get(
                f"{META_GRAPH}/oauth/access_token",
                params={"client_id": app_id, "redirect_uri": callback,
                        "client_secret": app_secret, "code": code},
            )
            r.raise_for_status()
            short = r.json()
            short_token = short["access_token"]

            # Step 2: Long-lived token (60 days)
            r2 = await client.get(
                f"{META_GRAPH}/oauth/access_token",
                params={"grant_type": "fb_exchange_token", "client_id": app_id,
                        "client_secret": app_secret, "fb_exchange_token": short_token},
            )
            r2.raise_for_status()
            long_token = r2.json()["access_token"]

            # Step 3: Get user's Pages and their linked IG Business Account
            r3 = await client.get(
                f"{META_GRAPH}/me/accounts",
                params={"access_token": long_token,
                        "fields": "id,name,instagram_business_account,access_token"},
            )
            r3.raise_for_status()
            pages = r3.json().get("data", [])

        for page in pages:
            ig = page.get("instagram_business_account", {})
            if not ig:
                continue
            ig_account_id  = ig.get("id", "")
            page_token     = page.get("access_token", long_token)
            account_name   = page.get("name", "")

            if user_id:
                existing = db.table("social_accounts").select("id").eq("user_id", user_id).eq("platform", "instagram").execute()
                # Long-lived IG tokens last 60 days — record expiry so a refresh
                # job can renew them before they lapse (see refresh_expiring_tokens).
                expires_at = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
                data = {
                    "user_id":          user_id,
                    "platform":         "instagram",
                    "account_id":       ig_account_id,
                    "account_name":     account_name,
                    "access_token":     encrypt(page_token),
                    "refresh_token":    encrypt(long_token),
                    "token_expires_at": expires_at,
                    "is_active":        True,
                }
                if existing.data:
                    db.table("social_accounts").update(data).eq("id", existing.data[0]["id"]).execute()
                else:
                    db.table("social_accounts").insert(data).execute()
                logger.info(f"Instagram connected: {ig_account_id} for user {user_id}")
            break  # connect first IG account found

        frontend = getattr(settings, "FRONTEND_URL", "")
        return RedirectResponse(url=f"{frontend}/social/connect?platform=instagram&status=connected")

    except Exception as exc:
        logger.error(f"Instagram callback error: {exc}")
        frontend = getattr(settings, "FRONTEND_URL", "")
        return RedirectResponse(url=f"{frontend}/social/connect?platform=instagram&error={str(exc)[:100]}")


@router.get("/auth/linkedin/callback", summary="LinkedIn OAuth 2.0 callback")
async def linkedin_callback(
    code:  str           = Query(..., description="Authorization code from LinkedIn"),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db:    AppwriteClient = Depends(get_db),
):
    """
    LinkedIn OAuth 2.0 callback.

    LinkedIn redirects here. We exchange the code for an access token (60-day)
    and refresh token (365-day), fetch the user's profile, and save to `social_accounts`.

    LinkedIn Developer Portal → Auth → Authorized redirect URLs:
      https://api.contentstudio.thq.digital/api/v1/social/auth/linkedin/callback

    Required scopes: openid, profile, email, w_member_social
    """
    if error:
        return RedirectResponse(url=f"{getattr(settings, 'FRONTEND_URL', '/')}/social/connect?error={error}&platform=linkedin")

    client_id     = getattr(settings, "LINKEDIN_CLIENT_ID",     None)
    client_secret = getattr(settings, "LINKEDIN_CLIENT_SECRET", None)
    callback      = f"{_CALLBACK_BASE}/social/auth/linkedin/callback"
    user_id       = state  # we pass user_id as state

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Exchange code for token
            r = await client.post(
                f"{LI_OAUTH}/accessToken",
                data={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  callback,
                    "client_id":     client_id,
                    "client_secret": client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            r.raise_for_status()
            token_data    = r.json()
            access_token  = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")

            # Get profile via OpenID Connect userinfo
            r2 = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r2.raise_for_status()
            profile = r2.json()

        person_id   = profile.get("sub", "")
        person_urn  = f"urn:li:person:{person_id}"
        name        = profile.get("name", "")

        if user_id:
            existing = db.table("social_accounts").select("id").eq("user_id", user_id).eq("platform", "linkedin").execute()
            data = {
                "user_id":      user_id,
                "platform":     "linkedin",
                "account_id":   person_urn,
                "account_name": name,
                "access_token": encrypt(access_token),
                "refresh_token": encrypt(refresh_token),
                "is_active":    True,
            }
            if existing.data:
                db.table("social_accounts").update(data).eq("id", existing.data[0]["id"]).execute()
            else:
                db.table("social_accounts").insert(data).execute()
            logger.info(f"LinkedIn connected: {person_urn} for user {user_id}")

        frontend = getattr(settings, "FRONTEND_URL", "")
        return RedirectResponse(url=f"{frontend}/social/connect?platform=linkedin&status=connected")

    except Exception as exc:
        logger.error(f"LinkedIn callback error: {exc}")
        frontend = getattr(settings, "FRONTEND_URL", "")
        return RedirectResponse(url=f"{frontend}/social/connect?platform=linkedin&error={str(exc)[:100]}")


@router.get("/auth/facebook/callback", summary="Facebook OAuth 2.0 callback")
async def facebook_callback(
    code:  str           = Query(..., description="Authorization code from Facebook"),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db:    AppwriteClient = Depends(get_db),
):
    """
    Facebook Graph API OAuth 2.0 callback.

    Exchanges code for a long-lived Page Access Token and saves to `social_accounts`.

    Meta App Dashboard → Facebook Login → Valid OAuth Redirect URIs:
      https://api.contentstudio.thq.digital/api/v1/social/auth/facebook/callback

    Required permissions: pages_manage_posts, pages_read_engagement, public_profile
    """
    if error:
        return RedirectResponse(url=f"{getattr(settings, 'FRONTEND_URL', '/')}/social/connect?error={error}&platform=facebook")

    app_id     = getattr(settings, "META_APP_ID",     None)
    app_secret = getattr(settings, "META_APP_SECRET", None)
    callback   = f"{_CALLBACK_BASE}/social/auth/facebook/callback"
    user_id    = state

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Exchange code for short-lived user token
            r = await client.get(
                f"{META_GRAPH}/oauth/access_token",
                params={"client_id": app_id, "redirect_uri": callback,
                        "client_secret": app_secret, "code": code},
            )
            r.raise_for_status()
            short_token = r.json()["access_token"]

            # Upgrade to long-lived token
            r2 = await client.get(
                f"{META_GRAPH}/oauth/access_token",
                params={"grant_type": "fb_exchange_token", "client_id": app_id,
                        "client_secret": app_secret, "fb_exchange_token": short_token},
            )
            r2.raise_for_status()
            long_token = r2.json()["access_token"]

            # Get Pages the user manages + their permanent Page access tokens
            r3 = await client.get(
                f"{META_GRAPH}/me/accounts",
                params={"access_token": long_token, "fields": "id,name,access_token,category"},
            )
            r3.raise_for_status()
            pages = r3.json().get("data", [])

        for page in pages:
            page_id    = page["id"]
            page_token = page["access_token"]
            page_name  = page.get("name", "")

            if user_id:
                existing = db.table("social_accounts").select("id").eq("user_id", user_id).eq("platform", "facebook").execute()
                data = {
                    "user_id":      user_id,
                    "platform":     "facebook",
                    "account_id":   page_id,
                    "account_name": page_name,
                    "access_token": encrypt(page_token),
                    "refresh_token": encrypt(long_token),
                    "is_active":    True,
                }
                if existing.data:
                    db.table("social_accounts").update(data).eq("id", existing.data[0]["id"]).execute()
                else:
                    db.table("social_accounts").insert(data).execute()
                logger.info(f"Facebook Page connected: {page_id} for user {user_id}")
            break  # connect first page found

        frontend = getattr(settings, "FRONTEND_URL", "")
        return RedirectResponse(url=f"{frontend}/social/connect?platform=facebook&status=connected")

    except Exception as exc:
        logger.error(f"Facebook callback error: {exc}")
        frontend = getattr(settings, "FRONTEND_URL", "")
        return RedirectResponse(url=f"{frontend}/social/connect?platform=facebook&error={str(exc)[:100]}")


# ==================== TWITTER ENDPOINTS ====================

@router.get("/twitter/auth-url", summary="Get X (Twitter) OAuth 2.0 PKCE authorization URL",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_twitter_auth_url():
    """
    Returns the X authorization URL + PKCE params.

    Flow:
    1. Frontend calls this → gets { auth_url, code_verifier, state }
    2. Frontend stores `code_verifier` (localStorage or sessionStorage)
    3. Frontend redirects user to `auth_url`
    4. X redirects to callback → frontend gets ?code=...&state=...
    5. Frontend calls POST /social/twitter/connect { code, code_verifier }
    """
    user_id = "demo-user"
    try:
        code_verifier, code_challenge = _make_pkce()
        state    = f"{user_id}:{uuid.uuid4().hex}"
        callback = f"{_CALLBACK_BASE}/social/auth/twitter/callback"
        auth_url = twitter_service.get_auth_url(callback, state, code_challenge)
        return {
            "auth_url":      auth_url,
            "code_verifier": code_verifier,
            "state":         state,
            "note":          "Store code_verifier securely — you will need it in the next step",
        }
    except Exception as e:
        logger.error(f"Twitter auth URL error: {e}")
        raise HTTPException(status_code=500, detail="Could not start Twitter authorization. Please try again.")


@router.post("/twitter/connect", response_model=SocialAccountResponse, summary="Connect X (Twitter) account — complete OAuth 2.0 PKCE",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def connect_twitter(
    code:          str = Query(..., description="Authorization code from X callback"),
    code_verifier: str = Query(..., description="PKCE code_verifier stored by the frontend"),
    db: AppwriteClient = Depends(get_db),
):
    """
    Exchange an X authorization code for access + refresh tokens and save the account.

    X API v2 — OAuth 2.0 PKCE
    Scopes requested: tweet.read tweet.write users.read offline.access
    """
    user_id = "demo-user"
    try:
        callback    = f"{_CALLBACK_BASE}/social/auth/twitter/callback"
        token_data  = await twitter_service.exchange_code(code, code_verifier, callback)
        access_token  = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")

        user_info = await twitter_service.get_user_info(access_token)

        account_data = {
            "user_id":      user_id,
            "platform":     "twitter",
            "account_id":   user_info.get("user_id", ""),
            "account_name": user_info.get("username", ""),
            "access_token": encrypt(access_token),
            "refresh_token": encrypt(refresh_token),
            "is_active":    True,
        }

        existing = db.table("social_accounts").select("id").eq("user_id", user_id).eq("platform", "twitter").execute()
        if existing.data:
            result = db.table("social_accounts").update(account_data).eq("id", existing.data[0]["id"]).execute()
        else:
            result = db.table("social_accounts").insert(account_data).execute()

        return SocialAccountResponse(**result.data[0])
    except Exception as e:
        logger.error(f"Twitter connect error: {e}")
        raise HTTPException(status_code=500, detail="Could not connect the Twitter account. Please try again.")

@router.post("/twitter/tweet", response_model=PostTweetResponse, summary="Post tweet immediately",
    responses={
                400: {"description": "Bad request"},
                500: {"description": "Internal server error"}
    }
)
async def post_tweet(request: PostTweetRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        twitter_account = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "twitter").eq("is_active", True).execute()
        if not twitter_account.data:
            raise HTTPException(status_code=400, detail="No Twitter account connected.")
        
        account = twitter_account.data[0]
        if request.content_id:
            content_result = db.table("content").select("content").eq("id", request.content_id).eq("user_id", user_id).execute()
            tweet_text = content_result.data[0]["content"]
        else:
            tweet_text = request.tweet_text

        tweet_data = await twitter_service.post_tweet(
            access_token=decrypt(account["access_token"]),
            tweet_text=tweet_text,
        )
        if request.content_id:
            db.table("content").update({"status": "published"}).eq("id", request.content_id).execute()
        
        return PostTweetResponse(success=True, tweet_id=tweet_data['tweet_id'], tweet_url=tweet_data['tweet_url'], message="Tweet posted!")
    except Exception as e:
        logger.error(f"❌ Post tweet error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not post the tweet. Please try again.")

# ==================== INSTAGRAM ENDPOINTS ====================

@router.post("/instagram/connect", response_model=SocialAccountResponse,
    responses={
                500: {"description": "Internal server error"}
    }
)
async def connect_instagram(request: ConnectInstagramRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        account_info = await instagram_service.verify_credentials(request.access_token, request.instagram_account_id)
        existing = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "instagram").execute()
        
        account_data = {
            "user_id":              user_id,
            "platform":             "instagram",
            "instagram_account_id": request.instagram_account_id,
            "account_id":           request.instagram_account_id,
            "account_name":         account_info.get("username") or request.username or "",
            "access_token":         encrypt(request.access_token),
            "is_active":            True,
        }
        
        result = db.table("social_accounts").update(account_data).eq("id", existing.data[0]["id"]).execute() if existing.data else db.table("social_accounts").insert(account_data).execute()
        return SocialAccountResponse(**result.data[0])
    except Exception as e:
        logger.error(f"❌ Instagram connect error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not connect the Instagram account. Please try again.")

@router.post("/instagram/post",
    responses={
                400: {"description": "Bad request"},
                500: {"description": "Internal server error"}
    }
)
async def post_instagram(request: PostInstagramRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        acc = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "instagram").eq("is_active", True).execute()
        if not acc.data: raise HTTPException(status_code=400, detail="No Instagram account")
        
        result = await instagram_service.post_image(decrypt(acc.data[0]["access_token"]), acc.data[0]["instagram_account_id"], request.image_url, request.caption)
        if request.content_id:
            db.table("content").update({"status": "published"}).eq("id", request.content_id).execute()
        return {"success": True, "post_id": result["post_id"], "permalink": result["permalink"]}
    except Exception as e:
        logger.error(f"❌ Instagram post error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not publish to Instagram. Please try again.")

# ==================== LINKEDIN ENDPOINTS ====================

@router.post("/linkedin/connect", response_model=SocialAccountResponse,
    responses={
                500: {"description": "Internal server error"}
    }
)
async def connect_linkedin(request: ConnectLinkedInRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        user_info = await linkedin_service.verify_credentials(request.access_token)
        existing = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "linkedin").execute()
        
        account_data = {
            "user_id":      user_id,
            "platform":     "linkedin",
            "account_id":   user_info.get("user_id", ""),
            "account_name": user_info.get("name", ""),
            "access_token": encrypt(request.access_token),
            "is_active":    True,
        }
        
        result = db.table("social_accounts").update(account_data).eq("id", existing.data[0]["id"]).execute() if existing.data else db.table("social_accounts").insert(account_data).execute()
        return SocialAccountResponse(**result.data[0])
    except Exception as e:
        logger.error(f"❌ LinkedIn connect error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not connect the LinkedIn account. Please try again.")

@router.post("/linkedin/post",
    responses={
                400: {"description": "Bad request"},
                500: {"description": "Internal server error"}
    }
)
async def post_linkedin(request: PostLinkedInRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        acc = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "linkedin").eq("is_active", True).execute()
        if not acc.data: raise HTTPException(status_code=400, detail="No LinkedIn account")
        
        # account_id stores the person URN (urn:li:person:{id}) set during connect
        result = await linkedin_service.post_text(decrypt(acc.data[0]["access_token"]), acc.data[0].get("account_id", ""), request.text)
        if request.content_id:
            db.table("content").update({"status": "published"}).eq("id", request.content_id).execute()
        return {"success": True, "post_id": result["post_id"], "post_url": result["post_url"]}
    except Exception as e:
        logger.error(f"❌ LinkedIn post error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not publish to LinkedIn. Please try again.")

# ==================== FACEBOOK ENDPOINTS ====================

@router.post("/facebook/connect", response_model=SocialAccountResponse,
    responses={
                500: {"description": "Internal server error"}
    }
)
async def connect_facebook(request: ConnectFacebookRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        page_info = await facebook_service.get_page_info(request.page_access_token, request.page_id)
        existing = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "facebook").execute()
        
        account_data = {
            "user_id":      user_id,
            "platform":     "facebook",
            "page_id":      request.page_id,
            "account_id":   request.page_id,
            "account_name": page_info.get("name") or page_info.get("username") or "",
            "access_token": encrypt(request.page_access_token),
            "is_active":    True,
        }
        
        result = db.table("social_accounts").update(account_data).eq("id", existing.data[0]["id"]).execute() if existing.data else db.table("social_accounts").insert(account_data).execute()
        return SocialAccountResponse(**result.data[0])
    except Exception as e:
        logger.error(f"❌ Facebook connect error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not connect the Facebook account. Please try again.")

@router.post("/facebook/post",
    responses={
                400: {"description": "Bad request"},
                500: {"description": "Internal server error"}
    }
)
async def post_facebook(request: PostFacebookRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        acc = db.table("social_accounts").select("*").eq("user_id", user_id).eq("platform", "facebook").eq("is_active", True).execute()
        if not acc.data: raise HTTPException(status_code=400, detail="No Facebook Page connected")
        
        result = await facebook_service.post_to_page(decrypt(acc.data[0]["access_token"]), acc.data[0]["page_id"], request.message, request.link)
        if request.content_id:
            db.table("content").update({"status": "published"}).eq("id", request.content_id).execute()
        return {"success": True, "post_id": result["post_id"], "post_url": result["post_url"]}
    except Exception as e:
        logger.error(f"❌ Facebook post error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not publish to Facebook. Please try again.")

# ==================== GENERAL ENDPOINTS ====================

@router.get("/accounts", response_model=List[SocialAccountResponse],
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_connected_accounts(db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        result = db.table("social_accounts").select("*").eq("user_id", user_id).execute()
        return [SocialAccountResponse(**acc) for acc in result.data] if result.data else []
    except Exception as e:
        logger.error(f"❌ Get accounts error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not load connected accounts. Please try again.")

@router.delete("/disconnect",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def disconnect_account(request: DisconnectSocialAccountRequest, db: AppwriteClient = Depends(get_db)):
    user_id = "demo-user"
    try:
        account = db.table("social_accounts").select("id").eq("user_id", user_id).eq("platform", request.platform.value).execute()
        if not account.data:
            raise HTTPException(status_code=404, detail="Account not found")
        db.table("social_accounts").delete().eq("id", account.data[0]["id"]).execute()
        return {"message": f"{request.platform.value.title()} disconnected successfully"}
    except Exception as e:
        logger.error(f"❌ Disconnect error: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not disconnect the account. Please try again.")


# ==================== POSTIZ-STYLE MULTI-PLATFORM POST ====================
# Inspired by Postiz (https://github.com/gitroomhq/postiz-app):
# Write one post, publish to every connected platform in a single request.

class MultiPlatformPostRequest(BaseModel):
    content_id: Optional[str] = Field(None, description="Existing content ID from your library")
    text: str = Field(..., description="Post text (used for Twitter, LinkedIn, Facebook)")
    image_url: Optional[str] = Field(None, description="Optional image URL (used for Instagram)")
    platforms: Optional[List[str]] = Field(None, description="Platforms to post to. Omit to post to ALL connected accounts.")


@router.post("/publish", summary="Publish to all connected platforms at once (Postiz-style)",
    responses={
                400: {"description": "Bad request"}
    }
)
async def publish_to_all(
    request: MultiPlatformPostRequest,
    http_request: Request,
    db: AppwriteClient = Depends(get_db),
):
    """
    One post → every connected platform.

    Inspired by Postiz open-source (gitroomhq/postiz-app).
    Pass `platforms` to target specific networks, or omit to hit all connected accounts.

    Results include per-platform success/failure so partial failures are visible.
    """
    user_id = "demo-user"
    # Resolve content text from library if content_id provided
    text = request.text
    if request.content_id:
        cnt = db.table("content").select("content").eq("id", request.content_id).eq("user_id", user_id).execute()
        if cnt.data:
            text = cnt.data[0].get("content", text)

    # Load all active connected accounts
    accounts_res = db.table("social_accounts").select("*").eq("user_id", user_id).eq("is_active", True).execute()
    accounts = accounts_res.data or []

    if request.platforms:
        target = set(p.lower() for p in request.platforms)
        accounts = [a for a in accounts if a.get("platform") in target]

    if not accounts:
        raise HTTPException(status_code=400, detail="No connected social accounts found. Connect at least one platform first.")

    results = []

    for acc in accounts:
        platform = acc.get("platform")
        try:
            if platform == "twitter":
                data = await twitter_service.post_tweet(
                    access_token=decrypt(acc["access_token"]),
                    tweet_text=text,
                )
                results.append({"platform": "twitter", "success": True, "post_id": data.get("tweet_id"), "url": data.get("tweet_url")})

            elif platform == "linkedin":
                data = await linkedin_service.post_text(decrypt(acc["access_token"]), acc.get("account_id", ""), text)
                results.append({"platform": "linkedin", "success": True, "post_id": data.get("post_id"), "url": data.get("post_url")})

            elif platform == "facebook":
                data = await facebook_service.post_to_page(decrypt(acc["access_token"]), acc.get("account_id", ""), text, None)
                results.append({"platform": "facebook", "success": True, "post_id": data.get("post_id"), "url": data.get("post_url")})

            elif platform == "instagram":
                if not request.image_url:
                    results.append({"platform": "instagram", "success": False, "error": "Instagram requires image_url"})
                    continue
                data = await instagram_service.post_image(decrypt(acc["access_token"]), acc.get("account_id", ""), request.image_url, text)
                results.append({"platform": "instagram", "success": True, "post_id": data.get("post_id"), "url": data.get("permalink")})

            else:
                results.append({"platform": platform, "success": False, "error": "Platform posting not supported yet"})
                continue

            # Mark content as published
            if request.content_id:
                db.table("content").update({"status": "published"}).eq("id", request.content_id).execute()

        except Exception as e:
            logger.error(f"Multi-publish error on {platform}: {e}")
            results.append({"platform": platform, "success": False, "error": str(e)})

    published = [r for r in results if r.get("success")]
    failed    = [r for r in results if not r.get("success")]

    logger.info(f"Multi-publish: {len(published)} success, {len(failed)} failed for user {user_id}")

    # Immutable audit trail — publishing to live external channels is irreversible.
    await audit_log(
        db, user_id, "social.publish",
        resource_id=request.content_id,
        details={
            "platforms":   [r.get("platform") for r in results],
            "published":   [r.get("platform") for r in published],
            "failed":      [r.get("platform") for r in failed],
            "post_ids":    [r.get("post_id") for r in published if r.get("post_id")],
        },
        request=http_request,
    )

    return {
        "published_count": len(published),
        "failed_count":    len(failed),
        "results":         results,
    }