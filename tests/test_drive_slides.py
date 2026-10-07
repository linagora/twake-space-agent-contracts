from httpx import AsyncClient
from pptx.presentation import Presentation
from pptx.util import Inches

from tests.documents import PPTX, presentation, read_content
from tests.fakes import FakeBoundary, text_file

# The layouts of python-pptx's default template, as PowerPoint numbers them
TITLE_SLIDE, TITLE_AND_CONTENT, TITLE_ONLY = 0, 1, 5


def quarterly_review(deck: Presentation) -> None:
    opening = deck.slides.add_slide(deck.slide_layouts[TITLE_SLIDE])
    assert opening.shapes.title is not None
    opening.shapes.title.text = "Quarterly review"
    opening.placeholders[1].text = "Sales team"
    roadmap = deck.slides.add_slide(deck.slide_layouts[TITLE_AND_CONTENT])
    assert roadmap.shapes.title is not None
    roadmap.shapes.title.text = "Roadmap"
    roadmap.placeholders[1].text = "Q1: launch\nQ2: scale"
    roadmap.notes_slide.notes_text_frame.text = "Talk about hiring.\nThen the budget."
    numbers = deck.slides.add_slide(deck.slide_layouts[TITLE_ONLY])
    assert numbers.shapes.title is not None
    numbers.shapes.title.text = "Numbers"
    table = numbers.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    for row, cells in enumerate([("Region", "Revenue"), ("North", "1200")]):
        for column, text in enumerate(cells):
            table.cell(row, column).text = text
    box = numbers.shapes.add_textbox(Inches(1), Inches(4), Inches(3), Inches(1))
    box.text_frame.text = "Up 12% on last year"
    draft = deck.slides.add_slide(deck.slide_layouts[TITLE_ONLY])
    assert draft.shapes.title is not None
    draft.shapes.title.text = "Draft"
    # Hidden from the slide show, as PowerPoint marks it
    draft._element.set("show", "0")
    deck.slides.add_slide(deck.slide_layouts[TITLE_ONLY])


async def test_a_presentation_comes_slide_by_slide_with_its_notes(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = presentation(quarterly_review)
    boundary.drive.add(text_file("review", "Review.pptx", content=content, mime=PPTX))

    response = await read_content(client, "review")

    assert response.status_code == 200, response.text
    # Each slide under a heading with its number and title, its notes under a heading of their
    # own, and a slide without any text by its heading alone
    assert response.json() == {
        "id": "review",
        "size": len(content),
        "truncated": False,
        "untrusted": {
            "name": "Review.pptx",
            "mime": PPTX,
            "content": "# Slide 1: Quarterly review\n"
            "Sales team\n"
            "\n"
            "# Slide 2: Roadmap\n"
            "Q1: launch\n"
            "Q2: scale\n"
            "## Notes\n"
            "Talk about hiring.\n"
            "Then the budget.\n"
            "\n"
            "# Slide 3: Numbers\n"
            "Region\tRevenue\n"
            "North\t1200\n"
            "Up 12% on last year\n"
            "\n"
            "# Slide 4 (hidden): Draft\n"
            "\n"
            "# Slide 5",
        },
    }


async def test_a_damaged_presentation_is_not_extractable(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = presentation(quarterly_review)
    boundary.drive.add(
        text_file("cut", "Cut.pptx", content=content[: len(content) // 2], mime=PPTX)
    )

    response = await read_content(client, "cut")

    assert response.status_code == 415, response.text
    assert response.json()["code"] == "content_not_extractable"
    assert "Roadmap" not in response.text
