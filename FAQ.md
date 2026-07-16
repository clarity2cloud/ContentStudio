# Frequently Asked Questions (FAQ)

Common questions about ContentStudio AI Backend setup, configuration, and usage.

---

## Table of Contents

1. [Installation & Setup](#installation--setup)
2. [Configuration](#configuration)
3. [Usage](#usage)
4. [Troubleshooting](#troubleshooting)
5. [Contributing](#contributing)
6. [Performance & Scaling](#performance--scaling)

---

## Installation & Setup

### Q: What are the system requirements?

**A:**
- Python 3.11 or newer
- Docker (recommended for production)
- 2GB RAM minimum (4GB recommended)
- 10GB disk space (more for media storage)
- Access to NVIDIA NIM API
- Appwrite instance (self-hosted or cloud)

### Q: How do I install locally for development?

**A:**
```bash
# Clone repository
git clone https://github.com/clarity2cloud/open-source-contentstudio-agent.git
cd open-source-contentstudio-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials

# Run development server
uvicorn app.main:app --reload
```

### Q: What's the easiest way to get started?

**A:**
Use Docker Compose (includes all dependencies):

```bash
docker-compose up -d
```

This starts:
- Backend API on http://localhost:8000
- Redis cache on localhost:6379
- Appwrite database (optional)

### Q: How do I update dependencies?

**A:**
```bash
# Update all packages
pip install --upgrade -r requirements.txt

# Update specific package
pip install --upgrade fastapi

# Check for security vulnerabilities
pip install safety
safety check
```

---

## Configuration

### Q: What environment variables do I need?

**A:**
**Minimum required:**
- `APPWRITE_ENDPOINT` - Appwrite API URL
- `APPWRITE_API_KEY` - Appwrite API key
- `APPWRITE_PROJECT_ID` - Appwrite project ID
- `NVIDIA_NIM_API_KEY` - NVIDIA API key
- `JWT_SECRET_KEY` - Secret key for JWT tokens

**For social media:**
- `TWITTER_API_KEY`, `TWITTER_API_SECRET`
- `LINKEDIN_ACCESS_TOKEN`
- `INSTAGRAM_ACCESS_TOKEN`
- `FACEBOOK_PAGE_ACCESS_TOKEN`

See [DEPLOYMENT.md](./DEPLOYMENT.md) for complete list.

### Q: Where should I store sensitive credentials?

**A:**
- **Development:** `.env` file (add to `.gitignore`)
- **Production:** Environment variables or secret management (AWS Secrets Manager, HashiCorp Vault)
- **Docker:** Docker secrets or environment files
- **Kubernetes:** Kubernetes secrets

**Never commit:**
- `.env` files
- API keys or tokens
- Database passwords

### Q: How do I switch between development and production?

**A:**
```bash
# Development
ENVIRONMENT=development DEBUG=true LOG_LEVEL=DEBUG

# Production
ENVIRONMENT=production DEBUG=false LOG_LEVEL=WARNING
```

### Q: Can I use a different database instead of Appwrite?

**A:**
Currently, the backend is optimized for Appwrite. To use another database:

1. Modify `app/core/database.py` to use your database driver
2. Update collection schemas in `app/db/`
3. Update queries in service layer
4. Test thoroughly

We recommend sticking with Appwrite for simplicity.

---

## Usage

### Q: How do I create my first API request?

**A:**
```bash
# 1. Sign up
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'

# 2. Verify OTP (check email)
curl -X POST http://localhost:8000/api/v1/auth/verify-otp \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "otp": "123456"}'

# 3. Response includes JWT token
# 4. Use token in future requests
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/v1/me
```

### Q: How do I generate content?

**A:**
```bash
curl -X POST http://localhost:8000/api/v1/ai/generate/blog \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "campaign_id": "campaign_123",
    "brand_id": "brand_456",
    "topic": "How to use AI for content creation",
    "tone": "professional"
  }'
```

### Q: What's the difference between campaigns and content?

**A:**
- **Campaign:** Container for related content (e.g., "Q3 Product Launch")
  - One campaign per project or initiative
  - Holds metadata: dates, goals, target audience
  
- **Content:** Individual pieces (blog posts, tweets, emails)
  - Multiple pieces per campaign
  - Generated from templates or AI
  - Can be published to social platforms

### Q: Can I schedule content for later publishing?

**A:**
```bash
curl -X POST http://localhost:8000/api/v1/social-media/publish \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "content_123",
    "platforms": ["twitter", "linkedin"],
    "scheduled_time": "2024-07-15T14:30:00Z"
  }'
```

### Q: How do I repurpose content across platforms?

**A:**
```bash
curl -X POST http://localhost:8000/api/v1/ai/generate/repurpose \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_content_id": "blog_post_123",
    "target_platforms": ["twitter", "linkedin", "email"],
    "brand_id": "brand_456"
  }'
```

The system generates platform-optimized versions automatically.

### Q: What content templates are available?

**A:**
ContentStudio includes 14 pre-built templates:

1. **Blog Templates**
   - How-to guides
   - Case studies
   - Product announcements
   - Industry insights

2. **Social Templates**
   - Tweet threads
   - LinkedIn posts
   - Instagram captions
   - Stories

3. **Email Templates**
   - Newsletters
   - Promotional
   - Updates
   - Referral campaigns

See templates via: `GET /api/v1/templates`

### Q: How do I score content for quality?

**A:**
Content is automatically scored on:
- **Clarity** - Is it easy to understand?
- **Engagement** - Does it encourage interaction?
- **CTA Strength** - Is the call-to-action clear?
- **Brand Alignment** - Does it match brand voice?
- **SEO Readiness** - Is it optimized for search?

Scores are included in content response (0-100 for each metric).

---

## Troubleshooting

### Q: Why is my OTP not working?

**A:**
- Ensure OTP is entered within 5 minutes of request
- Check spelling carefully (case-sensitive in some cases)
- Check spam/junk email folder
- Request a new OTP if it expired

### Q: How do I reset my password?

**A:**
```bash
# Step 1: Request password reset
curl -X POST http://localhost:8000/api/v1/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'

# Step 2: Check email for OTP
# Step 3: Reset password
curl -X POST http://localhost:8000/api/v1/auth/reset-password \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "otp": "123456",
    "new_password": "NewSecurePassword123"
  }'
```

### Q: Why does content generation fail?

**A:**
Common causes:
1. **API Rate Limits** - Wait and retry
2. **Invalid Brand Profile** - Ensure brand is properly configured
3. **NVIDIA NIM Connection** - Check API key and network
4. **Token Limit** - Topic may require shorter generation
5. **Quota Exceeded** - Check credit balance

Check [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for detailed solutions.

### Q: How do I debug API errors?

**A:**
1. Enable debug logging: `DEBUG=true`
2. Check response status code and message
3. Review logs in `logs/` directory
4. Use API docs at http://localhost:8000/docs
5. Check [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for specific errors

### Q: Is the API rate-limited?

**A:**
Yes:
- **Default:** 100 requests per minute per user
- **Configurable:** Change `RATE_LIMIT_REQUESTS` in `.env`
- **Rate limit exceeded:** Retry after 60 seconds
- **Admin:** Higher limits for admin accounts

---

## Contributing

### Q: How do I contribute to the project?

**A:**
1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make changes and test
4. Commit with clear messages: `git commit -m "feat: add new feature"`
5. Push to your fork
6. Open Pull Request

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed guidelines.

### Q: What's the coding style?

**A:**
- **Language:** Python 3.11+
- **Formatter:** Black (`black app/`)
- **Linter:** Flake8 (`flake8 app/`)
- **Type Checking:** MyPy (`mypy app/`)
- **Naming:** snake_case for functions, variables; PascalCase for classes

Run before committing:
```bash
black app/
flake8 app/
mypy app/
pytest tests/ -v
```

### Q: How do I report a bug?

**A:**
1. Check existing issues to avoid duplicates
2. Provide clear description and steps to reproduce
3. Include error logs and environment details
4. Open issue with bug report template

### Q: Are pull requests welcome?

**A:**
Yes! We welcome:
- Bug fixes
- New features (discuss in issue first for major changes)
- Documentation improvements
- Performance optimizations
- Test coverage improvements

---

## Performance & Scaling

### Q: How many concurrent users can the backend handle?

**A:**
Depends on:
- Instance size (CPU, RAM)
- Database performance
- NVIDIA NIM rate limits
- Social media API quotas

**Typical:**
- Single instance: 100-500 concurrent users
- With load balancing: 1000+ concurrent users
- With horizontal scaling: Unlimited

### Q: Should I use Redis?

**A:**
**Recommended:** Yes, for production

Benefits:
- Session caching
- Rate limiting tracking
- Task queue (Celery)
- Query result caching

Without Redis:
- Slower rate limiting
- No async tasks
- Higher database load

### Q: How do I scale to handle more traffic?

**A:**
1. **Horizontal Scaling:**
   - Deploy multiple backend instances
   - Use load balancer (nginx, AWS ELB)
   - Ensure stateless services

2. **Database Optimization:**
   - Add indexes
   - Use read replicas
   - Optimize queries

3. **Caching:**
   - Use Redis for frequent queries
   - Cache brand profiles
   - Cache template data

4. **Async Processing:**
   - Use Celery for long-running tasks
   - Queue content generation
   - Schedule batch operations

5. **CDN:**
   - Host generated media on CDN
   - Reduce backend load
   - Faster content delivery

### Q: What's the maximum file size for uploads?

**A:**
- **Images:** 10MB (configurable)
- **Videos:** 100MB (configurable)
- **Documents:** 50MB (configurable)

Configure in `.env`:
```bash
MAX_UPLOAD_SIZE_MB=10
```

### Q: How often should I backup data?

**A:**
**Recommended:** Daily automated backups

**For Appwrite:**
```bash
# Export collections
curl -X GET https://appwrite.example.com/v1/databases/default/collections \
  -H "X-Appwrite-Project: $PROJECT_ID" \
  -H "X-Appwrite-Key: $API_KEY"

# Backup to external storage
```

### Q: Can I migrate to a different host?

**A:**
Yes, ContentStudio is cloud-agnostic:

1. **Export data** from current database
2. **Configure new environment** with new connection strings
3. **Run migrations** on new database
4. **Update DNS/load balancer** to new host
5. **Monitor for issues** during cutover

See [DEPLOYMENT.md](./DEPLOYMENT.md) for detailed migration steps.

---

## More Questions?

- Check [README.md](./README.md) for overview
- Review [ARCHITECTURE.md](./ARCHITECTURE.md) for system design
- See [API_EXAMPLES.md](./API_EXAMPLES.md) for code examples
- Open GitHub issue with your question
