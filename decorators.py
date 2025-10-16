import functools
import logging
import time
from typing import Callable, Type, Tuple

# Configure a basic logger for the library
logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.NullHandler())


def retry(attempts: int = 3, delay: float = 1, backoff: float = 1, on_exception: Type[Exception] | Tuple[Type[Exception], ...] = Exception) -> Callable:
    """
    A decorator to retry a function call with a configurable delay and backoff.

    Args:
        attempts (int): The total number of times to try the function. Must be >= 1.
        delay (float): The initial number of seconds to wait between retries. Must be >= 0.
        backoff (float): The factor by which the delay should be multiplied after each retry. Must be >= 1.
        on_exception (Type[Exception] | Tuple[Type[Exception], ...]): The specific exception
            or a tuple of exceptions to catch and retry on. Defaults to `Exception`.

    Returns:
        Callable: The decorated function.

    Raises:
        ValueError: If arguments are invalid.
    """
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be an integer greater than or equal to 1")
    if not isinstance(delay, (int, float)) or delay < 0:
        raise ValueError("delay must be a non-negative number")
    if not isinstance(backoff, (int, float)) or backoff < 1:
        raise ValueError("backoff must be a number greater than or equal to 1")

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay

            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except on_exception as e:
                    last_exception = e
                    if attempt < attempts:
                        logger.warning(
                            f"Attempt {attempt}/{attempts} for '{func.__name__}' failed with "
                            f"{e.__class__.__name__}: {e}. Retrying in {current_delay:.2f}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {attempts} attempts for '{func.__name__}' failed."
                        )
            # This line is reached only if all attempts fail.
            # We re-raise the last captured exception.
            raise last_exception
        return wrapper
    return decorator
