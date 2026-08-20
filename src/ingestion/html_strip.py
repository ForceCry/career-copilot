from html import unescape
from html.parser import HTMLParser

# Block-level tags to break on when flattening to plain text, so words from
# adjacent elements don't get jammed together ("<p>Hello</p><p>World</p>"
# shouldn't become "HelloWorld"). td/th included - flagged by an independent
# Codex review: without them, adjacent table cells ("<td>Python</td>
# <td>Django</td>") concatenated into a single invented token ("PythonDjango")
# instead of two real ones.
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "td", "th",
}

# Tags whose content is never visible text - flagged by an independent
# Codex review: the original version had no notion of this, so a <style>
# or <script> tag's raw CSS/JS body was appended to the extracted text
# just like real content, reintroducing exactly the token-budget-wasting
# noise this whole function exists to remove. template/head added in a
# follow-up round of the same review: a <template> tag's content is
# never rendered by definition, and a <head>/<title> only shows up if
# some source ever hands us a full HTML document rather than a fragment
# - neither should count as the posting's visible text either.
# Deliberately NOT blanket-ignoring noscript/iframe/svg - their content
# can be conditionally or genuinely visible, unlike these four.
_IGNORE_CONTENT_TAGS = {"script", "style", "template", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._ignore_depth = 0

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0:
            self.chunks.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _IGNORE_CONTENT_TAGS:
            self._ignore_depth += 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORE_CONTENT_TAGS:
            self._ignore_depth = max(0, self._ignore_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.chunks.append(" ")


def strip_html(raw: str, unescape_first: bool = True) -> str:
    """Shared by every ingestion source whose raw description comes back
    as HTML markup rather than plain text - originally written for
    Arbeitnow, later reused for DOU.ua. Left as-is, that markup soup
    burns a large chunk of TEI's 512-token embedding budget on class
    names/tags instead of actual job content.

    `unescape_first` exists because the two sources hand this function
    text at genuinely different escaping depths - a real bug, not a
    hypothetical, flagged by an independent Codex review and confirmed
    live on a real DOU posting. Arbeitnow's raw JSON string value has its
    OWN real tags hidden behind one whole extra layer of HTML-entity
    escaping (e.g. the literal value is "&lt;p&gt;...&lt;/p&gt;", not yet
    "<p>...</p>") - unescaping first is required just to reveal them.
    DOU's RSS <description>, by contrast, arrives via stdlib
    ElementTree, which already resolves the XML-level escaping on its
    own, so its `.text` already contains DOU's real tags directly (e.g.
    a genuine "<br>"). Passing THAT through an extra manual unescape()
    before HTMLParser ever sees it also resolves entities that were
    never meant to become tags - confirmed live: a real posting's prose
    mentioning C# generics contained "Span&lt;T&gt;" as literal text (a
    real "&lt;"/"&gt;" pair meant to stay visible as "<T>"), and the
    extra unescape turned it into an actual "<T>" tag, silently deleting
    it. HTMLParser's own convert_charrefs (on by default) already
    decodes entities correctly *within already-real markup* - it knows
    the difference between a literal "<" character starting a tag and an
    escaped "&lt;" that should stay as visible text - which is exactly
    what DOU needs and what a premature blanket unescape() defeats.
    Arbeitnow passes `unescape_first=True` (the default); DOU passes
    `unescape_first=False`.

    extractor.close() is required, not optional - flagged by an
    independent Codex review: HTMLParser buffers trailing content it
    hasn't fully parsed yet (an incomplete tag, or an entity reference
    missing its terminating ";") in case more data is about to be fed,
    and only flushes that buffer on close(). Without it, a description
    ending mid-entity - confirmed live: `strip_html("visible &amp")`
    returned "" instead of "visible &" - could silently lose real
    trailing content, or in the worst case the whole string."""
    extractor = _TextExtractor()
    extractor.feed(unescape(raw) if unescape_first else raw)
    extractor.close()
    return " ".join("".join(extractor.chunks).split())
