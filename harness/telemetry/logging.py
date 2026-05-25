import logging, structlog

def configure_logging():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

def get_logger(name: str):
    return structlog.get_logger(name)
