# app/utils/helpers.py

"""
Utility Helper Functions for ContentStudio AI

This module contains reusable helper functions for:
- Text processing
- Date/time utilities
- File handling
- Data validation
- String manipulation
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import re
import hashlib
import secrets
import string
from urllib.parse import urlparse


# ==================== TEXT PROCESSING ====================

def truncate_text(
        text: str,
        max_length: int = 280,
        suffix: str = "...") -> str:
    """
    Truncate text to specified length

    Args:
        text: Text to truncate
        max_length: Maximum length (default: 280 for Twitter)
        suffix: Suffix to add when truncated

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)].rstrip() + suffix


def extract_hashtags(text: str) -> List[str]:
    """Extract all hashtags from text"""
    hashtag_pattern = r'#(\w+)'
    return re.findall(hashtag_pattern, text)


def extract_mentions(text: str) -> List[str]:
    """Extract all @mentions from text"""
    mention_pattern = r'@(\w+)'
    return re.findall(mention_pattern, text)


def extract_urls(text: str) -> List[str]:
    """Extract all URLs from text"""
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    return re.findall(url_pattern, text)


def count_words(text: str) -> int:
    """Count words in text"""
    return len(text.split())


def clean_text(text: str) -> str:
    """Clean text by removing extra whitespace"""
    return ' '.join(text.split()).strip()


# ==================== DATE/TIME UTILITIES ====================

def format_datetime(dt: datetime, format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format datetime to string"""
    return dt.strftime(format)


def parse_datetime(
        date_string: str,
        format: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    """Parse string to datetime"""
    try:
        return datetime.strptime(date_string, format)
    except ValueError:
        return None


def get_time_ago(dt: datetime) -> str:
    """Get human-readable time ago string"""
    now = datetime.utcnow()
    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    elif seconds < 31536000:
        months = int(seconds / 2592000)
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = int(seconds / 31536000)
        return f"{years} year{'s' if years != 1 else ''} ago"


def is_future_date(dt: datetime) -> bool:
    """Check if datetime is in the future"""
    return dt > datetime.utcnow()


def add_days(dt: datetime, days: int) -> datetime:
    """Add days to datetime"""
    return dt + timedelta(days=days)


# ==================== STRING UTILITIES ====================

def slugify(text: str) -> str:
    """Convert text to URL-friendly slug"""
    text = text.lower()
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'[^a-z0-9-]', '', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def generate_random_string(
        length: int = 32,
        include_special: bool = False) -> str:
    """Generate random string"""
    if include_special:
        chars = string.ascii_letters + string.digits + string.punctuation
    else:
        chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def generate_hash(text: str, algorithm: str = "sha256") -> str:
    """Generate hash of text"""
    hash_obj = hashlib.new(algorithm)
    hash_obj.update(text.encode('utf-8'))
    return hash_obj.hexdigest()


def mask_email(email: str) -> str:
    """Mask email for privacy"""
    if '@' not in email:
        return email
    username, domain = email.split('@')
    if len(username) <= 2:
        masked_username = username[0] + '*'
    else:
        masked_username = username[0] + '*' * \
            (len(username) - 2) + username[-1]
    return f"{masked_username}@{domain}"


# ==================== VALIDATION UTILITIES ====================

def is_valid_email(email: str) -> bool:
    """Validate email address"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def is_valid_url(url: str) -> bool:
    """Validate URL"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except BaseException:
        return False


def is_strong_password(password: str, min_length: int = 6) -> Dict[str, Any]:
    """Check password strength"""
    suggestions = []
    if len(password) < min_length:
        suggestions.append(f"Use at least {min_length} characters")
    if not any(c.isupper() for c in password):
        suggestions.append("Include at least one uppercase letter")
    if not any(c.islower() for c in password):
        suggestions.append("Include at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        suggestions.append("Include at least one digit")
    if not any(c in string.punctuation for c in password):
        suggestions.append("Include at least one special character")

    return {
        "is_strong": len(suggestions) == 0,
        "suggestions": suggestions
    }


# ==================== DATA UTILITIES ====================

def paginate(items: List[Any], page: int = 1,
             page_size: int = 20) -> Dict[str, Any]:
    """Paginate list of items"""
    total = len(items)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }


def chunk_list(items: List[Any], chunk_size: int) -> List[List[Any]]:
    """Split list into chunks"""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def remove_duplicates(items: List[Any]) -> List[Any]:
    """Remove duplicates from list while preserving order"""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def flatten_dict(d: Dict[str, Any], parent_key: str = '',
                 sep: str = '.') -> Dict[str, Any]:
    """Flatten nested dictionary"""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


# ==================== FILE UTILITIES ====================

def get_file_extension(filename: str) -> str:
    """Get file extension"""
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def format_file_size(size_bytes: int) -> str:
    """Format file size to human-readable string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


# ==================== SOCIAL MEDIA UTILITIES ====================

def calculate_engagement_rate(
        likes: int,
        comments: int,
        shares: int,
        impressions: int) -> float:
    """Calculate engagement rate"""
    if impressions == 0:
        return 0.0
    total_engagement = likes + comments + shares
    rate = (total_engagement / impressions) * 100
    return round(rate, 2)


def get_best_time_to_post(platform: str = "twitter") -> str:
    """Get recommended best time to post for a platform"""
    best_times = {
        "twitter": "Weekdays 9 AM - 3 PM (your timezone)",
        "instagram": "Weekdays 11 AM - 1 PM (your timezone)",
        "linkedin": "Weekdays 7 AM - 8 AM, 12 PM, 5 PM - 6 PM (your timezone)",
        "facebook": "Weekdays 1 PM - 3 PM (your timezone)"
    }
    return best_times.get(
        platform.lower(),
        "Weekdays 9 AM - 5 PM (your timezone)")


def estimate_reading_time(text: str, words_per_minute: int = 200) -> int:
    """Estimate reading time in minutes"""
    word_count = count_words(text)
    return max(1, round(word_count / words_per_minute))
