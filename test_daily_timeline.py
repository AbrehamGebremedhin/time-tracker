"""Run: uv run test_daily_timeline.py"""
from daily_timeline import split_note, build_timeline


def test():
    assert split_note("Watch recording || skipped silent parts") == (
        "Watch recording", "skipped silent parts")
    assert split_note("Just a task") == ("Just a task", None)
    assert split_note("") == ("", None)

    entries = [
        {"description": "Late task", "timeInterval":
            {"start": "2026-06-18T10:00:00Z", "duration": "PT18M"}},
        {"description": "Early task || a note", "timeInterval":
            {"start": "2026-06-18T08:00:00Z", "duration": "PT1M"}},
    ]
    lines = build_timeline(entries)
    assert lines[0] == '1. Task: "Early task", 1 minute, "a note"', lines[0]
    assert lines[1] == '2. Task: "Late task", 18 minutes', lines[1]

    # already-wrapped descriptions must not be double-wrapped
    wrapped = [{"description": 'Task: "Deploy changes."', "timeInterval":
               {"start": "2026-06-18T09:00:00Z", "duration": "PT8M"}}]
    assert build_timeline(wrapped)[0] == '1. Task: "Deploy changes.", 8 minutes'
    print("ok")


if __name__ == "__main__":
    test()
