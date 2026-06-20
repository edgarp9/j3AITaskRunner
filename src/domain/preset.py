"""Pure preset-analysis parsing and prompt-building rules."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from json import JSONDecodeError, JSONDecoder
from typing import Any


class PresetAnalysisError(ValueError):
    """Raised when a preset analysis or generation response is invalid."""


class PresetResponseContractError(PresetAnalysisError):
    """Raised when a parsed preset response violates its required data contract."""


class PresetPromptCountMismatchError(PresetResponseContractError):
    """Raised when generated prompt count differs from selected candidate count."""


_REQUIRED_CANDIDATE_FIELDS = (
    "id",
    "title",
    "problem",
    "evidence",
    "priority",
    "risk",
    "impact",
)
_REQUIRED_GENERATED_PROMPT_FIELDS = (
    "candidate_id",
    "title",
    "prompt",
)
_PRIORITY_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
_CANDIDATES_PAYLOAD_PATTERN = re.compile(r"\{\{\s*candidates_payload\s*\}\}")


@dataclass(slots=True, frozen=True)
class PresetCandidate:
    """One analyzed work candidate returned from a preset analysis prompt."""

    id: str
    title: str
    problem: str
    evidence: str | tuple[str, ...]
    priority: str
    risk: str
    impact: str

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON payload shape expected by work prompt templates."""
        evidence_payload: str | list[str]
        if isinstance(self.evidence, tuple):
            evidence_payload = list(self.evidence)
        else:
            evidence_payload = self.evidence
        return {
            "id": self.id,
            "title": self.title,
            "problem": self.problem,
            "evidence": evidence_payload,
            "priority": self.priority,
            "risk": self.risk,
            "impact": self.impact,
        }


@dataclass(slots=True, frozen=True)
class GeneratedWorkPrompt:
    """One executable work prompt generated for an analyzed candidate."""

    candidate_id: str
    title: str
    prompt: str

    @property
    def id(self) -> str:
        """Return the matched analysis candidate id."""
        return self.candidate_id


def parse_json_object_from_text(text: str) -> dict[str, Any]:
    """Parse a JSON object from raw JSON, a fenced block, or the first object in text."""
    stripped = text.strip()
    if not stripped:
        raise PresetAnalysisError("응답 본문이 비어 있습니다.")

    direct = _load_json_object(stripped)
    if direct is not None:
        return direct

    fenced = _extract_fenced_code_block(stripped)
    if fenced is not None:
        parsed = _load_json_object(fenced)
        if parsed is not None:
            return parsed

    decoder = JSONDecoder()
    search_start = 0
    while True:
        index = stripped.find("{", search_start)
        if index == -1:
            break
        search_start = index + 1
        content_index = index + 1
        while content_index < len(stripped) and stripped[content_index] in " \t\r\n":
            content_index += 1
        if content_index >= len(stripped) or stripped[content_index] not in ('"', "}"):
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped, index)
        except JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise PresetAnalysisError("응답을 JSON 객체로 해석하지 못했습니다.")


def try_parse_json_object_from_text(text: str) -> dict[str, Any] | None:
    """Return a parsed JSON object or None when parsing fails."""
    try:
        return parse_json_object_from_text(text)
    except PresetAnalysisError:
        return None


def extract_candidates(response_text: str) -> list[PresetCandidate]:
    """Extract and validate the top-level candidates array from analysis text."""
    payload = parse_json_object_from_text(response_text)
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list):
        raise PresetResponseContractError("분석 응답에서 candidates 목록을 찾을 수 없습니다.")
    if not candidates_raw:
        return []

    candidates: list[PresetCandidate] = []
    seen_candidate_ids: set[str] = set()
    duplicate_candidate_ids: list[str] = []
    invalid_candidate_items: list[str] = []
    for index, item in enumerate(candidates_raw, start=1):
        if not isinstance(item, dict):
            invalid_candidate_items.append(f"#{index}: candidate 항목이 객체가 아닙니다")
            continue

        missing_fields = _find_missing_required_candidate_fields(item)
        if missing_fields:
            invalid_candidate_items.append(f"#{index}: {', '.join(missing_fields)}")
            continue

        candidate_id = _stringify(item.get("id")).strip()
        if candidate_id in seen_candidate_ids:
            duplicate_candidate_ids.append(candidate_id)
            continue

        seen_candidate_ids.add(candidate_id)
        candidates.append(
            PresetCandidate(
                id=candidate_id,
                title=_stringify(item.get("title")).strip(),
                problem=_stringify(item.get("problem")).strip(),
                evidence=_normalize_evidence(item.get("evidence")),
                priority=_normalize_priority(_stringify(item.get("priority")).strip()),
                risk=_stringify(item.get("risk")).strip(),
                impact=_stringify(item.get("impact")).strip(),
            )
        )

    if invalid_candidate_items:
        invalid_text = "; ".join(invalid_candidate_items)
        raise PresetResponseContractError(
            f"분석 응답의 candidate 필수 필드가 누락되었거나 비어 있습니다: {invalid_text}"
        )
    if duplicate_candidate_ids:
        duplicate_text = _format_unique_ids(duplicate_candidate_ids)
        raise PresetResponseContractError(
            f"분석 응답에 중복된 candidate id가 있습니다: {duplicate_text}"
        )
    if not candidates:
        raise PresetResponseContractError("분석 응답의 candidates 항목 형식이 올바르지 않습니다.")
    return candidates


