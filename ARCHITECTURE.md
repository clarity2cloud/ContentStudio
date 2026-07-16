# Architecture Guide

This document describes the high-level system design, component architecture, data flows, and key design decisions for ContentStudio AI Backend.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Core Components](#core-components)
4. [Technology Stack](#technology-stack)
5. [Data Flow](#data-flow)
6. [Key Design Decisions](#key-design-decisions)
7. [Scaling Considerations](#scaling-considerations)

---

## System Overview

ContentStudio AI Backend is a **production-grade FastAPI service** that orchestrates:

- **AI Content Generation** - Multi-format content creation using NVIDIA NIM
- **Brand Intelligence** - Persistent brand profiles that influence all AI outputs
- **Campaign Management** - Hierarchical organization of content under campaigns
- **Social Publishing** - Direct integration with Twitter, LinkedIn, Instagram, Facebook
- **Content Scoring** - AI-powered quality and brand alignment analysis
- **User Management** - Full authentication and authorization
- **Analytics** - Content performance tracking and insights

The system is designed for **horizontal scalability**, **high availability**, and **enterprise-grade reliability**.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT APPLICATIONS                          │
│                    (Web, Mobile, Integrations)                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                    HTTPS / API Gateway
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                       FASTAPI BACKEND                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │              API LAYER (v1 Routes)                         │   │
│  ├────────────────────────────────────────────────────────────┤   │
│  │ /auth           → Authentication & Authorization          │   │
│  │ /brands         → Brand Profile Management                │   │
│  │ /campaigns      → Campaign Orchestration                  │   │
│  │ /ai             → AI Content Generation                   │   │
│  │ /content        → Content Management & Retrieval          │   │
│  │ /media          → Media Generation & Management           │   │
│  │ /social-media   → Social Publishing                       │   │
│  │ /dashboard      → Analytics & Dashboard                   │   │
│  │ /templates      → Template Management                     │   │
│  │ /scheduling     → Content Scheduling                      │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │              SERVICE LAYER                                │   │
│  ├────────────────────────────────────────────────────────────┤   │
│  │ AIGenerationService   → LLM orchestration & prompting     │   │
│  │ BrandService          → Brand profile management         │   │
│  │ CampaignService       → Campaign orchestration           │   │
│  │ ContentService        → Content CRUD & filtering         │   │
│  │ MediaService          → Image/video/carousel gen         │   │
│  │ SocialMediaService    → Platform integrations            │   │
│  │ AnalyticsService      → Metrics & insights              │   │
│  │ AuthService           → JWT & user auth                 │   │
│  │ NotificationService   → Email & notifications            │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │              CORE INFRASTRUCTURE                           │   │
│  ├────────────────────────────────────────────────────────────┤   │
│  │ Security        → JWT, RBAC, Request signing             │   │
│  │ Database Layer  → Appwrite ORM, Query builders           │   │
│  │ Error Handling  → Custom exceptions, HTTP mapping        │   │
│  │ Logging         → Structured logging, Audit trails       │   │
│  │ Middleware      → CORS, Rate limiting, Auth              │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┬─────────────────┐
        │                  │                  │                 │
        ▼                  ▼                  ▼                 ▼
   ┌─────────┐      ┌──────────────┐   ┌──────────┐      ┌────────────┐
   │APPWRITE │      │NVIDIA NIM    │   │ REDIS    │      │  SOCIAL    │
   │DATABASE │      │  LLM + Image │   │  QUEUE   │      │   APIs     │
   │         │      │  Generation  │   │          │      │            │
   └─────────┘      └──────────────┘   └──────────┘      └────────────┘
        │                  │                  │                 │
   Collections         LLM Models          Celery           Tweepy
   Indexes            FLUX.2 Image        Workers          LinkedIn SDK
   Documents          CogVideoX Video                      Instagram API
```

---

## Core Components

### 1. API Layer (`app/api/v1/`)

**Responsibility:** HTTP request routing and validation

**Key Files:**
- `auth.py` - Authentication endpoints
- `brand.py` - Brand profile management
- `campaigns.py` - Campaign CRUD and orchestration
- `ai_generation.py` - AI content generation endpoints
- `content.py` - Content management
- `media_generation.py` - Image/video/carousel generation
- `social_media.py` - Social platform publishing
- `dashboard.py` - Analytics and dashboard
- `templates.py` - Template management
- `scheduling.py` - Content scheduling

**Pattern:** Each endpoint file:
- Validates request schemas (Pydantic models)
- Checks authentication/authorization
- Delegates to service layer
- Handles service exceptions
- Returns standardized JSON responses

### 2. Service Layer (`app/services/`)

**Responsibility:** Business logic orchestration

**Core Services:**
- `ai_generation_service.py` - LLM prompt engineering, token management
- `brand_service.py` - Brand profile CRUD and validation
- `campaign_service.py` - Campaign lifecycle management
- `content_service.py` - Content persistence and retrieval
- `media_service.py` - Image/video/carousel generation
- `social_media_service.py` - Platform API integrations
- `analytics_service.py` - Analytics queries and aggregations
- `auth_service.py` - JWT token creation, OTP verification
- `notification_service.py` - Email and in-app notifications

**Pattern:** Services are **stateless** and **composable**:
```python
class AIGenerationService:
    def generate_blog(self, campaign_id, brand_id, topic):
        # 1. Fetch brand profile
        brand = self.brand_service.get_brand(brand_id)
        
        # 2. Build prompt with brand context
        prompt = self._build_blog_prompt(topic, brand)
        
        # 3. Call LLM
        content = self._call_nim_api(prompt)
        
        # 4. Score content
        score = self._score_content(content, brand)
        
        # 5. Persist to database
        doc = self.content_service.create_content(
            campaign_id=campaign_id,
            content=content,
            score=score
        )
        
        return doc
```

### 3. Database Layer (`app/db/` and `app/core/database.py`)

**Responsibility:** Data persistence and query abstraction

**Database:** Appwrite (NoSQL)

**Collections:**
- `users` - User accounts and profiles
- `brands` - Brand profiles with tone/voice settings
- `campaigns` - Campaign metadata and status
- `content` - Generated content pieces
- `content_history` - Audit trail of all changes
- `media` - Generated images, videos, carousels
- `social_posts` - Published social media posts
- `analytics` - Aggregated metrics and events

**Key Features:**
- Automatic timestamps (created_at, updated_at)
- Soft deletes (deleted_at field)
- Audit logging on all mutations
- Index optimization for common queries
- Pagination support

### 4. External Integrations

#### NVIDIA NIM (AI Models)

**LLM:** llama-3.3-nemotron-super-49b-v1
- Blog generation
- Caption generation
- Email writing
- Repurposing and remix

**Image Generation:** FLUX.2-klein-4B
- AI image creation from text prompts

**Video Generation:** cosmos-1-0-diffusion-7b-text2world
- AI video from text descriptions

**Integration Pattern:**
```python
def call_nim_api(self, model, prompt, temperature=0.7):
    """Call NVIDIA NIM API with error handling and rate limiting"""
    headers = {"Authorization": f"Bearer {self.api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 2000
    }
    response = requests.post(
        f"{self.base_url}/chat/completions",
        json=payload,
        headers=headers
    )
    return response.json()
```

#### Social Media APIs

**Twitter/X (Tweepy):**
- Tweet composition and posting
- Rate limiting (300 requests/15 min)

**LinkedIn SDK:**
- LinkedIn post creation
- Company page posts

**Instagram/Facebook Graph API:**
- Caption and image posts
- Story content

**Integration Pattern:**
- Store OAuth tokens in user profile
- Refresh tokens on expiration
- Handle platform-specific rate limits
- Log all publishing actions for audit

#### Resend (Email Service)

- OTP delivery
- Password reset emails
- Notification emails
- Newsletter distribution

---

## Data Flow

### 1. Content Generation Flow

```
User Request (POST /ai/generate/blog)
         │
         ▼
    Validate Input
    (topic, brand_id, campaign_id)
         │
         ▼
    Get Brand Profile
    (fetch tone, voice, vocabulary)
         │
         ▼
    Build AI Prompt
    (incorporate brand context)
         │
         ▼
    Call NVIDIA NIM LLM
    (stream response if needed)
         │
         ▼
    Score Content
    (clarity, engagement, SEO)
         │
         ▼
    Persist to Database
    (content + metadata)
         │
         ▼
    Emit Event
    (for real-time dashboards)
         │
         ▼
    Return to Client
```

### 2. Social Publishing Flow

```
User clicks "Publish" (POST /social-media/publish)
         │
         ▼
    Get Content + Platform Settings
         │
         ▼
    Get User's OAuth Token
    (for platform)
         │
         ▼
    Validate Schedule Time
    (if scheduled)
         │
         ▼
    Schedule Job or Publish Immediately
    (Celery task or direct API call)
         │
         ▼
    Call Platform API
    (Twitter/LinkedIn/Instagram)
         │
         ▼
    Store Publication Record
    (for analytics)
         │
         ▼
    Return Status to Client
```

### 3. Campaign Analytics Flow

```
GET /campaigns/{id}/analytics
         │
         ▼
    Get Campaign Details
         │
         ▼
    Query Content Count
    (by type, status)
         │
         ▼
    Query Published Posts
    (engagement metrics from social APIs)
         │
         ▼
    Aggregate Metrics
    (likes, shares, clicks)
         │
         ▼
    Calculate KPIs
    (engagement rate, reach)
         │
         ▼
    Cache Results
    (Redis for 1 hour)
         │
         ▼
    Return to Client
```

---

## Key Design Decisions

### 1. Stateless Services

**Decision:** All services are **stateless** and store no in-memory state.

**Rationale:**
- Enables horizontal scaling (add/remove servers freely)
- Simplifies debugging and testing
- Reduces memory footprint per instance

**Implementation:**
- No instance variables except logger
- All data fetched from database on each request
- Configuration loaded from environment

### 2. Appwrite for Database

**Decision:** Use Appwrite (NoSQL) instead of traditional SQL database.

**Rationale:**
- Flexible schema (content types vary significantly)
- Built-in authentication collection
- Self-hosted or managed cloud option
- Real-time subscriptions for live dashboards
- File storage integrated

**Trade-offs:**
- No complex JOINs (use denormalization)
- Weaker consistency guarantees
- Limited query capabilities

### 3. Async Task Queue (Celery)

**Decision:** Use Celery for long-running operations.

**Rationale:**
- Video generation (30+ seconds)
- Large batch content creation
- Email sending
- Social media publishing scheduling
- Analytics aggregation

**Implementation:**
```python
# Task definition
@app.task
def generate_video_async(content_id, prompt):
    result = call_nvidia_video_api(prompt)
    save_to_database(content_id, result)
    notify_user(content_id)

# Usage
generate_video_async.delay(content_id, prompt)
```

### 4. JWT for Stateless Auth

**Decision:** JWT tokens instead of sessions.

**Rationale:**
- Stateless (no server-side session storage)
- Works across multiple servers
- Mobile and SPA friendly
- Can be used for service-to-service auth

**Implementation:**
- Access token: 24 hours
- Refresh token: 7 days (stored in database)
- Automatic refresh on expiration
- Token blacklist on logout

### 5. Rate Limiting at Middleware

**Decision:** Implement rate limiting in middleware, not per-endpoint.

**Rationale:**
- Consistent across all endpoints
- Protects against DDoS
- Fair resource allocation

**Implementation:**
```python
# Rate limiting: 100 requests per minute per user
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    user_id = request.headers.get("X-User-ID")
    key = f"rate_limit:{user_id}"
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, 60)
    if count > 100:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"}
        )
    return await call_next(request)
```

### 6. Event-Driven Architecture

**Decision:** Emit events for side effects (notifications, analytics).

**Rationale:**
- Loose coupling between services
- Easy to add new event handlers
- Asynchronous processing

**Implementation:**
```python
# When content is created
event = ContentCreatedEvent(
    content_id=doc.id,
    user_id=user_id,
    content_type="blog",
    timestamp=datetime.now()
)
event_bus.emit(event)

# Handlers subscribe to events
@event_bus.on(ContentCreatedEvent)
async def notify_user_on_content_created(event):
    await notification_service.send_email(
        user_id=event.user_id,
        subject="Your content is ready!",
        message=f"Blog post '{event.content_id}' generated"
    )
```

---

## Scaling Considerations

### Horizontal Scaling

**Current Bottlenecks:**
1. NVIDIA NIM API calls (external rate limits)
2. Database query performance
3. Social media API rate limits

**Solutions:**
1. **LLM Calls:**
   - Queue with priority levels
   - Batch similar requests
   - Implement exponential backoff

2. **Database:**
   - Add indexes on frequently queried fields
   - Use database read replicas
   - Cache common queries in Redis

3. **Social APIs:**
   - Implement per-platform rate limit management
   - Use queue with adaptive backoff
   - Schedule posts during optimal windows

### Caching Strategy

```
Application Cache Hierarchy:

L1: Redis (1-hour TTL)
├── Campaign analytics
├── Brand profiles
└── User permissions

L2: Appwrite cache
├── Content queries
└── User sessions

L3: CDN (static content)
├── Generated media URLs
└── Public API responses
```

### Database Optimization

**Indexes:**
- `users(email)` - for auth
- `campaigns(user_id, created_at)` - for listing
- `content(campaign_id, status)` - for filtering
- `social_posts(user_id, published_at)` - for analytics

**Query Optimization:**
- Use Appwrite queries with filters (server-side)
- Avoid N+1 queries with batch operations
- Implement pagination (limit 50 per page default)

---

## Security Architecture

### Authentication & Authorization

```
Request comes in
         │
         ▼
    Extract JWT from header
         │
         ▼
    Verify signature
         │
    ┌─────┴─────┐
    │           │
    ▼           ▼
 Valid      Invalid
    │           │
    │           ▼
    │      Return 401
    │
    ▼
 Check user role/permissions
    │
    ├─ Admin ────────┐
    ├─ User ─────────┤
    └─ Guest ────────┤
                     │
                     ▼
          Allow or deny resource access
```

### Data Security

- All sensitive data encrypted at rest (Appwrite)
- HTTPS for all API calls
- No PII in logs
- Regular security audits
- Dependency scanning

---

## Monitoring & Observability

### Metrics Collected

- Request latency by endpoint
- Error rates and types
- LLM API call durations
- Database query times
- Task queue depth
- Active user sessions

### Logging

- All requests logged with user ID, method, status
- Error logs include stack traces
- Audit logs for data mutations
- Structured logging (JSON format)

### Alerts

- High error rate (>5%)
- Database connection pool exhaustion
- API latency >5 seconds
- Failed authentication attempts (>10/min)
- Celery queue backlog

---

## Future Improvements

1. **GraphQL API** - More flexible querying
2. **WebSocket Support** - Real-time content updates
3. **Multi-tenancy** - Support agency accounts
4. **Advanced Caching** - Redis-based query cache
5. **ML Pipeline** - Custom model fine-tuning
6. **CDN Integration** - Global media delivery

