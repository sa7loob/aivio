import random


def backoff_seconds(attempt: int, *, base: float = 5.0, cap: float = 900.0) -> float:
    """Exponential backoff مع jitter: 5s, 10s, 20s ... حتى 15 دقيقة."""
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay * random.uniform(0.8, 1.2)