def select_work_candidates(
    candidates: list[PresetCandidate],
    threshold: str,
) -> list[PresetCandidate]:
    """Filter candidates by a high/medium/low Work Priority threshold."""
    normalized_threshold = threshold.strip().lower()
    if normalized_threshold not in _PRIORITY_ORDER:
        valid_values = ", ".join(("high", "medium", "low"))
        raise PresetAnalysisError(f"우선순위는 {valid_values} 중 하나여야 합니다.")

    threshold_rank = _PRIORITY_ORDER[normalized_threshold]
    return [
        candidate
        for candidate in candidates
        if _PRIORITY_ORDER[candidate.priority] >= threshold_rank
    ]


def build_candidates_payload(candidates: list[PresetCandidate]) -> str:
    """Serialize candidates for the ``{{candidates_payload}}`` template slot."""
    return json.dumps(
        [candidate.to_payload() for candidate in candidates],
        ensure_ascii=False,
        indent=2,
    )


def render_work_prompt_template(
    template_text: str,
    candidates: list[PresetCandidate],
) -> str:
    """Render a work-generation prompt by replacing the candidates payload slot."""
    if not template_text.strip():
        raise PresetAnalysisError("작업 프롬프트 템플릿이 비어 있습니다.")
    if _CANDIDATES_PAYLOAD_PATTERN.search(template_text) is None:
        raise PresetAnalysisError(
            "작업 프롬프트 템플릿에 {{candidates_payload}} 자리표시자가 없습니다."
        )
    payload = build_candidates_payload(candidates)
    return _CANDIDATES_PAYLOAD_PATTERN.sub(lambda _match: payload, template_text)


