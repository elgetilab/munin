"""
Standalone unit test for vision.build_tool_result_followup (§5).

This is the deterministic counterpart to the flaky "does the model
actually use the image" behavioural stress tests: it verifies the pure
function that converts run_python tool_results into a synthetic user
message with image_url content blocks.

Runs in-process inside the retrieval container (no live service
needed) via:

    docker exec munin-retrieval python /app/tests/test_vision_synthesis.py

Exit code 0 = pass, non-zero = fail.

The sandbox HTTP dependency is stubbed via httpx.MockTransport so
the test does not rely on the sidecar being up. If anything in
build_tool_result_followup changes its message shape or data-URL
prefix, this test fails loudly.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import traceback

import httpx

# Retrieval code lives at /app inside the container.
sys.path.insert(0, "/app")

import vision  # noqa: E402


# Minimal valid 1x1 red PNG (67 bytes). Hex-dumped from a PIL-generated
# image so we don't need Pillow at test time.
_RED_1X1_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000"
    "00907753de0000000c4944415408996300010000050001020505bc78"
    "3d0000000049454e44ae426082"
)


def _make_mock_client() -> httpx.AsyncClient:
    """httpx.AsyncClient whose GET /artifacts/... returns a known PNG."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/artifacts/" in request.url.path:
            return httpx.Response(
                200,
                content=_RED_1X1_PNG,
                headers={"content-type": "image/png"},
            )
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _case_happy_path() -> None:
    """A run_python result with one image artifact should synthesise a
    multimodal user follow-up with the expected shape."""
    tool_results = [
        {
            "name": "run_python",
            "result": {
                "artifacts": [
                    {
                        "id": "abc123",
                        "filename": "plot.png",
                        "content_type": "image/png",
                        "size_bytes": len(_RED_1X1_PNG),
                    }
                ],
            },
        }
    ]
    async with _make_mock_client() as client:
        followup = await vision.build_tool_result_followup(
            tool_results=tool_results,
            conversation_id="conv-xyz",
            client=client,
        )
    assert followup is not None, "followup should not be None for image results"
    assert followup["role"] == "user", f"role should be user, got {followup['role']!r}"
    content = followup["content"]
    assert isinstance(content, list), f"content should be a list, got {type(content)}"
    types = [b.get("type") for b in content if isinstance(b, dict)]
    assert "text" in types, f"expected a text block, got types {types}"
    assert "image_url" in types, f"expected an image_url block, got types {types}"
    image_block = next(
        b for b in content
        if isinstance(b, dict) and b.get("type") == "image_url"
    )
    url = image_block["image_url"]["url"]
    assert url.startswith("data:image/png;base64,"), (
        f"expected data:image/png prefix, got {url[:40]!r}"
    )
    # Round-trip the base64 back to bytes and compare.
    payload = url.split(",", 1)[1]
    decoded = base64.b64decode(payload)
    assert decoded == _RED_1X1_PNG, "round-tripped bytes do not match source"


async def _case_no_artifacts() -> None:
    """run_python with zero image artifacts should return None."""
    tool_results = [
        {"name": "run_python", "result": {"artifacts": []}},
    ]
    async with _make_mock_client() as client:
        followup = await vision.build_tool_result_followup(
            tool_results=tool_results,
            conversation_id="conv-xyz",
            client=client,
        )
    assert followup is None, f"expected None for no artifacts, got {followup}"


async def _case_non_run_python() -> None:
    """Results from other tools should be ignored even if they claim to have
    image artifacts - feedback loop is run_python only."""
    tool_results = [
        {
            "name": "web_search",
            "result": {
                "artifacts": [
                    {"id": "wrong", "content_type": "image/png", "size_bytes": 10},
                ],
            },
        }
    ]
    async with _make_mock_client() as client:
        followup = await vision.build_tool_result_followup(
            tool_results=tool_results,
            conversation_id="conv-xyz",
            client=client,
        )
    assert followup is None, (
        f"expected None for non-run_python tool, got {followup}"
    )


async def _case_non_image_artifact() -> None:
    """Non-image artifacts (like xlsx files) should NOT be injected into
    the feedback loop - only image/* content types qualify."""
    tool_results = [
        {
            "name": "run_python",
            "result": {
                "artifacts": [
                    {
                        "id": "sheet",
                        "filename": "data.xlsx",
                        "content_type": (
                            "application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet"
                        ),
                        "size_bytes": 5000,
                    }
                ],
            },
        }
    ]
    async with _make_mock_client() as client:
        followup = await vision.build_tool_result_followup(
            tool_results=tool_results,
            conversation_id="conv-xyz",
            client=client,
        )
    assert followup is None, (
        f"expected None for xlsx-only result, got {followup}"
    )


async def _case_data_url_helpers() -> None:
    """Round-trip parse_inline_data_url / _build_data_url to make sure the
    lower-level helpers agree on encoding. Guards the §5 request path."""
    url = vision._build_data_url(_RED_1X1_PNG, "png")
    assert url.startswith("data:image/png;base64,")
    bytes_back, subtype = vision.parse_inline_data_url(url)
    assert subtype == "png"
    assert bytes_back == _RED_1X1_PNG


async def main() -> int:
    cases = [
        ("happy_path", _case_happy_path),
        ("no_artifacts", _case_no_artifacts),
        ("non_run_python", _case_non_run_python),
        ("non_image_artifact", _case_non_image_artifact),
        ("data_url_helpers", _case_data_url_helpers),
    ]
    failures = []
    for name, fn in cases:
        try:
            await fn()
            print(f"  [PASS] {name}")
        except AssertionError as exc:
            print(f"  [FAIL] {name}: {exc}")
            failures.append(name)
        except Exception as exc:
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            failures.append(name)
    print()
    if failures:
        print(f"FAILED {len(failures)}/{len(cases)}: {failures}")
        return 1
    print(f"PASSED {len(cases)}/{len(cases)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
