from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.feishu import FeishuChannel, _extract_post_content
from nanobot.config.schema import FeishuConfig


def test_extract_post_content_supports_post_wrapper_shape() -> None:
    payload = {
        "post": {
            "zh_cn": {
                "title": "日报",
                "content": [
                    [
                        {"tag": "text", "text": "完成"},
                        {"tag": "img", "image_key": "img_1"},
                    ]
                ],
            }
        }
    }

    text, image_keys = _extract_post_content(payload)

    assert text == "日报 完成"
    assert image_keys == ["img_1"]


def test_extract_post_content_keeps_direct_shape_behavior() -> None:
    payload = {
        "title": "Daily",
        "content": [
            [
                {"tag": "text", "text": "report"},
                {"tag": "img", "image_key": "img_a"},
                {"tag": "img", "image_key": "img_b"},
            ]
        ],
    }

    text, image_keys = _extract_post_content(payload)

    assert text == "Daily report"
    assert image_keys == ["img_a", "img_b"]


@pytest.mark.asyncio
async def test_feishu_send_uses_video_msg_type_for_mp4(tmp_path: Path) -> None:
    channel = FeishuChannel(FeishuConfig(), MessageBus())
    channel._client = object()
    channel._upload_file_sync = MagicMock(return_value="file-key")
    channel._send_message_sync = MagicMock(return_value=True)

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"video-bytes")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_demo",
            content="",
            media=[str(video_path)],
        )
    )

    assert channel._send_message_sync.call_args.args[2] == "video"
