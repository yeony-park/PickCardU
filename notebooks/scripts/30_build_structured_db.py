"""Build the experiment-only structured benefit database for notebook 30.

The source normalized JSON files are treated as immutable.  Every source fact is
preserved; conservative derived fields are only an aid for candidate selection.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


SOURCE_GLOB = "data/rag/runtime/working/run_f2d29820c6dca642/documents/*/upstage/normalized.json"
SCHEMA_VERSION = "structured-benefit-experiment-v3"

# Versioned, deliberately small aliases.  They are persisted in SQLite and the
# build summary so a result can be interpreted without reading this module.
TARGET_ALIASES: dict[str, list[str]] = {
    "여행": ["여행", "항공", "항공사", "호텔", "숙박", "공항", "라운지", "면세점"],
    "커피": ["커피", "카페", "스타벅스", "투썸", "이디야", "폴바셋", "커피빈"],
    "카페": ["커피", "카페", "스타벅스", "투썸", "이디야", "폴바셋", "커피빈"],
}

_MONEY_TOKEN = re.compile(r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>천|만)?\s*원")
_PERCENT_TOKEN = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*%")
_SPEND_PHRASE = re.compile(r"전월\s*(?:이용\s*)?(?:실적|이용금액|이용\s*실적)")


def _canonical(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _money_from_match(match: re.Match[str]) -> int | None:
    try:
        number = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        return None
    multiplier = {None: 1, "천": 1_000, "만": 10_000}[match.group("unit")]
    amount = number * multiplier
    if amount != amount.to_integral_value():
        return None
    return int(amount)


def _typed_decimals(typed: dict[str, Any], field: str, kind: str) -> list[Decimal]:
    values: list[Decimal] = []
    for entry in typed.get(field, []) or []:
        if not isinstance(entry, dict) or entry.get("kind") != kind:
            continue
        try:
            values.append(Decimal(str(entry["decimal"])))
        except (InvalidOperation, KeyError, TypeError):
            continue
    return values


def _derive_percent(fact: dict[str, Any]) -> tuple[float | None, str, str]:
    typed = _typed_decimals(fact.get("typed_normalization") or {}, "value", "ratio")
    raw_text = f"{fact.get('value', '')} {fact.get('unit', '')}"
    raw = [Decimal(match.group("number")) for match in _PERCENT_TOKEN.finditer(raw_text)]
    has_numeric_range = bool(re.search(r"\d\s*(?:~|∼|～|–|—|-)\s*\d", raw_text))
    has_bound = bool(re.search(r"최대|최소|이상|이하|초과|미만|까지", raw_text))
    has_mixed_money_percent = bool(_MONEY_TOKEN.search(raw_text) and _PERCENT_TOKEN.search(raw_text))
    if has_numeric_range or has_bound or has_mixed_money_percent:
        return None, "unknown", "raw benefit is a range, bound, or mixed money/percent expression"
    if len(typed) != 1:
        state = "unknown" if typed or fact.get("value") else "not_applicable"
        return None, state, "typed ratio count is not one"
    percent = typed[0] * Decimal(100)
    if len(raw) != 1 or raw[0] != percent:
        return None, "unknown", "typed ratio and raw percent do not uniquely agree"
    return float(percent), "known", "single typed ratio agrees with raw percent"


def _derive_spend_threshold(fact: dict[str, Any]) -> tuple[int | None, str | None, str, str]:
    condition = str(fact.get("condition") or "")
    phrases = list(_SPEND_PHRASE.finditer(condition))
    typed_krw = _typed_decimals(fact.get("typed_normalization") or {}, "condition", "KRW")
    if not phrases:
        state = "unknown" if typed_krw and condition else "not_applicable"
        return None, None, state, "no explicit previous-month spend phrase"

    connected: list[tuple[int, str]] = []
    for phrase in phrases:
        # Thresholds normally appear immediately after the phrase.  A limited
        # backward window handles table-derived text such as "30만원 이상 전월 실적".
        windows = [condition[phrase.end() : phrase.end() + 45], condition[max(0, phrase.start() - 30) : phrase.start()]]
        for window in windows:
            for match in _MONEY_TOKEN.finditer(window):
                amount = _money_from_match(match)
                if amount is None:
                    continue
                tail = window[match.end() : match.end() + 8]
                operator = "gte" if re.search(r"이상", tail) else "lte" if re.search(r"이하", tail) else "unknown"
                connected.append((amount, operator))

    distinct = sorted(set(connected))
    if len(distinct) != 1:
        return None, None, "unknown", "explicit spend phrase has zero or multiple connected thresholds"
    amount, operator = distinct[0]
    if operator != "gte":
        return None, operator, "unknown", "only an explicit minimum spend threshold is supported"
    if Decimal(amount) not in typed_krw:
        return None, operator, "unknown", "connected raw threshold does not agree with typed KRW"
    return amount, operator, "known", "single explicit previous-month threshold agrees with typed KRW"


def _derive_cap(fact: dict[str, Any]) -> tuple[int | None, str, str]:
    cap_text = str(fact.get("cap") or "")
    typed = _typed_decimals(fact.get("typed_normalization") or {}, "cap", "KRW")
    raw = [_money_from_match(match) for match in _MONEY_TOKEN.finditer(cap_text)]
    raw = [value for value in raw if value is not None]
    spend_ceiling = bool(
        re.search(
            r"이용\s*금액|이용액|결제\s*금액|결제액|승인\s*금액|매출\s*(?:금액|액|건당)",
            cap_text,
        )
    )
    if spend_ceiling:
        return None, "unknown", "cap text describes an eligible spend or transaction ceiling"
    if len(typed) != 1:
        state = "unknown" if typed or cap_text else "not_applicable"
        return None, state, "typed cap count is not one"
    if len(raw) != 1 or Decimal(raw[0]) != typed[0]:
        return None, "unknown", "typed cap and raw KRW do not uniquely agree"
    return int(typed[0]), "known", "single typed cap agrees with raw KRW"


def _derive_cap_period(fact: dict[str, Any]) -> tuple[str | None, str, str]:
    cap_text = str(fact.get("cap") or "")
    period_text = str(fact.get("period") or "")
    text = _canonical(f"{cap_text} {period_text}")
    month = any(
        token in text
        for token in ("매월", "월간", "월별", "당월", "해당월", "월한도", "월최대", "월통합")
    ) or bool(re.search(r"(?:^|[^가-힣])월\d", text))
    day = any(token in text for token in ("하루", "일일", "1일최대", "1일한도", "1일1회")) or bool(
        re.search(r"(?:^|[^가-힣])일(?:최대|한도|\d)", text)
    )
    if month and day:
        return None, "unknown", "both monthly and daily cap periods are explicit"
    if month:
        return "month", "known", "explicit monthly cap period"
    if day:
        return "day", "known", "explicit daily cap period"
    if cap_text or period_text:
        return None, "unknown", "cap period is not explicit"
    return None, "not_applicable", "cap and period are absent"


def _derive_action(fact: dict[str, Any]) -> tuple[str | None, str, str]:
    text = _canonical(f"{fact.get('action', '')} {fact.get('benefit_type', '')}")
    negative = ("제외" in text or "제공되지않" in text or "적용되지않" in text or "불가" in text)
    kinds = []
    if "할인" in text:
        kinds.append("discount")
    if any(token in text for token in ("적립", "포인트", "마일리지", "airmoney", "에어머니")):
        kinds.append("earn")
    if negative:
        return (kinds[0] if len(kinds) == 1 else None), "false", "action text is negated or excluded"
    if len(kinds) == 1:
        return kinds[0], "true", "single supported positive action"
    if len(kinds) > 1:
        return None, "unknown", "multiple action kinds"
    return None, "unknown", "unsupported or ambiguous action"


def _derive_fact(fact: dict[str, Any]) -> dict[str, Any]:
    percent, percent_state, percent_reason = _derive_percent(fact)
    spend, spend_operator, spend_state, spend_reason = _derive_spend_threshold(fact)
    cap, cap_state, cap_reason = _derive_cap(fact)
    cap_period, cap_period_state, cap_period_reason = _derive_cap_period(fact)
    action_kind, action_status, action_reason = _derive_action(fact)
    exceptions = str(fact.get("exceptions") or "")
    return {
        "target_normalized": _canonical(str(fact.get("target") or "")),
        "action_kind": action_kind,
        "action_status": action_status,
        "action_reason": action_reason,
        "percent": percent,
        "percent_state": percent_state,
        "percent_reason": percent_reason,
        "spend_threshold_krw": spend,
        "spend_operator": spend_operator,
        "spend_state": spend_state,
        "spend_reason": spend_reason,
        "cap_krw": cap,
        "cap_state": cap_state,
        "cap_reason": cap_reason,
        "cap_period": cap_period,
        "cap_period_state": cap_period_state,
        "cap_period_reason": cap_period_reason,
        "period_raw": str(fact.get("period") or ""),
        "has_exceptions": bool(exceptions.strip()),
        "exceptions_normalized": _canonical(exceptions),
    }


def _fact_id(provider: str, source_pdf_sha256: str, document_id: str, ordinal: int) -> str:
    fingerprint = f"{provider}|{source_pdf_sha256}|{document_id}|{ordinal}".encode("utf-8")
    return f"fact_{_sha256_bytes(fingerprint)[:24]}"


def _source_files(repository_root: Path) -> list[Path]:
    files = sorted(repository_root.glob(SOURCE_GLOB))
    if not files:
        raise FileNotFoundError(f"no normalized JSON files matched {SOURCE_GLOB}")
    return files


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            card_name TEXT,
            issuer_name TEXT,
            source_pdf_sha256 TEXT NOT NULL,
            normalized_json_sha256 TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            source_path_sha256 TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            ordinal INTEGER NOT NULL,
            target_raw TEXT NOT NULL,
            action_raw TEXT NOT NULL,
            benefit_type_raw TEXT NOT NULL,
            value_raw TEXT NOT NULL,
            unit_raw TEXT NOT NULL,
            condition_raw TEXT NOT NULL,
            cap_raw TEXT NOT NULL,
            period_raw TEXT NOT NULL,
            frequency_raw TEXT NOT NULL,
            exceptions_raw TEXT NOT NULL,
            action_kind TEXT,
            action_status TEXT NOT NULL,
            percent REAL,
            percent_state TEXT NOT NULL,
            spend_threshold_krw INTEGER,
            spend_operator TEXT,
            spend_state TEXT NOT NULL,
            cap_krw INTEGER,
            cap_state TEXT NOT NULL,
            cap_period TEXT,
            raw_json TEXT NOT NULL,
            derived_json TEXT NOT NULL,
            UNIQUE(document_id, ordinal)
        );
        CREATE TABLE target_aliases (
            alias_group TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            PRIMARY KEY(alias_group, alias)
        );
        CREATE INDEX idx_facts_document ON facts(document_id);
        CREATE INDEX idx_facts_action ON facts(action_kind, action_status);
        CREATE INDEX idx_facts_percent ON facts(percent) WHERE percent_state = 'known';
        CREATE INDEX idx_facts_spend ON facts(spend_threshold_krw) WHERE spend_state = 'known';
        CREATE INDEX idx_facts_cap ON facts(cap_krw) WHERE cap_state = 'known';
        """
    )


