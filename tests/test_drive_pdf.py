from httpx import AsyncClient

from tests.documents import PDF, encrypted_pdf, pdf, read_content
from tests.fakes import FakeBoundary, text_file


async def test_a_pdf_comes_page_by_page(client: AsyncClient, boundary: FakeBoundary) -> None:
    content = pdf("Annual report\nRevenue grew by 12%.", None, "Outlook\nStable (for now).")
    boundary.drive.add(text_file("report", "Report.pdf", content=content, mime=PDF))

    response = await read_content(client, "report")

    assert response.status_code == 200, response.text
    # Each page under a heading with its number, a page without text by its heading alone
    assert response.json() == {
        "id": "report",
        "size": len(content),
        "truncated": False,
        "untrusted": {
            "name": "Report.pdf",
            "mime": PDF,
            "content": "# Page 1\n"
            "Annual report\n"
            "Revenue grew by 12%.\n"
            "\n"
            "# Page 2\n"
            "\n"
            "# Page 3\n"
            "Outlook\n"
            "Stable (for now).",
        },
    }


async def test_a_pdf_without_text_says_so(client: AsyncClient, boundary: FakeBoundary) -> None:
    # Pages that only show images, as a scan's do
    boundary.drive.add(text_file("scan", "Scan.pdf", content=pdf(None, None), mime=PDF))

    response = await read_content(client, "scan")

    assert response.status_code == 415, response.text
    assert response.json()["code"] == "content_not_extractable"
    assert "no text" in response.json()["detail"]


async def test_a_pdf_protected_by_a_password_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    locked = encrypted_pdf(pdf("Plans"), user_password="secret", owner_password="secret")
    boundary.drive.add(text_file("locked", "Locked.pdf", content=locked, mime=PDF))

    response = await read_content(client, "locked")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "file_encrypted"
    assert "password" in response.json()["detail"]


async def test_a_pdf_that_opens_without_a_password_is_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Its owner's password only limits what readers may do, such as print it
    limited = encrypted_pdf(pdf("Plans"), user_password="", owner_password="owner")
    boundary.drive.add(text_file("limited", "Limited.pdf", content=limited, mime=PDF))

    response = await read_content(client, "limited")

    assert response.status_code == 200, response.text
    assert response.json()["untrusted"]["content"] == "# Page 1\nPlans"


async def test_only_the_first_pages_of_a_long_pdf_come(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = pdf(*(f"Text of page {number}" for number in range(1, 206)))
    boundary.drive.add(text_file("long", "Long.pdf", content=content, mime=PDF))

    answer = (await read_content(client, "long")).json()

    text = answer["untrusted"]["content"]
    # Said first, so that a text cut at max_bytes still says it
    assert text.startswith("[Only its first 200 pages, of 205, are given.]\n\n# Page 1\n")
    assert text.endswith("# Page 200\nText of page 200")
    assert answer["truncated"] is True


async def test_a_damaged_pdf_is_not_extractable(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = pdf("Confidential plans")
    boundary.drive.add(
        text_file("cut", "Cut.pdf", content=content[: len(content) // 3], mime=PDF),
        text_file("other", "Other.pdf", content=b"%PDF-1.7 Confidential plans", mime=PDF),
    )

    for file_id in ("cut", "other"):
        response = await read_content(client, file_id)

        assert response.status_code == 415, response.text
        assert response.json()["code"] == "content_not_extractable"
        assert "Confidential" not in response.text
