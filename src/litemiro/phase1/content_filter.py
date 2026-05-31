"""provider content-filter 차단 식별 — phase1·api 공용.

``phase1`` 의 EntityExtractor 가 fallback 트리거를 위해 이 판별을 필요로 하는데,
원래는 ``api.ontology_store`` 에만 있어 ``phase1 → api`` 역의존이 생긴다. 중립
위치로 빼고 ``ontology_store`` 가 re-export 해 공개 위치/시그니처를 유지한다.
"""

from __future__ import annotations


def is_content_filter_error(exc: BaseException) -> bool:
    """provider content moderation 차단 여부를 메시지 substring 으로 식별.

    LiteLLM 이 OpenRouter 의 raw provider 에러를 그대로 wrapping 해 던지므로
    구조적 분류가 불가능 — 문자열 매칭으로 ``data_inspection_failed`` (Qwen /
    Alibaba) 와 OpenAI 류 ``content_policy_violation`` 을 동시에 잡는다. 다른
    provider 의 식별자가 늘어나면 여기서 같이 추가.
    """
    text = str(exc).lower()
    return (
        "data_inspection_failed" in text
        or "inappropriate content" in text
        or "content_policy_violation" in text
    )


__all__ = ["is_content_filter_error"]
