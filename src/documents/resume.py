from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..storage.models import Profile

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Profile fields (summary, highlights, etc.) are free-text the user typed
# into their own profile - not adversarial today, but this file becomes
# untrusted-input-rendering-to-HTML the moment profile data comes from
# anywhere else (e.g. an import feature), so autoescape is on now rather
# than retrofitted later. Flagged by an independent Codex review.
_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
_env.filters["dateformat"] = lambda d: d.strftime("%m/%Y") if d else "Present"


def render_resume_html(profile: Profile) -> str:
    return _env.get_template("resume.html").render(profile=profile)
