from job_search_agent.logging_config import get_logger, logger


def test_get_logger_returns_logger():
    lg = get_logger("job_search_agent.test")
    assert lg is not None
    assert hasattr(lg, "info")


def test_module_logger_has_handlers():
    assert logger is not None
    assert len(logger.handlers) >= 1
