import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.search.indexer import build_vacancy_embedding_text  # noqa: E402


def test_reorders_description_to_put_requirements_section_first():
    """Regression: company-marketing boilerplate conventionally comes
    first in a raw posting, but TEI's 512-token auto_truncate only ever
    sees the first 512 tokens - confirmed live this was burning the
    whole embedding budget on company history before ever reaching an
    actual requirement."""
    description = "Acme is a great company founded in 2010. Requirements: 5 years PHP, Symfony, MySQL."
    text = build_vacancy_embedding_text("PHP Developer", description)

    assert text.startswith("passage: PHP Developer\n\nRequirements: 5 years PHP, Symfony, MySQL.")
    assert "Acme is a great company founded in 2010." in text


def test_falls_back_to_original_order_when_no_marker_found():
    description = "Just a plain description with no recognizable section headings at all."
    text = build_vacancy_embedding_text("Backend Engineer", description)

    assert text == f"passage: Backend Engineer\n\n{description}"


def test_recognizes_german_markers():
    description = "Wir sind ein tolles Unternehmen. Dein Profil: 5 Jahre PHP Erfahrung."
    text = build_vacancy_embedding_text("PHP Entwickler", description)

    assert text.startswith("passage: PHP Entwickler\n\nDein Profil: 5 Jahre PHP Erfahrung.")


def test_recognizes_polish_markers():
    description = "Jesteśmy świetną firmą. Wymagania: 5 lat doświadczenia PHP."
    text = build_vacancy_embedding_text("Programista PHP", description)

    assert text.startswith("passage: Programista PHP\n\nWymagania: 5 lat doświadczenia PHP.")


def test_uses_earliest_marker_when_multiple_present():
    description = "Intro text. Your Responsibilities: build things. Requirements: PHP."
    text = build_vacancy_embedding_text("Developer", description)

    assert text.startswith("passage: Developer\n\nYour Responsibilities: build things. Requirements: PHP.")


def test_does_not_match_marker_mid_word():
    """Regression: an independent Codex review found plain str.find()
    matched "requirements" inside "prerequirements" - a real word-
    boundary check is required before the marker, not just a substring
    search."""
    description = "See prerequirements doc first. Real Requirements: PHP."
    text = build_vacancy_embedding_text("Developer", description)

    assert text.startswith("passage: Developer\n\nRequirements: PHP.")


def test_does_not_match_marker_before_a_period():
    """Regression: an independent Codex review found "requirements.txt"
    (a filename reference, not a heading) was matching and rotating a
    low-value incidental mention ahead of the real requirements section
    further down."""
    description = "Company intro is long. Install via requirements.txt. Actual Requirements: PHP."
    text = build_vacancy_embedding_text("Developer", description)

    assert text.startswith("passage: Developer\n\nRequirements: PHP.")


def test_still_matches_a_marker_glued_directly_to_the_next_word():
    """Some real postings (confirmed live against justjoin.it's scraped
    plain text) run a heading directly into the next word with no
    separator at all, e.g. "WymaganiaBackend" - the fix for the period-
    filename case above must not also reject this."""
    description = "Firma opis. WymaganiaBackend i architektura: PHP."
    text = build_vacancy_embedding_text("Developer", description)

    assert text.startswith("passage: Developer\n\nWymaganiaBackend i architektura: PHP.")


def test_does_not_prioritize_a_benefits_section_over_requirements():
    """Regression: an independent Codex review found "co oferujemy"
    (Polish for "what we offer" - a benefits heading, not requirements)
    was in the marker list, so a benefits section appearing before the
    real requirements section would win the earliest-match race and
    actively make the embedding worse, not just fail to improve it."""
    description = "Intro. Co oferujemy: benefits here. Wymagania: PHP, MySQL."
    text = build_vacancy_embedding_text("Developer", description)

    assert text.startswith("passage: Developer\n\nWymagania: PHP, MySQL.")


def test_marker_at_position_zero_does_not_add_a_trailing_space():
    """Regression: an independent Codex review found the reorder always
    inserted a separator space after the marker portion, even when
    there was no boilerplate prefix to join it to - "Requirements: PHP"
    (found at position 0) became "Requirements: PHP " with a trailing
    space, a real (if harmless) deviation from "reorder, don't alter"."""
    description = "Requirements: PHP only, nothing before it."
    text = build_vacancy_embedding_text("Developer", description)

    assert text == f"passage: Developer\n\n{description}"