def build_database(repository_root: Path, output_dir: Path) -> dict[str, Any]:
    """Build reproducible JSONL and SQLite artifacts from immutable Upstage JSON."""

    repository_root = repository_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_files = _source_files(repository_root)
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []

    for source_path in source_files:
        raw_bytes = source_path.read_bytes()
        source = json.loads(raw_bytes)
        document_id = str(source["document_id"])
        relative = source_path.relative_to(repository_root).as_posix()
        provider = str(source.get("provider") or "upstage")
        source_pdf_sha256 = str(source.get("source_pdf_sha256") or "")
        identity = source.get("identity") or {}
        source_facts = source.get("facts") or []
        document_row = {
            "document_id": document_id,
            "provider": provider,
            "identity": identity,
            "source_pdf_sha256": source_pdf_sha256,
            "normalized_json_sha256": _sha256_bytes(raw_bytes),
            "source_relative_path": relative,
            "source_path_sha256": _sha256_bytes(relative.encode("utf-8")),
            "structure_schema_version": source.get("structure_schema_version"),
            "normalization_contract": source.get("normalization_contract"),
            "provenance": source.get("provenance"),
            "ocr_provenance": source.get("ocr_provenance"),
            "ocr_parse_provenance": source.get("ocr_parse_provenance"),
            "pages": source.get("pages") or [],
            "page_count": len(source.get("pages") or []),
            "fact_count": len(source_facts),
        }
        documents.append(document_row)
        for ordinal, raw_fact in enumerate(source_facts):
            derived = _derive_fact(raw_fact)
            facts.append(
                {
                    "fact_id": _fact_id(provider, source_pdf_sha256, document_id, ordinal),
                    "document_id": document_id,
                    "ordinal": ordinal,
                    "provider": provider,
                    "source_pdf_sha256": source_pdf_sha256,
                    "source_relative_path": relative,
                    "source_path_sha256": document_row["source_path_sha256"],
                    "identity": identity,
                    "fields": {
                        key: raw_fact.get(key, "")
                        for key in (
                            "target", "action", "benefit_type", "value", "unit", "condition",
                            "cap", "period", "frequency", "exceptions"
                        )
                    },
                    "typed_normalization": raw_fact.get("typed_normalization") or {},
                    "field_evidence": raw_fact.get("field_evidence") or {},
                    "relation_scope": raw_fact.get("relation_scope") or {},
                    "derived": derived,
                    "raw_fact": raw_fact,
                }
            )

    if len({row["document_id"] for row in documents}) != len(documents):
        raise ValueError("duplicate document_id in source corpus")
    if len({row["fact_id"] for row in facts}) != len(facts):
        raise ValueError("fact_id collision")
    source_versions = {
        (
            row["provider"],
            row["structure_schema_version"],
            row["normalization_contract"],
            (row["provenance"] or {}).get("config_hash"),
        )
        for row in documents
    }
    if len(source_versions) != 1 or next(iter(source_versions))[0] != "upstage":
        raise ValueError(f"expected one Upstage normalized source version, found {sorted(source_versions, key=repr)}")

    documents_path = output_dir / "documents.jsonl"
    facts_path = output_dir / "facts.jsonl"
    database_path = output_dir / "structured.sqlite"
    summary_path = output_dir / "db_build_summary.json"
    _write_jsonl(documents_path, documents)
    _write_jsonl(facts_path, facts)

    # Replace only this experiment artifact.  Source and operational databases
    # are never opened for writing.
    if database_path.exists():
        database_path.unlink()
    with sqlite3.connect(database_path) as connection:
        _create_schema(connection)
        connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["document_id"], row["provider"], row["identity"].get("card_name"),
                    row["identity"].get("issuer_name"), row["source_pdf_sha256"],
                    row["normalized_json_sha256"], row["source_relative_path"],
                    row["source_path_sha256"], row["page_count"], row["fact_count"],
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                )
                for row in documents
            ],
        )
        connection.executemany(
            "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["fact_id"], row["document_id"], row["ordinal"], row["fields"]["target"],
                    row["fields"]["action"], row["fields"]["benefit_type"], row["fields"]["value"],
                    row["fields"]["unit"], row["fields"]["condition"], row["fields"]["cap"],
                    row["fields"]["period"], row["fields"]["frequency"], row["fields"]["exceptions"],
                    row["derived"]["action_kind"], row["derived"]["action_status"],
                    row["derived"]["percent"], row["derived"]["percent_state"],
                    row["derived"]["spend_threshold_krw"], row["derived"]["spend_operator"],
                    row["derived"]["spend_state"], row["derived"]["cap_krw"], row["derived"]["cap_state"],
                    row["derived"]["cap_period"],
                    json.dumps(row["raw_fact"], ensure_ascii=False, sort_keys=True),
                    json.dumps(row["derived"], ensure_ascii=False, sort_keys=True),
                )
                for row in facts
            ],
        )
        alias_rows = [
            (group, alias, _canonical(alias))
            for group, aliases in sorted(TARGET_ALIASES.items())
            for alias in aliases
        ]
        connection.executemany("INSERT INTO target_aliases VALUES (?, ?, ?)", alias_rows)

        counts = {
            "documents": len(documents),
            "pages": sum(row["page_count"] for row in documents),
            "facts": len(facts),
            "known_percent": sum(row["derived"]["percent_state"] == "known" for row in facts),
            "known_spend_threshold": sum(row["derived"]["spend_state"] == "known" for row in facts),
            "known_cap": sum(row["derived"]["cap_state"] == "known" for row in facts),
            "known_cap_period": sum(
                row["derived"]["cap_state"] == "known" and row["derived"]["cap_period_state"] == "known"
                for row in facts
            ),
            "positive_supported_action": sum(row["derived"]["action_status"] == "true" for row in facts),
        }
        summary = {
            "schema_version": SCHEMA_VERSION,
            "source_glob": SOURCE_GLOB,
            "source_policy": "immutable Upstage normalized JSON; OCR/validation assumed passed for this experiment",
            "source_version": {
                "provider": documents[0]["provider"],
                "structure_schema_version": documents[0]["structure_schema_version"],
                "normalization_contract": documents[0]["normalization_contract"],
                "normalization_config_hash": (documents[0]["provenance"] or {}).get("config_hash"),
                "normalization_model_recorded_in_source": (documents[0]["provenance"] or {}).get("model"),
                "normalization_reasoning_recorded_in_source": (documents[0]["provenance"] or {}).get("reasoning"),
            },
            "counts": counts,
            "support_rates": {
                key: (value / counts["facts"] if counts["facts"] else 0.0)
                for key, value in counts.items()
                if key.startswith("known_") or key == "positive_supported_action"
            },
            "target_aliases": TARGET_ALIASES,
            "limitations": [
                "OCR and normalization correctness were not revalidated.",
                "Derived numbers remain unknown unless typed and raw values uniquely agree.",
                "Percent ranges, bounds, and mixed money/percent expressions are not treated as guaranteed rates.",
                "Spend thresholds require an explicit previous-month phrase and a connected single minimum.",
                "Monthly and daily caps only match when the same fact has one explicit period; absent or mixed periods remain unknown.",
                "Eligible spend and transaction ceilings are not treated as benefit payout caps.",
                "Candidate selection is source-relative diagnostic evidence, not an evaluation gold label.",
            ],
        }
        connection.executemany(
            "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in summary.items()],
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    summary["verification"] = {"sqlite_integrity_check": integrity, "foreign_key_errors": foreign_key_errors}
    summary["artifact_sha256"] = {
        "documents.jsonl": _sha256_bytes(documents_path.read_bytes()),
        "facts.jsonl": _sha256_bytes(facts_path.read_bytes()),
        "structured.sqlite": _sha256_bytes(database_path.read_bytes()),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def load_facts(output_dir: Path) -> list[dict[str, Any]]:
    """Restore fact rows from the experiment SQLite artifact, read-only.

    ``facts.jsonl`` remains a transparent audit/export artifact.  Search callers
    use the persisted database rows so the evaluated structured search exercises
    the database rather than a parallel JSONL code path.
    """

    database_path = (output_dir / "structured.sqlite").resolve()
    uri = f"{database_path.as_uri()}?mode=ro&immutable=1"
    query = """
        SELECT
            f.fact_id, f.document_id, f.ordinal, f.raw_json, f.derived_json,
            d.raw_json
        FROM facts AS f
        JOIN documents AS d ON d.document_id = f.document_id
        ORDER BY d.source_relative_path, f.ordinal
    """
    restored: list[dict[str, Any]] = []
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        for fact_id, document_id, ordinal, raw_fact_json, derived_json, document_json in connection.execute(query):
            raw_fact = json.loads(raw_fact_json)
            derived = json.loads(derived_json)
            document = json.loads(document_json)
            restored.append(
                {
                    "fact_id": fact_id,
                    "document_id": document_id,
                    "ordinal": ordinal,
                    "provider": document["provider"],
                    "source_pdf_sha256": document["source_pdf_sha256"],
                    "source_relative_path": document["source_relative_path"],
                    "source_path_sha256": document["source_path_sha256"],
                    "identity": document["identity"],
                    "fields": {
                        key: raw_fact.get(key, "")
                        for key in (
                            "target", "action", "benefit_type", "value", "unit", "condition",
                            "cap", "period", "frequency", "exceptions"
                        )
                    },
                    "typed_normalization": raw_fact.get("typed_normalization") or {},
                    "field_evidence": raw_fact.get("field_evidence") or {},
                    "relation_scope": raw_fact.get("relation_scope") or {},
                    "derived": derived,
                    "raw_fact": raw_fact,
                }
            )
    return restored


def query_facts_by_cards(database_path: Path, card_keys: list[str]) -> list[tuple[str, str]]:
    """Parameterised helper used by notebooks; empty scope intentionally returns no rows."""

    if not card_keys:
        return []
    placeholders = ",".join("?" for _ in card_keys)
    query = f"SELECT fact_id, document_id FROM facts WHERE document_id IN ({placeholders}) ORDER BY document_id, ordinal"
    with sqlite3.connect(database_path) as connection:
        return [(str(a), str(b)) for a, b in connection.execute(query, card_keys)]


def _expanded_targets(targets: list[str]) -> list[str]:
    expanded: list[str] = []
    for target in targets:
        normalized = _canonical(target)
        aliases = TARGET_ALIASES.get(normalized, [target])
        expanded.extend(_canonical(alias) for alias in aliases)
    return sorted(set(item for item in expanded if item))


@dataclass(frozen=True)
class _FactEvaluation:
    status: str
    fact_id: str | None = None


def _evaluate_fact(row: dict[str, Any], requirement: dict[str, Any]) -> _FactEvaluation:
    fields = row["fields"]
    derived = row["derived"]
    target = derived.get("target_normalized") or _canonical(str(fields.get("target") or ""))
    wanted = _expanded_targets(list(requirement.get("targets") or []))
    excluded = _expanded_targets(list(requirement.get("exclude_targets") or []))
    if wanted and not any(alias in target for alias in wanted):
        return _FactEvaluation("false")
    if excluded and any(alias in target for alias in excluded):
        return _FactEvaluation("false")

    requested_action = requirement.get("action", "any")
    action_status = derived.get("action_status", "unknown")
    if action_status == "false":
        return _FactEvaluation("false")
    if requested_action != "any" and derived.get("action_kind") != requested_action:
        if derived.get("action_kind") is None:
            return _FactEvaluation("unknown")
        return _FactEvaluation("false")
    uncertain = action_status != "true"

    # A target mentioned as an exception cannot be promoted to a positive match.
    exception_text = derived.get("exceptions_normalized", "")
    if wanted and exception_text and any(alias in exception_text for alias in wanted):
        uncertain = True

    min_percent = requirement.get("min_percent")
    if min_percent is not None:
        if derived.get("percent_state") != "known":
            uncertain = True
        elif float(derived["percent"]) < float(min_percent):
            return _FactEvaluation("false")

    max_spend = requirement.get("max_spend_krw")
    if max_spend is not None:
        if derived.get("spend_state") != "known":
            uncertain = True
        elif int(derived["spend_threshold_krw"]) > int(max_spend):
            return _FactEvaluation("false")

    min_cap = requirement.get("min_cap_krw")
    if min_cap is not None:
        if derived.get("cap_state") != "known":
            uncertain = True
        elif int(derived["cap_krw"]) < int(min_cap):
            return _FactEvaluation("false")
        requested_period = requirement.get("cap_period", "any")
        if requested_period not in {"any", "month", "day"}:
            raise ValueError("unsupported cap_period")
        if requested_period != "any":
            if derived.get("cap_period_state") != "known":
                uncertain = True
            elif derived.get("cap_period") != requested_period:
                return _FactEvaluation("false")
    return _FactEvaluation("unknown" if uncertain else "true", row["fact_id"] if not uncertain else None)


def select_cards(facts: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    """Apply OR-of-AND groups with conservative three-valued matching."""

    by_card: dict[str, list[dict[str, Any]]] = {}
    for row in facts:
        by_card.setdefault(str(row["document_id"]), []).append(row)
    requested_scope = list(plan.get("card_keys") or [])
    cards = requested_scope if requested_scope else sorted(by_card)
    cards = [card for card in cards if card in by_card]

    if plan.get("route") == "unstructured":
        return {
            "eligible_cards": cards,
            "matching_fact_ids": {},
            "unknown_cards": [],
            "reason": "unstructured route preserves the requested card scope without fact filtering",
        }
    if plan.get("route") != "structured_then_unstructured":
        raise ValueError("unsupported route")
    if plan.get("unresolved"):
        return {
            "eligible_cards": [],
            "matching_fact_ids": {},
            "unknown_cards": cards,
            "reason": "plan contains unresolved structured requirements",
        }
    groups = plan.get("groups") or []
    if not groups:
        return {
            "eligible_cards": [],
            "matching_fact_ids": {},
            "unknown_cards": cards,
            "reason": "structured route has no requirement groups",
        }

    eligible: list[str] = []
    unknown: list[str] = []
    matched: dict[str, list[str]] = {}
    for card in cards:
        group_states: list[str] = []
        true_group_ids: list[str] = []
        for group in groups:
            requirement_states: list[str] = []
            group_ids: list[str] = []
            for requirement in group:
                evaluations = [_evaluate_fact(row, requirement) for row in by_card[card]]
                true_evaluations = [item for item in evaluations if item.status == "true"]
                if true_evaluations:
                    requirement_states.append("true")
                    group_ids.extend(item.fact_id for item in true_evaluations if item.fact_id)
                elif any(item.status == "unknown" for item in evaluations):
                    requirement_states.append("unknown")
                else:
                    requirement_states.append("false")
            if all(state == "true" for state in requirement_states):
                group_states.append("true")
                true_group_ids.extend(group_ids)
            elif any(state == "false" for state in requirement_states):
                group_states.append("false")
            else:
                group_states.append("unknown")
        if "true" in group_states:
            eligible.append(card)
            matched[card] = sorted(set(true_group_ids))
        elif "unknown" in group_states:
            unknown.append(card)

    return {
        "eligible_cards": eligible,
        "matching_fact_ids": matched,
        "unknown_cards": unknown,
        "reason": "source-relative OR-of-AND fact filter; unknown values are not treated as zero or false",
    }


def main() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    output_dir = repository_root / "notebooks" / "data" / "30_structured_benefit_db"
    print(json.dumps(build_database(repository_root, output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
