import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    # لا نسجّل محتوى طلبات httpx (قد تحتوي توكنات في الهيدر عند debug)
    logging.getLogger("httpx").setLevel(logging.WARNING)
