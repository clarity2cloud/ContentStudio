# ContentStudio

Create blog posts, social media content, campaigns, and multi-platform copy — all aligned to your brand voice. One backend, every channel.

## Why this is open source

ContentStudio was built by [THQ.Digital](https://thq.digital), the enterprise AI infrastructure company behind **Ritvam** (governance), **Sutra** (intelligence/context), and **MIRA** (sovereign inference). We built a number of AI agents like this one along the way — and we've come to believe that building agents has become commodity work. What's genuinely hard, and what we're now focused on, is the governance and intelligence infrastructure that makes agents like this one safe to run at enterprise scale.

So we're open-sourcing ContentStudio, free, for anyone to use, extend, and improve.

## What it does

- Generates blog posts, tweets, LinkedIn/Instagram/Facebook captions, and email/newsletter copy from a single topic
- Multi-platform generation in one call — one topic in, channel-native content out for every platform at once
- Brand-aware generation — pass brand context (tone, voice, vocabulary) and every output inherits it
- Headline and value-proposition generation for campaigns
- Campaign management with an editorial calendar
- Content library with search, export, and status tracking
- Built-in scheduler for publishing workflows
- **No authentication required** — every request runs as a demo user
- **Unlimited generation** — no credit system, billing, or usage limits
- Works **with or without** a database — no Appwrite instance configured, it falls back to in-memory responses instead of failing

## Getting started

### Prerequisites

- Python 3.11+
- An [NVIDIA NIM](https://build.nvidia.com) API key (free tier available) — this powers all AI generation
- *(Optional)* An [Appwrite](https://appwrite.io) instance if you want persistent storage instead of in-memory fallback

### Install and run

```bash
git clone https://github.com/clarity2cloud/open-source-contentstudio-agent.git
cd open-source-contentstudio-agent

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and set at minimum: NVIDIA_API_KEY, SECRET_KEY

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now running at `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`.

### Run with Docker

If you prefer containerized deployment:

#### Option 1: Docker Compose (Recommended)
```bash
# Build and run in one command
docker-compose up --build

# Expected output:
# [+] Building 45.2s (9/9) FINISHED
# [+] Running 1/1
#   ✔ contentstudio-backend Pulled
#   ✔ Container contentstudio-backend-1 Started
```

#### Option 2: Docker Direct

**Step 1: Build the image**
```bash
docker build -t contentstudio-backend .

# Expected output:
# [+] Building 128.2s (13/13) FINISHED
# => naming to docker.io/library/contentstudio-backend:latest
```

**Step 2: Run the container**
```bash
docker run -p 8000:8000 --env-file .env --name contentstudio-test contentstudio-backend

# Expected output:
# INFO:     Started server process [1]
# INFO:     Uvicorn running on http://0.0.0.0:8000
# INFO:     Application startup complete
# (Keep this terminal open - container is running)
```

**Step 3: Test in another terminal**
```bash
curl http://localhost:8000/health

# Expected response:
# {"status":"healthy","version":"3.0.0","services":{...}}
```

**Step 4: Stop the container**
```bash
# Option A: Press Ctrl+C in the terminal where container is running

# Option B: In a new terminal, run:
docker stop contentstudio-test

# Clean up:
docker rm contentstudio-test
```

Both approaches will:
- Mount the `storage/media` directory for persistent media files
- Expose the API on port 8000
- Read configuration from your `.env` file
- Automatically restart if the container crashes

#### Verify it's Running

**Terminal 1: Start the container**
```bash
docker run -p 8000:8000 --env-file .env contentstudio-backend

# Expected output:
# INFO:     Uvicorn running on http://0.0.0.0:8000
# INFO:     Application startup complete
```

**Terminal 2: Test the health endpoint**
```bash
curl http://localhost:8000/health

# Expected response:
# {"status":"ok"}
```

**Test content generation**
```bash
curl -X POST http://localhost:8000/api/v1/ai/generate-native \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "blog",
    "topic": "How AI is changing content creation"
  }'

# Expected response (successful):
# {
#   "content": "...(generated blog post)...",
#   "platform": "blog",
#   "content_id": "abc123...",
#   "duration_ms": 2341,
#   "metadata": {...}
# }
```

**Access the interactive API docs**
Open in your browser: http://localhost:8000/docs

The API will be at `http://localhost:8000`.

### Generate your first piece of content

```bash
curl -X POST http://localhost:8000/api/v1/ai/generate-native \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "blog",
    "topic": "How AI is changing content creation"
  }'
```

No login, no API key in the request, no credit balance to check — it just runs.

### Configuration

Everything is set via `.env` (see [.env.example](.env.example) for the full list, and [app/config.py](app/config.py) for every variable the app reads). The two that matter for a first run:

| Variable | Purpose |
|---|---|
| `NVIDIA_API_KEY` | Required for AI generation (NVIDIA NIM) |
| `SECRET_KEY` | Required at startup, minimum 32 characters |

**No billing required.** This version of ContentStudio has zero credit/usage tracking — all generation is unlimited and free.

Appwrite (`APPWRITE_ENDPOINT`, `APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`) and social platform credentials are optional — leave them unset to run in local/demo mode with in-memory storage.

## Project structure

```
app/
├── main.py            # App factory, middleware, router registration
├── config.py          # Settings (pydantic-settings)
├── api/v1/             # All API routers (ai_generation, brand, campaigns, content, ...)
├── core/               # Dependencies, security, exceptions, database
├── db/                 # Appwrite client with graceful in-memory fallback
├── middleware/         # Rate limiting, CSRF, tenant isolation, error handling
├── models/             # Pydantic schemas
├── services/           # AI generation, social platforms, analytics, scheduling
├── tasks/               # Background jobs (Celery, with a no-op fallback)
└── utils/               # Logging, sanitization, encryption helpers
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full breakdown, and [DEPLOYMENT.md](DEPLOYMENT.md) for production setup.


## Docker Quick Reference

| Task | Command |
|------|---------|
| Build image | `docker build -t contentstudio-backend .` |
| Run container | `docker run -p 8000:8000 --env-file .env contentstudio-backend` |
| Run with compose | `docker-compose up --build` |
| Stop container | `docker-compose down` |
| View logs | `docker-compose logs -f` |
| Test health | `curl http://localhost:8000/health` |
| API docs | Open `http://localhost:8000/docs` in browser |
| Storage location | `storage/media` (mounted in container) |

## Docker Troubleshooting

**Port already in use:**
```bash
# If port 8000 is already in use, map to a different port:
docker run -p 8001:8000 --env-file .env contentstudio-backend

# Then access at http://localhost:8001
```

**View container logs:**
```bash
# See real-time logs
docker logs -f contentstudio-backend

# Or with docker-compose
docker-compose logs -f
```

**Restart the container:**
```bash
docker-compose down
docker-compose up --build
```

**Remove images/containers:**
```bash
# Remove stopped containers
docker container prune

# Remove unused images
docker image prune -a
```

## Want this running with enterprise-grade governance?

If you're deploying this (or agents like it) inside a regulated enterprise and need audit trails, policy enforcement, access control, or sovereign/on-premises deployment, check out **[Ritvam](https://thq.digital)** — THQ.Digital's governance control plane, built for exactly this.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Contributing

Issues and pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Support

This is a community-maintained open-source project. For enterprise support or custom deployments, contact [support@thq.digital](mailto:support@thq.digital).
