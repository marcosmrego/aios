"""Tests for NotionClient property parsers and block builders."""

import pytest
from tools.notion import NotionClient


@pytest.fixture
def client(mock_notion_client) -> NotionClient:
    return NotionClient()


def test_text_extracts_plain_text(client: NotionClient) -> None:
    prop = {"rich_text": [{"plain_text": "hello"}, {"plain_text": " world"}]}
    assert client._text(prop) == "hello world"


def test_text_returns_empty_for_none(client: NotionClient) -> None:
    assert client._text(None) == ""


def test_select_extracts_name(client: NotionClient) -> None:
    prop = {"select": {"name": "High"}}
    assert client._select(prop) == "High"


def test_select_returns_empty_for_none(client: NotionClient) -> None:
    assert client._select(None) == ""


def test_multi_select_extracts_names(client: NotionClient) -> None:
    prop = {"multi_select": [{"name": "frontend"}, {"name": "realtime"}]}
    assert client._multi_select(prop) == ["frontend", "realtime"]


def test_number_extracts_value(client: NotionClient) -> None:
    prop = {"number": 5}
    assert client._number(prop) == 5


def test_number_returns_none_for_none(client: NotionClient) -> None:
    assert client._number(None) is None


def test_heading_block_structure(client: NotionClient) -> None:
    block = client._heading("My Heading", level=2)
    assert block["type"] == "heading_2"
    assert block["heading_2"]["rich_text"][0]["text"]["content"] == "My Heading"


def test_paragraph_block_structure(client: NotionClient) -> None:
    block = client._paragraph("Some text")
    assert block["type"] == "paragraph"
    assert block["paragraph"]["rich_text"][0]["text"]["content"] == "Some text"


def test_code_block_structure(client: NotionClient) -> None:
    block = client._code_block("SELECT 1", language="sql")
    assert block["type"] == "code"
    assert block["code"]["language"] == "sql"
    assert block["code"]["rich_text"][0]["text"]["content"] == "SELECT 1"


def test_code_block_truncates_long_content(client: NotionClient) -> None:
    long_code = "x" * 3000
    block = client._code_block(long_code)
    assert len(block["code"]["rich_text"][0]["text"]["content"]) == 2000


def test_parse_backlog_item(client: NotionClient) -> None:
    page = {
        "id": "abc-123",
        "url": "https://notion.so/abc-123",
        "properties": {
            "Name": {"title": [{"plain_text": "Feature X"}]},
            "Description": {"rich_text": [{"plain_text": "Description here"}]},
            "Status": {"select": {"name": "Backlog"}},
            "Priority": {"select": {"name": "High"}},
            "Project": {"select": {"name": "Climate"}},
            "Effort": {"number": 3},
            "Tags": {"multi_select": [{"name": "backend"}]},
        },
    }
    result = client._parse_backlog_item(page)
    assert result["notion_id"] == "abc-123"
    assert result["title"] == "Feature X"
    assert result["priority"] == "High"
    assert result["effort_points"] == 3
    assert "backend" in result["tags"]
