from nanobot.providers.base import LLMProvider


def test_sanitize_empty_content_strips_internal_meta_from_image_blocks() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                    "_meta": {"path": "/tmp/demo.png"},
                },
                {"type": "text", "text": "describe image"},
            ],
        }
    ]

    sanitized = LLMProvider._sanitize_empty_content(messages)

    content = sanitized[0]["content"]
    assert isinstance(content, list)
    assert "_meta" not in content[0]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
