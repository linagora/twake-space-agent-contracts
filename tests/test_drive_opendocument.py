from httpx import AsyncClient

from tests.documents import ODP, ODS, ODT, opendocument, read_content
from tests.fakes import FakeBoundary, text_file

REPORT = (
    # The text a tracked change deleted, kept apart from the text
    "<text:tracked-changes><text:changed-region text:id='ct1'><text:deletion>"
    "<office:change-info><dc:creator>Alice</dc:creator></office:change-info>"
    "<text:p>Removed words</text:p></text:deletion></text:changed-region></text:tracked-changes>"
    "<text:p text:style-name='P1'>Quarterly report</text:p>"
    "<text:h text:outline-level='1'>Results</text:h>"
    # A footnote and a comment, which sit in the text they are about
    "<text:p>Revenue grew by <text:span text:style-name='T1'>12%</text:span>."
    "<text:note text:id='n1' text:note-class='footnote'><text:note-citation>1</text:note-citation>"
    "<text:note-body><text:p>Before taxes.</text:p></text:note-body></text:note>"
    "<office:annotation><dc:creator>Bob</dc:creator><text:p>Check this</text:p></office:annotation>"
    "<text:change text:change-id='ct1'/></text:p>"
    # Spaces, tabs and line breaks as elements, where the text's own blanks collapse
    "<text:p>Name<text:tab/>Value<text:s text:c='3'/>here<text:line-break/>next\n    line</text:p>"
    "<text:list><text:list-item><text:p>New customers</text:p>"
    "<text:list><text:list-item><text:p>In the north</text:p></text:list-item></text:list>"
    "</text:list-item></text:list>"
    "<table:table table:name='Table1'><table:table-column table:number-columns-repeated='2'/>"
    "<table:table-row><table:table-cell office:value-type='string'><text:p>Region</text:p>"
    "</table:table-cell><table:table-cell office:value-type='string'><text:p>Revenue</text:p>"
    "</table:table-cell></table:table-row><table:table-row><table:table-cell><text:p>North</text:p>"
    "</table:table-cell><table:table-cell><text:p>1200</text:p></table:table-cell>"
    "</table:table-row></table:table>"
    "<text:h text:outline-level='2'>Outlook</text:h>"
    "<text:p>Stable.</text:p>"
)
# The paragraph style P1 of the title, as LibreOffice derives it from Title
TITLE_STYLE = (
    "<style:style style:name='P1' style:family='paragraph' style:parent-style-name='Title'/>"
)


