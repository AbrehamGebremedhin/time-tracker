import sys

from bot import run_capture, handle


def test_capture_passes_argv_and_returns_stdout():
    def fake():
        print("hello", sys.argv[1])
    assert run_capture(fake, ["x", "world"]) == "hello world"


def test_capture_reports_errors_instead_of_raising():
    def boom():
        raise ValueError("nope")
    assert "nope" in run_capture(boom, ["x"])


def test_argv_is_restored():
    before = list(sys.argv)
    run_capture(lambda: None, ["x", "y"])
    assert sys.argv == before


def test_unknown_command_shows_help():
    assert "/report" in handle("/hello")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
