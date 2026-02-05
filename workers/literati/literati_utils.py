
import time
import functools
import logging
import random
from typing import Type, Tuple, Optional

logger = logging.getLogger("Literati.Utils")

def retry_api_call(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    retry_exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Decorator to retry API calls with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay in seconds.
        retry_exceptions: Tuple of exception types to retry on.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while attempt <= max_retries:
                try:
                    return func(*args, **kwargs)
                except retry_exceptions as e:
                    attempt += 1
                    if attempt > max_retries:
                        logger.error(f"❌ Failed after {max_retries} retries: {e}")
                        raise e
                    
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    # Add jitter
                    delay = delay * (0.5 + random.random())
                    
                    error_msg = str(e)
                    if "429" in error_msg or "ResourceExhausted" in error_msg:
                        logger.warning(f"⚠️ Rate limit hit. Retrying in {delay:.2f}s... (Attempt {attempt}/{max_retries})")
                    else:
                        logger.warning(f"⚠️ API Error: {e}. Retrying in {delay:.2f}s... (Attempt {attempt}/{max_retries})")
                        
                    time.sleep(delay)
            return None # Should not reach here
        return wrapper
    return decorator
