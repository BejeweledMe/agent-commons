"""The walkthrough the guide admitted it did not have.

Round 3 scored "In short" 9/10 as onboarding and the reference pages 3/10, and
the honest repair at the time was to stop calling reference a tutorial.  This
page is the tutorial: one worked example, from a directory the panel has never
seen to work a person accepted, on the onboarding side of the strip's rule.

It lands as a settled scaffold and a drafted text, and it says which is which
at the top.  That combination is the point of these assertions: the eight steps
and their order are a claim about the product and are pinned, while the prose
under them is explicitly a sketch to be rewritten from a real walk.  The
pictures are marked gaps -- a screenshot of this panel is taken off a working
panel, and one drawn in advance would be a drawing of a guess -- so what is
pinned about them is that they are gaps, not that they are pictures.
"""

from __future__ import annotations

import re

from agent_commons.ui import read_spa

# The path, and its order.  These are the claim; the words under them are not.
BUILD_STEPS = (
    "setup",
    "hire",
    "task",
    "run",
    "answer",
    "review",
    "accept",
    "next",
)


def _language_tables() -> tuple[str, str]:
    table = read_spa().split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    return (
        table.split("en: {", 1)[1].split("\n  },", 1)[0],
        table.split("ru: {", 1)[1].split("\n  },", 1)[0],
    )


def _value(block: str, key: str) -> str:
    match = re.search(rf'^\s*{key}: "(.*)",$', block, re.MULTILINE)
    assert match, key
    return match.group(1)


def _guide_markup() -> str:
    return (
        read_spa()
        .split('<section class="view" id="view-guide">', 1)[1]
        .split("\n    </section>", 1)[0]
    )


def _page() -> str:
    return _guide_markup().split('<div id="gpage-build"', 1)[1].split("\n        </div>", 1)[0]


def test_the_walkthrough_is_onboarding_and_sits_on_that_side_of_the_strip() -> None:
    """The strip divides one onboarding page from six reference ones and says so
    in a label.  A walkthrough belongs on the first side -- and putting it past
    the rule would make the rule wrong rather than making the page reference."""

    guide = _guide_markup()
    strip = guide.split('id="guide-tabs"', 1)[1].split("</div>", 1)[0]
    assert strip.index('data-gpage="brief"') < strip.index('data-gpage="build"')
    assert strip.index('data-gpage="build"') < strip.index("tabsplit")

    # And it does not wear the reference lead, because it is not reference.
    page = _page()
    assert "guide_ref_lead" not in page

    english, russian = _language_tables()
    assert _value(english, "guide_tab_build") == "How to build a product"
    assert _value(russian, "guide_tab_build") == "Как собрать продукт"
    # It opens hidden like every page but the first, and the one tab handler
    # shows it: no second mechanism was added for a new page.
    assert '<div id="gpage-build" hidden>' in guide
    assert 'document.getElementById("gpage-" + tab.dataset.gpage).hidden = !active;' in read_spa()


def test_the_eight_steps_are_the_path_and_their_order_is_the_order() -> None:
    """The scaffold is the settled half.  Each step is a heading with a stable
    id, so a "?" anywhere in the panel can point at a STEP and not only at a
    definition -- and the order is the order the work actually goes in: a run
    before a review, a review before an acceptance, and never the reverse."""

    page = _page()
    ids = re.findall(r'<h3 id="(g-bd-[a-z]+)"', page)
    assert ids == [f"g-bd-{step}" for step in BUILD_STEPS], ids

    english, russian = _language_tables()
    for step in BUILD_STEPS:
        for suffix in ("h", "p"):
            key = f"guide_bd_{step}_{suffix}"
            assert f'data-i18n="{key}"' in page, key
            for block in (english, russian):
                assert _value(block, key), key
    # Numbered in the text of the headings themselves, so a reader who arrives
    # in the middle by deep link knows where in the walk they landed.
    for number, step in enumerate(BUILD_STEPS, start=1):
        assert _value(english, f"guide_bd_{step}_h").startswith(f"{number}. ")
        assert _value(russian, f"guide_bd_{step}_h").startswith(f"{number}. ")

    # Every paragraph is owned by its marker and not also written into the
    # markup, exactly like the reference pages: two sentences for one string is
    # how the source and the screen come to disagree.
    written = re.findall(r'data-i18n="guide_bd_\w+"[^>]*>([^<]*)<', page)
    assert written and all(shown.strip() == "" for shown in written[1:]), written