def extract_generated_work_prompts(
    response_text: str,
    input_candidates: list[PresetCandidate],
) -> list[GeneratedWorkPrompt]:
    """Extract generated prompts and return them in input candidate order."""
    if not input_candidates:
        raise PresetAnalysisError("작업 후보에 대응하는 프롬프트를 만들지 못했습니다.")

    payload = parse_json_object_from_text(response_text)
    prompts_raw = payload.get("prompts")
    if not isinstance(prompts_raw, list):
        if isinstance(payload.get("candidates"), list):
            raise PresetResponseContractError(
                "작업 프롬프트 응답이 분석 응답 candidates 형식입니다. "
                "최상위 prompts 목록을 반환해야 합니다."
            )
        raise PresetResponseContractError(
            "작업 프롬프트 응답에서 prompts 목록을 찾을 수 없습니다."
        )

    input_candidate_ids = [candidate.id for candidate in input_candidates]
    duplicate_input_candidate_ids = _find_duplicate_ids(input_candidate_ids)
    if duplicate_input_candidate_ids:
        duplicate_text = _format_unique_ids(duplicate_input_candidate_ids)
        raise PresetResponseContractError(
            f"작업 후보에 중복된 candidate id가 있습니다: {duplicate_text}"
        )
    if len(prompts_raw) != len(input_candidate_ids):
        raise PresetResponseContractError(
            "작업 프롬프트 응답의 prompts 개수가 candidates 개수와 다릅니다: "
            f"candidates={len(input_candidate_ids)} prompts={len(prompts_raw)}"
        )

    expected_prompt_count = len(input_candidates)
    actual_prompt_count = len(prompts_raw)
    if actual_prompt_count != expected_prompt_count:
        raise PresetPromptCountMismatchError(
            "작업 프롬프트 개수가 선택된 분석 후보 개수와 다릅니다. "
            f"선택된 분석 후보 {expected_prompt_count}건, "
            f"생성 프롬프트 {actual_prompt_count}건입니다. "
            "후보 작업 등록을 중단합니다."
        )

    input_candidate_id_set = set(input_candidate_ids)
    prompt_by_candidate_id: dict[str, GeneratedWorkPrompt] = {}
    duplicate_prompt_candidate_ids: list[str] = []
    unknown_prompt_candidate_ids: list[str] = []
    invalid_prompt_items: list[str] = []
    for index, item in enumerate(prompts_raw, start=1):
        if not isinstance(item, dict):
            invalid_prompt_items.append(f"#{index}: prompt 항목이 객체가 아닙니다")
            continue

        missing_fields = _find_missing_required_generated_prompt_fields(item)
        if missing_fields:
            invalid_prompt_items.append(f"#{index}: {', '.join(missing_fields)}")
            continue

        candidate_id = _stringify(item.get("candidate_id")).strip()
        title = _stringify(item.get("title")).strip()
        prompt_text = _stringify(item.get("prompt")).strip()
        if candidate_id not in input_candidate_id_set:
            unknown_prompt_candidate_ids.append(candidate_id)
            continue
        if candidate_id in prompt_by_candidate_id:
            duplicate_prompt_candidate_ids.append(candidate_id)
            continue

        prompt_by_candidate_id[candidate_id] = GeneratedWorkPrompt(
            candidate_id=candidate_id,
            title=title,
            prompt=prompt_text,
        )

    if invalid_prompt_items:
        invalid_text = "; ".join(invalid_prompt_items)
        raise PresetResponseContractError(
            "작업 프롬프트 응답의 prompt 필수 필드가 "
            f"누락되었거나 비어 있습니다: {invalid_text}"
        )
    if unknown_prompt_candidate_ids:
        unknown_text = _format_unique_ids(unknown_prompt_candidate_ids)
        raise PresetResponseContractError(
            f"작업 프롬프트 응답에 알 수 없는 candidate_id가 있습니다: {unknown_text}"
        )
    if duplicate_prompt_candidate_ids:
        duplicate_text = _format_unique_ids(duplicate_prompt_candidate_ids)
        raise PresetResponseContractError(
            f"작업 프롬프트 응답에 중복된 candidate_id가 있습니다: {duplicate_text}"
        )

    missing_candidate_ids = [
        candidate.id
        for candidate in input_candidates
        if candidate.id not in prompt_by_candidate_id
    ]
    if missing_candidate_ids:
        missing_text = ", ".join(missing_candidate_ids)
        raise PresetResponseContractError(
            f"작업 프롬프트 응답에 누락된 candidate_id가 있습니다: {missing_text}"
        )

    return [prompt_by_candidate_id[candidate.id] for candidate in input_candidates]


def _load_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _extract_fenced_code_block(text: str) -> str | None:
    fence_start = text.find("```")
    if fence_start == -1:
        return None

    content_start = text.find("\n", fence_start)
    if content_start == -1:
        return None

    fence_end = text.find("```", content_start + 1)
    if fence_end == -1:
        return None
    return text[content_start + 1 : fence_end].strip()


def _find_missing_required_candidate_fields(item: dict[str, Any]) -> list[str]:
    missing_fields: list[str] = []
    for field in _REQUIRED_CANDIDATE_FIELDS:
        if field not in item:
            missing_fields.append(field)
            continue
        if field == "evidence":
            if not _has_required_evidence(item.get(field)):
                missing_fields.append(field)
            continue
        if not _stringify(item.get(field)).strip():
            missing_fields.append(field)
    return missing_fields


def _find_missing_required_generated_prompt_fields(item: dict[str, Any]) -> list[str]:
    missing_fields: list[str] = []
    for field in _REQUIRED_GENERATED_PROMPT_FIELDS:
        if field not in item:
            missing_fields.append(field)
            continue
        if not _stringify(item.get(field)).strip():
            missing_fields.append(field)
    return missing_fields


def _has_required_evidence(value: Any) -> bool:
    normalized = _normalize_evidence(value)
    if isinstance(normalized, tuple):
        return bool(normalized)
    return bool(normalized.strip())


def _normalize_priority(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in _PRIORITY_ORDER:
        return normalized
    valid_values = ", ".join(("high", "medium", "low"))
    raise PresetResponseContractError(
        f"분석 응답 candidate priority는 {valid_values} 중 하나여야 합니다."
    )


def _normalize_evidence(value: Any) -> str | tuple[str, ...]:
    if isinstance(value, list):
        normalized_items = tuple(
            item for item in (_stringify(raw_item).strip() for raw_item in value) if item
        )
        return normalized_items
    return _stringify(value).strip()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _find_duplicate_ids(ids: list[str]) -> list[str]:
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    for item_id in ids:
        if item_id in seen_ids:
            duplicate_ids.append(item_id)
            continue
        seen_ids.add(item_id)
    return duplicate_ids


def _format_unique_ids(ids: list[str]) -> str:
    return ", ".join(dict.fromkeys(ids))
