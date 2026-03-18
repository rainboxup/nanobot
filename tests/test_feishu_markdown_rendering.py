from nanobot.channels.feishu import FeishuChannel


def test_split_headings_avoids_double_bold_wrapping() -> None:
    channel = FeishuChannel.__new__(FeishuChannel)

    elements = channel._split_headings("## **Already Bold**")

    assert elements == [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**Already Bold**",
            },
        }
    ]


def test_parse_md_table_strips_markdown_formatting_from_headers_and_cells() -> None:
    table_text = (
        "| **Name** | Value |\n"
        "| --- | --- |\n"
        "| **Alpha** | ~~Done~~ |\n"
    )

    table = FeishuChannel._parse_md_table(table_text)

    assert table is not None
    assert table["columns"][0]["display_name"] == "Name"
    assert table["rows"][0]["c0"] == "Alpha"
    assert table["rows"][0]["c1"] == "Done"
