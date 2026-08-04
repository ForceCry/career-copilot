import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.documents.resume import render_resume_html  # noqa: E402
from src.storage.models import Experience, Profile  # noqa: E402


def _profile(**overrides) -> Profile:
    defaults = dict(full_name="Test User", summary="Backend engineer.")
    profile = Profile(**{**defaults, **overrides})
    profile.skills = []
    profile.experiences = []
    profile.educations = []
    return profile


def test_profile_text_is_escaped_not_rendered_as_html():
    """Regression: an independent Codex review found the Jinja2 Environment
    had no autoescape configured, so any '<'/'>' in profile data (summary,
    highlights, etc.) would render as live HTML rather than literal text -
    an XSS vector the moment that data stops being exclusively
    self-authored (e.g. a future import feature)."""
    profile = _profile(summary="<script>alert(1)</script>")

    html = render_resume_html(profile)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_experience_highlights_are_escaped():
    profile = _profile()
    profile.experiences = [
        Experience(
            title="Engineer",
            company='Acme"><img src=x onerror=alert(1)>',
            start_date=date(2020, 1, 1),
            end_date=None,
            highlights="Did stuff.",
        )
    ]

    html = render_resume_html(profile)

    assert "<img src=x onerror=alert(1)>" not in html
