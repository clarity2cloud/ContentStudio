# Troubleshooting Guide

Common issues and solutions for ContentStudio AI Backend.

---

## Table of Contents

1. [Startup Issues](#startup-issues)
2. [Database Connection Issues](#database-connection-issues)
3. [API Errors](#api-errors)
4. [AI Generation Issues](#ai-generation-issues)
5. [Social Media Integration Issues](#social-media-integration-issues)
6. [Performance Issues](#performance-issues)
7. [Authentication Issues](#authentication-issues)
8. [Debugging Tips](#debugging-tips)

---

## Startup Issues

### Problem: Backend won't start - ModuleNotFoundError

**Error:**
```
ModuleNotFoundError: No module named 'app'
```

**Solution:**
1. Ensure you're in the project root directory:
   ```bash
   cd contentstudio-backend
   ```

2. Check if virtual environment is activated:
   ```bash
   # Windows
   venv\Scripts\activate
   
   # macOS/Linux
   source venv/bin/activate
   ```

3. Reinstall dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

### Problem: Port 8000 already in use

**Error:**
```
OSError: [Errno 48] Address already in use
```

**Solution:**

**Option 1: Use different port**
```bash
uvicorn app.main:app --port 8001
```

**Option 2: Kill existing process**

Windows:
```bash
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

macOS/Linux:
```bash
lsof -ti:8000 | xargs kill -9
```

---

### Problem: Import errors in startup

**Error:**
```
ImportError: cannot import name 'x' from 'app.y'
```

**Solution:**
1. Check for circular imports in service files
2. Verify all required packages are installed:
   ```bash
   pip install -r requirements.txt --upgrade
   ```
3. Clear Python cache:
   ```bash
   find . -type d -name __pycache__ -exec rm -r {} +
   find . -name "*.pyc" -delete
   ```

---

## Database Connection Issues

### Problem: Cannot connect to Appwrite

**Error:**
```
Connection error: Cannot connect to 'https://appwrite.example.com'
Authentication failed: Invalid API key
```

**Solution:**

1. **Verify Appwrite is running:**
   ```bash
   # If using Docker
   docker ps | grep appwrite
   
   # If using cloud, check status page
   ```

2. **Check environment variables:**
   ```bash
   # Windows
   echo %APPWRITE_ENDPOINT%
   echo %APPWRITE_API_KEY%
   
   # macOS/Linux
   echo $APPWRITE_ENDPOINT
   echo $APPWRITE_API_KEY
   ```

3. **Test connection manually:**
   ```python
   from app.db.appwrite_client import appwrite_client
   
   try:
       health = appwrite_client.health.get()
       print("Connected successfully")
   except Exception as e:
       print(f"Connection failed: {e}")
   ```

4. **Regenerate API key:**
   - Log into Appwrite dashboard
   - Go to Settings → API Keys
   - Generate new API key
   - Update `.env` file
   - Restart backend

---

### Problem: Database collection not found

**Error:**
```
AppwriteException: Collection 'users' not found
```

**Solution:**

1. **Run migration script:**
   ```bash
   python app/db/migrate_missing_collections.py
   ```

2. **Create collections manually via Appwrite UI:**
   - Log into Appwrite dashboard
   - Create collections: users, brands, campaigns, content, etc.

3. **Check collection names:**
   Ensure environment variables match collection names in code:
   ```bash
   # In .env
   APPWRITE_USERS_COLLECTION=users
   APPWRITE_BRANDS_COLLECTION=brands
   ```

---

### Problem: Slow database queries

**Solution:**

1. **Add database indexes:**
   ```bash
   python app/db/create_indexes.py
   ```

2. **Check query performance:**
   ```python
   import time
   
   start = time.time()
   users = appwrite_client.collections.get_collection('users')
   end = time.time()
   print(f"Query took {end - start:.2f}s")
   ```

3. **Limit query results:**
   ```python
   # Bad: fetching all documents
   docs = collection.list_documents()
   
   # Good: use pagination
   docs = collection.list_documents(
       queries=[Query.limit(50), Query.offset(0)]
   )
   ```

---

## API Errors

### Problem: 401 Unauthorized

**Error:**
```
"detail": "Not authenticated"
```

**Solution:**

1. **Check JWT token in request:**
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/v1/me
   ```

2. **Verify token is not expired:**
   ```bash
   # Decode JWT (online tool or Python jwt library)
   import jwt
   
   token = "your_jwt_token"
   decoded = jwt.decode(token, options={"verify_signature": False})
   print(decoded)  # Check 'exp' field
   ```

3. **Get new token:**
   ```bash
   # Login
   curl -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "user@example.com", "password": "password"}'
   ```

---

### Problem: 403 Forbidden

**Error:**
```
"detail": "Not authorized to perform this action"
```

**Solution:**

1. **Check user permissions:**
   - Verify user role in database
   - Check resource ownership (user_id)

2. **For admin operations:**
   - Ensure user has admin role
   - Check role assignment in Appwrite

---

### Problem: 400 Bad Request

**Error:**
```
"detail": "Validation error: Invalid input"
```

**Solution:**

1. **Check request body format:**
   ```bash
   # Correct format
   curl -X POST http://localhost:8000/api/v1/brands \
     -H "Content-Type: application/json" \
     -d '{
       "name": "My Brand",
       "industry": "tech",
       "tone": "professional"
     }'
   ```

2. **Validate required fields:**
   Review API documentation for required fields

3. **Check data types:**
   - Strings should be quoted
   - Numbers should not have quotes
   - Booleans should be lowercase (true/false)

---

### Problem: 500 Internal Server Error

**Error:**
```
"detail": "Internal server error"
```

**Solution:**

1. **Check server logs:**
   ```bash
   # For local development
   tail -f logs/app.log
   
   # For Docker
   docker logs container_id
   
   # For Kubernetes
   kubectl logs pod_name
   ```

2. **Check error trace:**
   Look for the full error message in logs

3. **Common causes:**
   - Database connection lost
   - External API call failed (NVIDIA NIM, social APIs)
   - Unhandled exception in service

---

## AI Generation Issues

### Problem: Content generation fails with rate limit error

**Error:**
```
"detail": "Rate limit exceeded: 100 requests per minute"
```

**Solution:**

1. **Wait before retrying:**
   - Built-in exponential backoff waits up to 60 seconds
   - Manually wait 1-2 minutes before retrying

2. **Batch requests:**
   - Instead of 10 individual requests, use generate-content-suite
   - Reduces total API calls

3. **Upgrade NVIDIA NIM tier:**
   - Higher tier has higher rate limits
   - Contact NVIDIA support

---

### Problem: Generated content is low quality

**Solution:**

1. **Improve brand context:**
   - Ensure brand profile is detailed
   - Add specific tone/voice examples
   - Update brand vocabulary list

2. **Adjust prompts:**
   - Use more specific topic descriptions
   - Include target audience details
   - Add reference content

3. **Change temperature setting:**
   ```python
   # Lower temperature = more deterministic
   response = ai_service.generate_blog(
       topic="...",
       temperature=0.5  # Was 0.7
   )
   ```

---

### Problem: NVIDIA NIM API connection fails

**Error:**
```
requests.exceptions.ConnectionError: Failed to connect to NVIDIA NIM
```

**Solution:**

1. **Verify API endpoint:**
   ```bash
   echo $NVIDIA_NIM_BASE_URL
   # Should be: https://integrate.api.nvidia.com/v1
   ```

2. **Check API key:**
   ```bash
   echo $NVIDIA_NIM_API_KEY
   # Should not be empty
   ```

3. **Test connection:**
   ```bash
   curl -H "Authorization: Bearer $NVIDIA_NIM_API_KEY" \
     https://integrate.api.nvidia.com/v1/models
   ```

4. **Check NVIDIA status page:**
   - Visit https://status.nvidia.com
   - Verify NIM services are operational

---

## Social Media Integration Issues

### Problem: Twitter/X posting fails

**Error:**
```
"detail": "Failed to publish to Twitter"
tweepy.TweepError: 403 Forbidden
```

**Solution:**

1. **Check Twitter API credentials:**
   ```bash
   # Verify tokens in .env
   echo $TWITTER_API_KEY
   echo $TWITTER_ACCESS_TOKEN
   ```

2. **Verify Twitter API permissions:**
   - Log into Twitter Developer Console
   - Check app permissions are set to "Read and Write"
   - Regenerate tokens if needed

3. **Check rate limits:**
   - Twitter allows 300 tweets per 15 minutes
   - Wait and retry after 15 minutes if exceeded

---

### Problem: LinkedIn posting authorization fails

**Error:**
```
"detail": "LinkedIn authentication failed: Invalid token"
```

**Solution:**

1. **Refresh OAuth token:**
   - OAuth tokens expire after a period
   - Implement token refresh in social_media_service.py
   - User may need to re-authorize

2. **Check LinkedIn app permissions:**
   - Log into LinkedIn Developer Console
   - Verify app has "Share on LinkedIn" permission
   - Regenerate access token if needed

3. **Verify organization access:**
   - User must have admin access to LinkedIn Page/Company
   - Check user's LinkedIn permissions

---

## Performance Issues

### Problem: High API latency

**Solution:**

1. **Check slow endpoints:**
   ```bash
   # Enable timing logs
   LOG_LEVEL=DEBUG python -m uvicorn app.main:app
   ```

2. **Profile database queries:**
   ```python
   import time
   
   start = time.time()
   # Your database call
   docs = appwrite_client.collections.list_documents(...)
   print(f"Query time: {time.time() - start:.2f}s")
   ```

3. **Optimize queries:**
   - Add indexes
   - Use pagination
   - Avoid N+1 queries
   - Use query filters server-side

4. **Enable caching:**
   ```python
   from functools import lru_cache
   import redis
   
   redis_client = redis.Redis(host='localhost', port=6379)
   
   @lru_cache(maxsize=128)
   def get_brand_cached(brand_id):
       # Cache for 1 hour
       return get_brand(brand_id)
   ```

---

### Problem: High memory usage

**Solution:**

1. **Monitor memory:**
   ```bash
   # Docker
   docker stats container_id
   
   # Local
   python -m memory_profiler app/main.py
   ```

2. **Identify memory leaks:**
   - Check for circular references
   - Close database connections properly
   - Don't store large objects in memory

3. **Reduce batch sizes:**
   ```python
   # Bad: Process 10,000 items at once
   items = get_all_items()
   
   # Good: Process in batches
   batch_size = 100
   for i in range(0, len(items), batch_size):
       process_batch(items[i:i+batch_size])
   ```

---

### Problem: Slow content generation

**Solution:**

1. **Check NVIDIA NIM latency:**
   - Monitor response times from NIM API
   - Add connection pooling for reuse

2. **Use async generation:**
   ```bash
   POST /api/v1/ai/generate/blog?async=true
   # Returns immediately with task_id
   # Poll for results: GET /tasks/{task_id}
   ```

3. **Enable streaming (if supported):**
   ```python
   response = ai_service.generate_blog(
       topic="...",
       stream=True  # Stream response as it's generated
   )
   ```

---

## Authentication Issues

### Problem: OTP code not received

**Solution:**

1. **Check email configuration:**
   ```bash
   echo $RESEND_API_KEY
   echo $SMTP_HOST
   ```

2. **Verify email address:**
   - User entered correct email
   - Check spam/junk folder

3. **Check OTP expiration:**
   - OTP expires after 5 minutes
   - Request new OTP if expired

4. **Check email logs:**
   ```bash
   tail -f logs/email.log
   ```

---

### Problem: Password reset link expired

**Solution:**

1. **OTP expiration time:**
   - Password reset OTP valid for 30 minutes
   - Request new OTP if expired

2. **Use correct endpoint:**
   ```bash
   POST /api/v1/auth/reset-password
   {
       "email": "user@example.com",
       "otp": "123456",
       "new_password": "SecurePassword123"
   }
   ```

---

## Debugging Tips

### Enable Debug Logging

**File:** `.env`
```bash
DEBUG=true
LOG_LEVEL=DEBUG
```

### View Detailed Request/Response

**Using curl:**
```bash
curl -v -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "password"}'
```

**Using Python requests:**
```python
import requests
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

response = requests.post(
    "http://localhost:8000/api/v1/auth/login",
    json={"email": "user@example.com", "password": "password"}
)
print(response.text)
```

### Database Query Debugging

```python
from app.db.appwrite_client import appwrite_client
from appwrite.query import Query

# List users with detailed output
users = appwrite_client.databases.list_documents(
    database_id="default",
    collection_id="users",
    queries=[Query.limit(5)]
)

for user in users['documents']:
    print(f"User: {user['email']} - ID: {user['$id']}")
```

### Check Running Processes

**Docker:**
```bash
docker ps
docker stats
docker logs -f container_id
```

**Local:**
```bash
# Check if Redis is running
redis-cli ping

# Check if Appwrite is accessible
curl http://localhost/v1/health
```

---

## Getting Help

If issue persists:

1. Check logs in `logs/` directory
2. Review [ARCHITECTURE.md](./ARCHITECTURE.md) for system design
3. Check [FAQ.md](./FAQ.md) for common questions
4. Open GitHub issue with:
   - Error logs
   - Steps to reproduce
   - Environment details
   - Recent changes made

---

## Support Resources

- **GitHub Issues:** https://github.com/clarity2cloud/open-source-contentstudio-agent/issues
- **Documentation:** [README.md](./README.md), [ARCHITECTURE.md](./ARCHITECTURE.md)
- **API Docs:** http://localhost:8000/docs (when running locally)
