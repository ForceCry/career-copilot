from sqlmodel import Session

from ..storage.models import VacancyRecord
from .embeddings import embed
from .es_client import VACANCY_INDEX, ensure_index, get_client

# Section headings (English/German/Polish) that reliably mark where a
# posting's actual responsibilities/requirements start, as opposed to the
# company-marketing boilerplate that conventionally precedes them - the
# three languages actually seen across this project's three ingestion
# sources (Adzuna/Arbeitnow: English or German; justjoin.it: Polish).
# Checked live against all 705 real ingested vacancies: a marker is found
# in 270 of them (38%) - see build_vacancy_embedding_text()'s docstring
# for what that does and doesn't actually buy in terms of truncation.
# Only the FIRST occurrence (lowest string index) counts, so a
# "Requirements" heading further in doesn't lose to an earlier, unrelated
# use of one of these words.
#
# Deliberately excludes benefits/offer-section headings ("about you",
# "co oferujemy" - Polish for "what we offer") - flagged by an independent
# Codex review: those aren't requirements at all, so "earliest marker
# wins" could prioritize a benefits section over the real requirements
# further down, actively making the embedding worse rather than just
# failing to improve it.
_REQUIREMENTS_MARKERS = (
    # English
    "your profile", "your responsibilities", "requirements", "qualifications",
    "what you'll bring", "what you bring",
    "what we're looking for", "what we are looking for", "must have", "nice to have",
    # German
    "dein profil", "deine aufgaben", "deine verantwortung", "das wünschen wir uns",
    "das bringst du mit", "was du mitbringst", "anforderungen",
    # Polish
    "wymagania", "oczekiwania", "twoje zadania", "twoje obowiązki",
    "kogo szukamy", "nasze wymagania", "profil kandydata",
)


def _find_marker(lower_description: str, marker: str) -> int:
    """Finds `marker` in `lower_description` as a real heading, not a
    substring match inside an unrelated word - flagged by an independent
    Codex review: plain str.find() matched "requirements" inside
    "prerequirements" (mid-word) and inside "requirements.txt" (a
    filename reference, not a heading), the latter confirmed live to
    rotate a low-value incidental mention ahead of the real requirements
    section instead of the real one. Requires a non-alphanumeric
    character (or start-of-string) immediately before the marker - this
    alone rules out mid-word matches - and specifically rules out "."
    immediately after, which rules out filename/extension references
    without also requiring whitespace/colon/newline after the marker
    (some real headings run directly into the next word with no
    separator at all, e.g. a scraped "WymaganiaBackend" with the
    original heading/body boundary lost - confirmed live in real
    justjoin.it postings - so requiring a separator there would throw
    away real matches this was built to catch)."""
    start = 0
    while True:
        idx = lower_description.find(marker, start)
        if idx == -1:
            return -1
        before_ok = idx == 0 or not lower_description[idx - 1].isalnum()
        after_idx = idx + len(marker)
        after_ok = after_idx == len(lower_description) or lower_description[after_idx] != "."
        if before_ok and after_ok:
            return idx
        start = idx + 1


def build_vacancy_embedding_text(title: str, description: str) -> str:
    """Constructs the text actually sent to the embedding model - not
    just title+description verbatim, but with the description REORDERED
    (nothing discarded) to put the requirements/responsibilities section
    first when one is detectable, since that's the content that actually
    determines a match. Company-marketing boilerplate conventionally
    comes first in a raw posting, but TEI's 512-token auto_truncate only
    ever sees the FIRST 512 tokens - confirmed live that boilerplate-
    first postings were burning that whole budget on company history/
    funding numbers before ever reaching a single actual requirement.
    Reordering doesn't shrink the text, so it can't change the overall
    truncation rate (249/705, 35%, measured identically before and
    after) - the real effect is narrower: of 705 real ingested
    vacancies, a marker is found in 270 (38%); of those, 146 would have
    reached the model within the 512-token budget even in the original
    order (reordering is a no-op for them in practice), 106 still don't
    fit even reordered (the requirements section itself is too long),
    and 18 (2.5% of all vacancies) are the actual rescues - postings
    where the boilerplate alone would have consumed the whole budget
    in the original order, and now don't. Falls back to the original,
    untouched order when no marker is found - a false negative here just
    means no improvement, never data loss, since the "boilerplate"
    portion is appended after the requirements section, not dropped."""
    lower = description.lower()
    best_pos = None
    for marker in _REQUIREMENTS_MARKERS:
        idx = _find_marker(lower, marker)
        if idx != -1 and (best_pos is None or idx < best_pos):
            best_pos = idx
    if best_pos is None or best_pos == 0:
        # No marker found, or it's already at the very start - nothing to
        # move. Flagged by an independent Codex review: the previous
        # version always inserted a separator space after the marker
        # portion even when there was no prefix to join it to, so
        # "Requirements" (found at position 0) became "Requirements " -
        # a real, if harmless, deviation from the "reorder, don't alter"
        # claim.
        body = description
    else:
        body = f"{description[best_pos:]} {description[:best_pos]}"
    return f"passage: {title}\n\n{body}"


def index_vacancy(session: Session, vacancy_id: int) -> bool:
    """Embeds and indexes one vacancy. Returns False if the id doesn't
    exist (e.g. deleted between publish and consume) rather than raising -
    the worker logs and moves on, one missing vacancy shouldn't halt the
    queue."""
    record = session.get(VacancyRecord, vacancy_id)
    if not record:
        return False

    text = build_vacancy_embedding_text(record.title, record.description)
    vector = embed(text)

    client = get_client()
    ensure_index(client)
    client.index(
        index=VACANCY_INDEX,
        id=record.id,
        document={
            "vacancy_id": record.id,
            "source": record.source,
            "title": record.title,
            "company": record.company,
            "url": record.url,
            "embedding": vector,
        },
    )
    return True
