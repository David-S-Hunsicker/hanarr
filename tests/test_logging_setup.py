import logging

from hanarr.logging_setup import configure_file_logging


def test_configure_file_logging_creates_the_log_file_and_writes_to_it(tmp_path):
    root = logging.getLogger()
    added_handlers = []
    try:
        log_path = configure_file_logging(tmp_path)
        added_handlers = [h for h in root.handlers if getattr(h, "baseFilename", None) == str(log_path)]

        assert log_path == tmp_path / "logs" / "hanarr.log"
        assert log_path.exists()

        logging.getLogger("hanarr.test").error("a distinctive test message")
        for handler in added_handlers:
            handler.flush()

        assert "a distinctive test message" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in added_handlers:
            root.removeHandler(handler)
            handler.close()


def test_configure_file_logging_returns_the_log_path_under_data_dir_logs(tmp_path):
    root = logging.getLogger()
    log_path = configure_file_logging(tmp_path / "data")
    try:
        assert log_path.parent == tmp_path / "data" / "logs"
        assert log_path.name == "hanarr.log"
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "baseFilename", None) == str(log_path):
                root.removeHandler(handler)
                handler.close()
