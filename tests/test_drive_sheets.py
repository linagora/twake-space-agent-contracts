import datetime

from httpx import AsyncClient
from openpyxl import Workbook

from tests.documents import XLSX, excel_workbook, read_content, rezipped, workbook
from tests.fakes import FakeBoundary, text_file


def budget(book: Workbook) -> None:
    sheet = book.active
    assert sheet is not None
    sheet.title = "Budget"
    sheet.append(["Item", "Amount", "Paid on", "Done"])
    sheet.append(["Rent", 1200, datetime.date(2026, 10, 1), True])
    sheet.append(["Power", 85.5, datetime.datetime(2026, 10, 3, 14, 30), False])
    # A formula openpyxl gives no value, which only a spreadsheet application computes
    sheet.append(["Total", "=SUM(B2:B3)"])
    sheet["E2"] = "#N/A"
    data = book.create_sheet("Data")
    data.sheet_state = "hidden"
    data["B2"] = "Kept aside"
    book.create_sheet("Empty")


async def test_a_spreadsheet_comes_sheet_by_sheet_as_rows_of_values(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = workbook(budget)
    boundary.drive.add(text_file("budget", "Budget.xlsx", content=content, mime=XLSX))

    response = await read_content(client, "budget")

    assert response.status_code == 200, response.text
    # Each sheet under a heading with its number and name, each row a line of values parted by
    # tabs, from the first column, its dates as days and times, and never a formula
    assert response.json() == {
        "id": "budget",
        "size": len(content),
        "truncated": False,
        "untrusted": {
            "name": "Budget.xlsx",
            "mime": XLSX,
            "content": "# Sheet 1: Budget\n"
            "Item\tAmount\tPaid on\tDone\n"
            "Rent\t1200\t2026-10-01\tTRUE\t#N/A\n"
            "Power\t85.5\t2026-10-03 14:30\tFALSE\n"
            "Total\n"
            "\n"
            "# Sheet 2 (hidden): Data\n"
            "\tKept aside\n"
            "\n"
            "# Sheet 3: Empty",
        },
    }


async def test_the_values_excel_computed_come_in_place_of_their_formulas(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = excel_workbook(
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>1200</v></c></row>'
        '<row r="4"><c r="A4" t="s"><v>3</v></c><c r="B4"><f>SUM(B2:B3)</f><v>1285.5</v></c>'
        '<c r="D4" t="str"><f>A4&amp;"!"</f><v>Total!</v></c></row>'
        '<row r="5"><c r="A5" t="s"><v>4</v></c><c r="B5" t="s"><v>5</v></c>'
        '<c r="C5" s="1"><v>46296</v></c><c r="D5" s="2"><v>0.5</v></c></row>',
        [
            "<t>Item</t>",
            "<t>Amount</t>",
            # Rich text, in runs
            "<r><t>Re</t></r><r><rPr><b/></rPr><t>nt</t></r>",
            "<t>Total</t>",
            # The reading of a name in Japanese, set above it, which a reader does not see inline
            '<t>東京</t><rPh sb="0" eb="2"><t>トウキョウ</t></rPh>',
            # A line break, as Excel writes the carriage return it cannot write in XML
            "<t>Line one_x000D_\nline two</t>",
        ],
    )
    boundary.drive.add(text_file("totals", "Totals.xlsx", content=content, mime=XLSX))

    response = await read_content(client, "totals")

    assert response.status_code == 200, response.text
    # A cell on one line, and an empty cell between others kept as such, so that columns align
    assert response.json()["untrusted"]["content"] == (
        "# Sheet 1: Sheet1\n"
        "Item\tAmount\n"
        "Rent\t1200\n"
        "Total\t1285.5\t\tTotal!\n"
        "東京\tLine one line two\t2026-10-01\t12:00"
    )
    assert "SUM" not in response.text


async def test_dates_count_from_1904_when_the_workbook_says_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = excel_workbook('<row r="1"><c r="A1" s="1"><v>45000</v></c></row>', [], date1904=True)
    boundary.drive.add(text_file("mac", "Mac.xlsx", content=content, mime=XLSX))

    response = await read_content(client, "mac")

    assert response.json()["untrusted"]["content"] == "# Sheet 1: Sheet1\n2027-03-16"


def large(book: Workbook) -> None:
    sheet = book.active
    assert sheet is not None
    sheet.title = "Large"
    for _ in range(1_005):
        sheet.append([1] * 55)


async def test_only_the_first_rows_and_columns_of_a_sheet_come(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(text_file("large", "Large.xlsx", content=workbook(large), mime=XLSX))

    answer = (await read_content(client, "large", max_bytes=262_144)).json()

    lines = answer["untrusted"]["content"].split("\n")
    # Said under the sheet's heading, so that the text cut at max_bytes still says it
    assert lines[:2] == [
        "# Sheet 1: Large",
        "[Only its first 1000 rows that hold values, and its first 50 columns, are given.]",
    ]
    assert lines[2:] == ["\t".join(["1"] * 50)] * 1_000
    assert answer["truncated"] is True


async def test_a_damaged_spreadsheet_is_not_extractable(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = workbook(budget)
    boundary.drive.add(
        text_file("cut", "Cut.xlsx", content=content[: len(content) // 2], mime=XLSX)
    )

    response = await read_content(client, "cut")

    assert response.status_code == 415, response.text
    assert response.json()["code"] == "content_not_extractable"
    assert "Rent" not in response.text


async def test_a_workbook_that_lists_more_sheets_than_a_zip_holds_files_is_not_extractable(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Sheets that are nowhere, which only a crafted workbook lists, each the reader would remember
    sheets = "".join(f'<sheet name="S{number}" sheetId="{number}"/>' for number in range(10_001))
    listing = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheets>{sheets}</sheets></workbook>"
    ).encode()
    content = rezipped(excel_workbook("", []), {"xl/workbook.xml": listing})
    boundary.drive.add(text_file("crafted", "Crafted.xlsx", content=content, mime=XLSX))

    response = await read_content(client, "crafted")

    assert response.status_code == 415, response.text
    assert response.json()["code"] == "content_not_extractable"


async def test_only_the_first_65536_formats_of_a_workbook_are_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Formats 1 and 65,536 show days, those between nothing of their own: past the 64,000 formats
    # Excel holds, a workbook's formats are not read, nor remembered
    formats = '<xf numFmtId="0"/><xf numFmtId="14"/>' + "<xf/>" * 65_534 + '<xf numFmtId="14"/>'
    styles = (
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<cellXfs count="65537">{formats}</cellXfs></styleSheet>'
    ).encode()
    days = '<row r="1"><c r="A1" s="1"><v>46296</v></c><c r="B1" s="65536"><v>46296</v></c></row>'
    content = rezipped(excel_workbook(days, []), {"xl/styles.xml": styles})
    boundary.drive.add(text_file("formats", "Formats.xlsx", content=content, mime=XLSX))

    response = await read_content(client, "formats")

    assert response.json()["untrusted"]["content"] == "# Sheet 1: Sheet1\n2026-10-01\t46296"
