# Deployment Guide

This guide covers local setup, Docker deployment, configuration, and troubleshooting for ContentStudio AI Backend.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Development Setup](#local-development-setup)
3. [Docker Setup](#docker-setup)
4. [Environment Configuration](#environment-configuration)
5. [Running Tests](#running-tests)
6. [Production Deployment](#production-deployment)
7. [Database Setup](#database-setup)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Python 3.11+**
- **Docker & Docker Compose** (for containerized deployment)
- **Git**
- **Appwrite** (self-hosted or cloud instance)
- **NVIDIA NIM API** account with API keys
- **Redis** (for task queue, optional but recommended)
- **PostgreSQL or MongoDB** (database, or use Appwrite)

---

## Local Development Setup

### 1. Clone Repository

```bash
git clone https://github.com/clarity2cloud/open-source-contentstudio-agent.git
cd open-source-contentstudio-agent
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# On Windows
venv\Scripts\activate

# On macOS/Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For development/testing
```

### 4. Configure Environment Variables

Copy `.env.example` to `.env` and update with your configuration:

```bash
cp .env.example .env
```

Edit `.env` with your settings (see Environment Configuration section).

### 5. Start Development Server

```bash
# Using uvicorn directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Or using the provided script
python scripts/run_dev.py
```

The API will be available at `http://localhost:8000`

### 6. Access API Documentation

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## Docker Setup

### 1. Build Docker Image

```bash
docker build -t contentstudio-backend:latest .
```

### 2. Run Container

```bash
docker run -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/logs:/app/logs \
  contentstudio-backend:latest
```

### 3. Using Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  backend:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ENVIRONMENT=production
      - DATABASE_URL=${DATABASE_URL}
      - APPWRITE_API_KEY=${APPWRITE_API_KEY}
      - NVIDIA_NIM_API_KEY=${NVIDIA_NIM_API_KEY}
    volumes:
      - ./logs:/app/logs
    depends_on:
      - redis
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  # Optional: Appwrite
  appwrite:
    image: appwrite/appwrite:latest
    ports:
      - "80:80"
    environment:
      - _APP_ENV=production
    volumes:
      - appwrite_data:/storage
    
volumes:
  appwrite_data:
```

Run:

```bash
docker-compose up -d
```

---

## Environment Configuration

### Required Environment Variables

Create a `.env` file in the project root:

```bash
# Server Configuration
ENVIRONMENT=development  # or production
DEBUG=false
LOG_LEVEL=INFO

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/contentstudio
# OR for Appwrite:
APPWRITE_ENDPOINT=https://appwrite.example.com/v1
APPWRITE_API_KEY=your_api_key_here
APPWRITE_PROJECT_ID=your_project_id

# Authentication
JWT_SECRET_KEY=your-super-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
JWT_REFRESH_EXPIRATION_DAYS=7

# NVIDIA NIM
NVIDIA_NIM_API_KEY=your_nim_api_key
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_MODEL=llama-3.3-nemotron-super-49b-v1

# Image Generation
NVIDIA_IMAGE_GEN_MODEL=FLUX.2-klein-4B

# Video Generation
NVIDIA_VIDEO_GEN_MODEL=cosmos-1-0-diffusion-7b-text2world

# Email Service (Resend)
RESEND_API_KEY=your_resend_api_key

# Social Media APIs
TWITTER_API_KEY=your_twitter_api_key
TWITTER_API_SECRET=your_twitter_secret
TWITTER_ACCESS_TOKEN=your_access_token
TWITTER_ACCESS_SECRET=your_access_secret

LINKEDIN_ACCESS_TOKEN=your_linkedin_token

INSTAGRAM_ACCESS_TOKEN=your_instagram_token

FACEBOOK_PAGE_ACCESS_TOKEN=your_fb_token

# Gamma API (Carousel Generation)
GAMMA_API_KEY=your_gamma_api_key

# Redis (for task queue)
REDIS_URL=redis://localhost:6379/0

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# CORS
CORS_ORIGINS=http://localhost:3000,https://app.example.com

# Email Configuration
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USER=onboarding@resend.dev
SMTP_PASSWORD=your_password

# Analytics
ANALYTICS_ENABLED=true
ANALYTICS_DB_URL=postgresql://user:password@localhost:5432/analytics

# Admin
ADMIN_EMAIL=admin@example.com
```

### Optional Environment Variables

```bash
# Sentry (Error Tracking)
SENTRY_DSN=https://your-sentry-dsn

# Rate Limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60

# Background Jobs
BACKGROUND_JOBS_ENABLED=true
BACKGROUND_JOBS_QUEUE=default

# Timezone
TIMEZONE=UTC
```

---

## Running Tests

### Unit Tests

```bash
pytest tests/ -v
```

### Integration Tests

```bash
pytest tests_runtime/ -v
```

### Test Coverage

```bash
pytest --cov=app --cov-report=html tests/
```

Coverage report will be in `htmlcov/index.html`

### Specific Test Suite

```bash
# Test authentication
pytest tests/test_auth.py -v

# Test AI generation
pytest tests/test_ai_generation.py -v

# Test with markers
pytest -m integration tests/ -v
```

---

## Database Setup

### Appwrite Setup

1. **Create Appwrite Instance**

```bash
# Using Docker
docker run -d \
  -p 80:80 \
  -p 443:443 \
  appwrite/appwrite:latest
```

2. **Create Collections**

Use the migration scripts in `app/db/migrate_missing_collections.py`:

```bash
python app/db/migrate_missing_collections.py
```

3. **Create Indexes**

```bash
python app/db/create_indexes.py
```

### PostgreSQL Setup (Alternative)

1. **Create Database**

```sql
CREATE DATABASE contentstudio;
```

2. **Run Migrations**

```bash
alembic upgrade head
```

---

## Production Deployment

### ⚠️ Before you deploy this publicly

This build ships with authentication stubbed out — every request runs as a shared
`demo-user` (see `app/core/dependencies.py`). The app **refuses to start** with
`ENV=production` unless you set `ALLOW_DEMO_AUTH_IN_PRODUCTION=true` in your `.env`,
so this can never happen by accident. But setting that flag does not make the API
safe — it only lets you acknowledge the risk. Before exposing this to any network you
don't fully trust:

1. **Replace `app/core/dependencies.py`** with real authentication (API key, JWT, OAuth
   — whatever fits your stack), or put a trusted authenticating reverse proxy in front
   of the API.
2. **Set `ENCRYPTION_KEY`** to a dedicated, randomly generated value (not derived from
   `SECRET_KEY`) — the app also refuses to start without this in production. Generate
   one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. **Set `TRUSTED_PROXY_COUNT`** to the number of reverse-proxy hops in front of the
   app (e.g. `1` for a single ingress/load balancer) so per-IP rate limiting can't be
   bypassed by a forged `X-Forwarded-For` header. Leave at `0` if you're not behind a
   known proxy chain.
4. **Review `CORS_ORIGINS`** — it must list only origins you actually trust with
   credentialed requests.

None of this is optional hardening for a real deployment — without step 1 in
particular, every endpoint (content generation, social publishing, stored brand data)
is reachable by anyone who can reach the API.

### Using Kubernetes

1. **Build and Push Image**

```bash
docker build -t your-registry/contentstudio-backend:1.0.0 .
docker push your-registry/contentstudio-backend:1.0.0
```

2. **Deploy to Kubernetes**

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

3. **Configure Ingress**

```bash
kubectl apply -f k8s/ingress.yaml
```

### Using Docker Swarm

```bash
docker swarm init
docker stack deploy -c docker-compose.prod.yml contentstudio
```

### SSL/TLS Configuration

1. **Generate SSL Certificate**

```bash
certbot certonly --standalone -d api.example.com
```

2. **Configure in Nginx/Load Balancer**

Point certificate paths to your generated certificates.

---

## Monitoring & Logs

### View Logs

```bash
# Docker logs
docker logs -f container_id

# Kubernetes logs
kubectl logs -f deployment/contentstudio-backend

# Application logs (stored in logs/ directory)
tail -f logs/app.log
```

### Health Check

```bash
curl http://localhost:8000/health
```

### Metrics

Metrics are available at `/metrics` (if Prometheus integration is enabled).

---

## Troubleshooting

See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for common issues and solutions.

---

## Support

For deployment issues:
1. Check [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
2. Review application logs in `logs/` directory
3. Open an issue on GitHub with error logs and environment details
