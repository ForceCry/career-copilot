from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..storage.models import Profile

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
_env.filters["dateformat"] = lambda d: d.strftime("%m/%Y") if d else "Present"


def render_resume_html(profile: Profile) -> str:
    return _env.get_template("resume.html").render(profile=profile)
