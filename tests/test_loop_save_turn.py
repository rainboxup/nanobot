from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import Session


def _mk_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop._TOOL_RESULT_MAX_CHARS = AgentLoop._TOOL_RESULT_MAX_CHARS
    return loop


def test_save_turn_keeps_image_placeholder_with_path() -> None:
    loop = _mk_loop()
    session = Session(key="test:image-path")

    loop._save_turn(
        session,
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                        "_meta": {"path": "/media/feishu/demo.png"},
                    }
                ],
            }
        ],
        skip=0,
    )

    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image: /media/feishu/demo.png]"}
    ]


def test_save_turn_keeps_default_image_placeholder_without_path() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")

    loop._save_turn(
        session,
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ],
        skip=0,
    )

    assert session.messages[0]["content"] == [{"type": "text", "text": "[image]"}]
