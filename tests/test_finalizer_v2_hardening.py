from workers import finalizer
from workers.finalizer.models import SubtitleEvent
from workers.finalizer.timing import process_timing_pipeline


def test_text_quality_pass_strips_metadata_and_keeps_core_text():
    events = [{"start": 0.0, "end": 1.0, "text": "Var Og þetta <APPLAUSE>"}]
    processed = finalizer._text_quality_pass(events)
    assert processed[0]["text"].startswith("Var og þetta")


def test_timing_pipeline_quantizes_to_frame_boundaries():
    events = [
        SubtitleEvent(start=0.013, end=0.097, text="Halló"),
        SubtitleEvent(start=0.099, end=0.171, text="heimur"),
    ]
    result = process_timing_pipeline(events, fps=25.0, apply_scene_snap=False)
    for ev in result:
        assert abs((ev.start * 25.0) - round(ev.start * 25.0)) < 1e-6
        assert abs((ev.end * 25.0) - round(ev.end * 25.0)) < 1e-6
        assert ev.end > ev.start
