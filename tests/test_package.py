from chatto_threaddump import main


def test_console_entry_point_exists() -> None:
    assert callable(main)