def test_the_page_says_it_is_a_draft_before_it_says_anything_else() -> None:
    """The drafted half, marked.  The path this walks is being assembled by the
    same wave that added the page, so its prose describes a route that does not
    run end to end yet -- and an unmarked sketch would be exactly the failure
    the reference pages were scored down for, one page further along."""

    page = _page()
    # First thing under the title, before a reader has spent any trust.
    assert page.index('data-i18n="guide_bd_draft"') < page.index('data-i18n="guide_bd_example"')
    assert '<p class="draftnote" data-i18n="guide_bd_draft">' in page

    english, russian = _language_tables()
    draft_en = _value(english, "guide_bd_draft")
    draft_ru = _value(russian, "guide_bd_draft")
    assert draft_en.startswith("Draft.")
    assert draft_ru.startswith("Черновик.")
    # It separates the settled half from the drafted one rather than calling the
    # whole page provisional: the steps are a claim and stay one.
    assert "steps below and their order are settled" in draft_en
    assert "шаги ниже и их порядок уже определены" in draft_ru.lower()
    # And it says what the marked gaps are, so an empty box is read as a
    # deliberate absence rather than as a picture that failed to load.
    assert "screenshots go" in draft_en
    assert "снимк" in draft_ru.lower()


def test_a_picture_that_has_not_been_taken_is_a_marked_gap_and_never_a_drawing() -> None:
    """A screenshot of this panel comes off a working panel.  Inventing one --
    as a drawing, as a description of what it would look like, or as any
    reference the asset would have to fetch -- would put a picture of a guess in
    front of a reader who came here to be shown the real thing."""

    page = _page()
    slots = re.findall(r'<p class="shot" data-i18n="(guide_bd_\w+_shot)">', page)
    # One per step that shows a screen; the closing step is a pointer onward to
    # the reference pages and has no screen of its own.
    assert slots == [f"guide_bd_{step}_shot" for step in BUILD_STEPS if step != "next"], slots

    # Nothing is fetched and nothing is drawn: no image, no inline figure, no
    # data URI smuggled in as one.
    for forbidden in ("<img", "<svg", "data:image", "background-image"):
        assert forbidden not in page, forbidden

    english, russian = _language_tables()
    for slot in slots:
        # Each one names the SCREEN it will show -- which is knowable, the
        # screen exists -- and stops there.
        assert _value(english, slot).startswith("Picture: "), slot
        assert _value(russian, slot).startswith("Снимок: "), slot

    # The gap is visibly a gap, in CSS, rather than an empty line the reader
    # scrolls past without noticing anything is missing.
    style = read_spa().split(".doc p.shot{", 1)[1].split("}", 1)[0]
    assert "dashed" in style


def test_the_walk_states_the_rules_the_panel_would_otherwise_teach_by_refusal() -> None:
    """The three things both blind testers got wrong, said in the walk rather
    than met as refusals: a finished run has not finished the task, a reviewer
    may not be the author, and only acceptance closes anything."""

    english, russian = _language_tables()
    for block in (english, russian):
        # Step 4: a run that ended well is not a task that is done.
        assert "6" in _value(block, "guide_bd_run_p")
    assert "has not finished the task" in _value(english, "guide_bd_run_p")
    assert "не закрывает задачу" in _value(russian, "guide_bd_run_p")
    # Step 6: independent means not the author, and acceptance enforces it.
    assert "not the author" in _value(english, "guide_bd_review_p")
    assert "не автор" in _value(russian, "guide_bd_review_p")
    # Step 7: the panel's own honesty rule, stated where it is first met.
    assert "Only acceptance" in _value(english, "guide_bd_accept_p")
    assert "только приёмка" in _value(russian, "guide_bd_accept_p").lower()
