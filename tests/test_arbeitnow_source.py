import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.sources.arbeitnow import _strip_html  # noqa: E402


def test_strip_html_removes_tags_and_unescapes_entities():
    raw = "&lt;p class=&quot;sc-gEkIjz cjtSrT MuiTypography-root&quot;&gt;Hello &amp;amp; welcome&lt;/p&gt;"
    assert _strip_html(raw) == "Hello & welcome"


def test_strip_html_inserts_space_between_block_elements():
    """Regression: naive tag-stripping without any separator would merge
    adjacent block elements' text together - "<p>Hello</p><p>World</p>"
    must not become "HelloWorld"."""
    raw = "&lt;p&gt;Hello&lt;/p&gt;&lt;p&gt;World&lt;/p&gt;"
    assert _strip_html(raw) == "Hello World"


def test_strip_html_collapses_whitespace():
    raw = "&lt;div&gt;  Multiple   \n\n  spaces  &lt;/div&gt;"
    assert _strip_html(raw) == "Multiple spaces"


def test_strip_html_passes_through_plain_text_unchanged():
    """Adzuna and justjoin.it descriptions never contain HTML - confirmed
    live against the full ingested dataset - so this must be a no-op
    (beyond whitespace collapsing) for plain text."""
    assert _strip_html("Just plain text, no markup at all.") == "Just plain text, no markup at all."


def test_strip_html_empty_string():
    assert _strip_html("") == ""


def test_strip_html_discards_script_and_style_content():
    """Regression: an independent Codex review found that <script>/<style>
    tag bodies (raw JS/CSS, never visible text) were being appended to
    the extracted text just like real content - reintroducing exactly
    the token-budget-wasting noise this function exists to remove."""
    raw = (
        "&lt;p&gt;Role&lt;/p&gt;&lt;style&gt;.sc{color:red}&lt;/style&gt;"
        "&lt;script&gt;window.x=1;&lt;/script&gt;&lt;p&gt;End&lt;/p&gt;"
    )
    assert _strip_html(raw) == "Role End"


def test_strip_html_separates_table_cells():
    """Regression: an independent Codex review found td/th weren't in
    the block-boundary set, so adjacent table cells concatenated into a
    single invented token ("PythonDjango" instead of "Python Django")."""
    raw = (
        "&lt;table&gt;&lt;tr&gt;&lt;td&gt;Python&lt;/td&gt;"
        "&lt;td&gt;Django&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;"
    )
    assert _strip_html(raw) == "Python Django"
