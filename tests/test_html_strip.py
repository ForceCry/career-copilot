import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.html_strip import strip_html  # noqa: E402


def test_strip_html_removes_tags_and_unescapes_entities():
    raw = "&lt;p class=&quot;sc-gEkIjz cjtSrT MuiTypography-root&quot;&gt;Hello &amp;amp; welcome&lt;/p&gt;"
    assert strip_html(raw) == "Hello & welcome"


def test_strip_html_inserts_space_between_block_elements():
    """Regression: naive tag-stripping without any separator would merge
    adjacent block elements' text together - "<p>Hello</p><p>World</p>"
    must not become "HelloWorld"."""
    raw = "&lt;p&gt;Hello&lt;/p&gt;&lt;p&gt;World&lt;/p&gt;"
    assert strip_html(raw) == "Hello World"


def test_strip_html_collapses_whitespace():
    raw = "&lt;div&gt;  Multiple   \n\n  spaces  &lt;/div&gt;"
    assert strip_html(raw) == "Multiple spaces"


def test_strip_html_passes_through_plain_text_unchanged():
    assert strip_html("Just plain text, no markup at all.") == "Just plain text, no markup at all."


def test_strip_html_empty_string():
    assert strip_html("") == ""


def test_strip_html_discards_script_and_style_content():
    """Regression: an independent Codex review found that <script>/<style>
    tag bodies (raw JS/CSS, never visible text) were being appended to
    the extracted text just like real content - reintroducing exactly
    the token-budget-wasting noise this function exists to remove."""
    raw = (
        "&lt;p&gt;Role&lt;/p&gt;&lt;style&gt;.sc{color:red}&lt;/style&gt;"
        "&lt;script&gt;window.x=1;&lt;/script&gt;&lt;p&gt;End&lt;/p&gt;"
    )
    assert strip_html(raw) == "Role End"


def test_strip_html_separates_table_cells():
    """Regression: an independent Codex review found td/th weren't in
    the block-boundary set, so adjacent table cells concatenated into a
    single invented token ("PythonDjango" instead of "Python Django")."""
    raw = (
        "&lt;table&gt;&lt;tr&gt;&lt;td&gt;Python&lt;/td&gt;"
        "&lt;td&gt;Django&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;"
    )
    assert strip_html(raw) == "Python Django"


def test_strip_html_does_not_drop_trailing_incomplete_content():
    """Regression: a follow-up Codex review round found HTMLParser
    buffers trailing content it hasn't fully parsed yet (an entity
    reference missing its terminating ";") in case more data is about
    to be fed, and only flushes that buffer on close(). "visible &amp"
    without a trailing ";" is exactly that case."""
    assert strip_html("visible &amp") == "visible &"


def test_strip_html_with_unescape_first_false_preserves_entity_escaped_angle_brackets():
    """Regression: an independent Codex review found that DOU.ua's raw
    description (already containing real tags via ElementTree's own XML
    decode) got a redundant manual unescape() before this, which turned
    literal escaped text like "Span&lt;T&gt;" (meant to stay visible as
    "Span<T>") into an actual "<T>" tag that then got silently stripped
    - confirmed live on a real posting mentioning C# generics. With
    unescape_first=False, HTMLParser's own entity handling (on by
    default) correctly tells a real "<br>" tag apart from escaped
    "&lt;T&gt;" text without an extra manual pass."""
    raw = "Uses Span&lt;T&gt; and Memory&lt;T&gt;.<br>Real paragraph."
    assert strip_html(raw, unescape_first=False) == "Uses Span<T> and Memory<T>. Real paragraph."


def test_strip_html_unescape_first_true_is_still_the_default_for_arbeitnow():
    """Arbeitnow's raw JSON value has its OWN real tags hidden behind a
    full extra layer of HTML-entity escaping - unescape_first=True (the
    default) is required just to reveal them, unlike DOU."""
    raw = "&lt;p&gt;Hello&lt;/p&gt;"
    assert strip_html(raw) == "Hello"


def test_strip_html_discards_head_and_template_content():
    """Regression: a follow-up Codex review round found <template>
    (never rendered by definition) and <head>/<title> (only relevant if
    a source ever hands us a full HTML document instead of a fragment)
    still leaked into the extracted text."""
    head = "&lt;head&gt;&lt;title&gt;hidden title&lt;/title&gt;&lt;/head&gt;&lt;p&gt;Visible&lt;/p&gt;"
    template = "&lt;template&gt;hidden template&lt;/template&gt;&lt;p&gt;Visible&lt;/p&gt;"
    assert strip_html(head) == "Visible"
    assert strip_html(template) == "Visible"