async def test_an_opendocument_text_comes_as_headings_paragraphs_and_tables(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = opendocument(ODT, REPORT, styles=TITLE_STYLE)
    boundary.drive.add(text_file("report", "Report.odt", content=content, mime=ODT))

    response = await read_content(client, "report")

    assert response.status_code == 200, response.text
    # As a Word document comes, without what a reader does not see in place: deleted text,
    # footnotes and comments
    assert response.json() == {
        "id": "report",
        "size": len(content),
        "truncated": False,
        "untrusted": {
            "name": "Report.odt",
            "mime": ODT,
            "content": "# Quarterly report\n"
            "\n"
            "# Results\n"
            "Revenue grew by 12%.\n"
            "Name\tValue   here\n"
            "next line\n"
            "- New customers\n"
            "  - In the north\n"
            "\n"
            "Region\tRevenue\n"
            "North\t1200\n"
            "\n"
            "## Outlook\n"
            "Stable.",
        },
    }


BUDGET = (
    "<table:table table:name='Budget' table:style-name='ta1'>"
    "<table:table-column table:number-columns-repeated='16384'/>"
    "<table:table-row><table:table-cell office:value-type='string'><text:p>Item</text:p>"
    "</table:table-cell><table:table-cell office:value-type='string'><text:p>Amount</text:p>"
    "</table:table-cell><table:table-cell office:value-type='string'><text:p>Paid on</text:p>"
    "</table:table-cell><table:table-cell office:value-type='string'><text:p>Done</text:p>"
    "</table:table-cell><table:table-cell table:number-columns-repeated='16380'/></table:table-row>"
    # What a cell shows depends on the language of whoever saved it: its value does not
    "<table:table-row><table:table-cell office:value-type='string'><text:p>Rent</text:p>"
    "</table:table-cell><table:table-cell office:value-type='float' office:value='1200'>"
    "<text:p>1 200</text:p></table:table-cell><table:table-cell office:value-type='date' "
    "office:date-value='2026-10-01'><text:p>01/10/2026</text:p></table:table-cell>"
    "<table:table-cell table:formula='of:=TRUE()' office:value-type='boolean' "
    "office:boolean-value='true'><text:p>VRAI</text:p></table:table-cell>"
    "<table:table-cell table:formula='of:=#N/A' office:value-type='string' office:string-value=''"
    " calcext:value-type='error'><text:p>#N/A</text:p></table:table-cell></table:table-row>"
    "<table:table-row><table:table-cell office:value-type='string'><text:p>Power</text:p>"
    "<office:annotation><dc:creator>Bob</dc:creator><text:p>Too much</text:p></office:annotation>"
    "</table:table-cell><table:table-cell office:value-type='currency' office:currency='EUR' "
    "office:value='85.5'><text:p>85,50 €</text:p></table:table-cell><table:table-cell "
    "office:value-type='date' office:date-value='2026-10-03T14:30:00'>"
    "<text:p>03/10/2026 14:30</text:p></table:table-cell>"
    "<table:table-cell office:value-type='boolean' office:boolean-value='false'>"
    "<text:p>FAUX</text:p></table:table-cell><table:table-cell office:value-type='time' "
    "office:time-value='PT01H30M00S'><text:p>01:30:00</text:p></table:table-cell></table:table-row>"
    # A formula's value, and never the formula
    "<table:table-row><table:table-cell office:value-type='string'><text:p>Total</text:p>"
    "</table:table-cell><table:table-cell table:formula='of:=SUM([.B2:.B3])' "
    "office:value-type='float' office:value='1285.5'><text:p>1 285,5</text:p></table:table-cell>"
    "</table:table-row>"
    # Empty rows, which a sheet repeats to its end, and cells repeated or covered by a merged one
    "<table:table-row table:number-rows-repeated='3'><table:table-cell "
    "table:number-columns-repeated='16384'/></table:table-row>"
    "<table:table-row><table:table-cell office:value-type='string' "
    "table:number-columns-repeated='3'><text:p>x</text:p></table:table-cell>"
    "<table:covered-table-cell/><table:table-cell office:value-type='percentage' "
    "office:value='0.25'><text:p>25 %</text:p></table:table-cell></table:table-row>"
    "<table:table-row table:number-rows-repeated='1048570'><table:table-cell "
    "table:number-columns-repeated='16384'/></table:table-row>"
    "</table:table>"
    "<table:table table:name='Data' table:style-name='ta2'><table:table-row>"
    "<table:table-cell office:value-type='string'><text:p>Kept aside</text:p></table:table-cell>"
    "</table:table-row></table:table>"
)
# The style of a sheet hidden from its tabs, as LibreOffice writes it
SHEET_STYLES = (
    "<style:style style:name='ta1' style:family='table'>"
    "<style:table-properties table:display='true'/></style:style>"
    "<style:style style:name='ta2' style:family='table'>"
    "<style:table-properties table:display='false'/></style:style>"
)


async def test_an_opendocument_spreadsheet_comes_sheet_by_sheet_as_rows_of_values(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = opendocument(ODS, BUDGET, styles=SHEET_STYLES)
    boundary.drive.add(text_file("budget", "Budget.ods", content=content, mime=ODS))

    response = await read_content(client, "budget")

    assert response.status_code == 200, response.text
    # As an Excel spreadsheet comes: the values, whatever language showed them, and never the
    # formulas, nor what is said of the cells
    assert response.json()["untrusted"]["content"] == (
        "# Sheet 1: Budget\n"
        "Item\tAmount\tPaid on\tDone\n"
        "Rent\t1200\t2026-10-01\tTRUE\t#N/A\n"
        "Power\t85.5\t2026-10-03 14:30\tFALSE\t01:30\n"
        "Total\t1285.5\n"
        "x\tx\tx\t\t0.25\n"
        "\n"
        "# Sheet 2 (hidden): Data\n"
        "Kept aside"
    )
    assert "SUM" not in response.text


async def test_only_the_first_rows_and_columns_of_a_sheet_come_however_repeated(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A row of values repeated as many times as a sheet holds rows, each value as many times as it
    # holds columns: a few bytes, which the service never unfolds beyond what it reads
    content = opendocument(
        ODS,
        "<table:table table:name='Ones'><table:table-row table:number-rows-repeated='1048576'>"
        "<table:table-cell office:value-type='float' office:value='1' "
        "table:number-columns-repeated='16384'><text:p>1</text:p></table:table-cell>"
        "</table:table-row></table:table>",
    )
    boundary.drive.add(text_file("ones", "Ones.ods", content=content, mime=ODS))

    answer = (await read_content(client, "ones", max_bytes=262_144)).json()

    lines = answer["untrusted"]["content"].split("\n")
    assert lines[:2] == [
        "# Sheet 1: Ones",
        "[Only its first 1000 rows that hold values, and its first 50 columns, are given.]",
    ]
    assert lines[2:] == ["\t".join(["1"] * 50)] * 1_000
    assert answer["truncated"] is True


DECK = (
    "<draw:page draw:name='Roadmap' draw:style-name='dp1'>"
    "<draw:frame presentation:class='title'><draw:text-box><text:p>Roadmap</text:p>"
    "</draw:text-box></draw:frame>"
    "<draw:frame presentation:class='outline'><draw:text-box><text:list><text:list-item>"
    "<text:p>Q1: launch</text:p></text:list-item><text:list-item><text:p>Q2: scale</text:p>"
    "</text:list-item></text:list></draw:text-box></draw:frame>"
    # What the master page fills in, such as the slide's number
    "<draw:frame presentation:class='page-number'><draw:text-box><text:p>"
    "<text:page-number>2</text:page-number></text:p></draw:text-box></draw:frame>"
    "<presentation:notes><draw:page-thumbnail presentation:class='page'/>"
    "<draw:frame presentation:class='notes'><draw:text-box><text:p>Talk about hiring.</text:p>"
    "</draw:text-box></draw:frame></presentation:notes></draw:page>"
    "<draw:page draw:name='Numbers' draw:style-name='dp1'>"
    "<draw:frame presentation:class='title'><draw:text-box><text:p>Numbers</text:p>"
    "</draw:text-box></draw:frame>"
    "<draw:frame><table:table><table:table-row><table:table-cell><text:p>Region</text:p>"
    "</table:table-cell><table:table-cell><text:p>Revenue</text:p></table:table-cell>"
    "</table:table-row><table:table-row><table:table-cell><text:p>North</text:p>"
    "</table:table-cell><table:table-cell><text:p>1200</text:p></table:table-cell>"
    "</table:table-row></table:table></draw:frame>"
    # A shape with its text, as LibreOffice writes a text box of PowerPoint's
    "<draw:custom-shape><text:p>Up 12% on last year</text:p>"
    "<draw:enhanced-geometry draw:type='rectangle'/></draw:custom-shape></draw:page>"
    "<draw:page draw:name='Draft' draw:style-name='dp2'>"
    "<draw:frame presentation:class='title'><draw:text-box><text:p>Draft</text:p>"
    "</draw:text-box></draw:frame></draw:page>"
    "<draw:page draw:name='page4' draw:style-name='dp1'/>"
)
# The style of a slide hidden from the slide show, as LibreOffice writes it
SLIDE_STYLES = (
    "<style:style style:name='dp1' style:family='drawing-page'>"
    "<style:drawing-page-properties presentation:display-page-number='true'/></style:style>"
    "<style:style style:name='dp2' style:family='drawing-page'>"
    "<style:drawing-page-properties presentation:visibility='hidden'/></style:style>"
)


async def test_an_opendocument_presentation_comes_slide_by_slide_with_its_notes(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = opendocument(ODP, DECK, styles=SLIDE_STYLES)
    boundary.drive.add(text_file("deck", "Deck.odp", content=content, mime=ODP))

    response = await read_content(client, "deck")

    assert response.status_code == 200, response.text
    # As a PowerPoint presentation comes
    assert response.json()["untrusted"]["content"] == (
        "# Slide 1: Roadmap\n"
        "Q1: launch\n"
        "Q2: scale\n"
        "## Notes\n"
        "Talk about hiring.\n"
        "\n"
        "# Slide 2: Numbers\n"
        "Region\tRevenue\n"
        "North\t1200\n"
        "Up 12% on last year\n"
        "\n"
        "# Slide 3 (hidden): Draft\n"
        "\n"
        "# Slide 4"
    )


async def test_an_opendocument_protected_by_a_password_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = opendocument(ODT, REPORT, encrypted=True)
    boundary.drive.add(text_file("locked", "Locked.odt", content=content, mime=ODT))

    response = await read_content(client, "locked")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "file_encrypted"
    assert "password" in response.json()["detail"]


async def test_a_damaged_opendocument_is_not_extractable(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    whole = opendocument(ODT, REPORT, styles=TITLE_STYLE)
    # Its content cut short, or not XML at all
    unclosed = opendocument(ODT, "<text:p>Confidential plans")
    boundary.drive.add(
        text_file("cut", "Cut.odt", content=whole[: len(whole) // 2], mime=ODT),
        text_file("unclosed", "Unclosed.odt", content=unclosed, mime=ODT),
    )

    for file_id in ("cut", "unclosed"):
        response = await read_content(client, file_id)

        assert response.status_code == 415, response.text
        assert response.json()["code"] == "content_not_extractable"
        assert "Confidential" not in response.text
