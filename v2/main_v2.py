
"""eLearnGrapher: SQLite to enriched xAPI 1.0.3.

Reads the database without modifying it.
Adds question metadata and learning context.
Preserves source fields and avoids inventing missing data.
Does not send data to an LRS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent

LOCALES = {
    "en": "en-US",
    "de": "de-DE",
    "es": "es-ES",
    "bg": "bg-BG"
}

VERBS = {
    "TX": "experienced",
    "QS": "answered"
}

XAPI_ACTIVITIES = "http://adlnet.gov/expapi/activities"
XAPI_VERBS = "http://adlnet.gov/expapi/verbs"


# ============================================
# 1. IDENTIFIERS AND DATABASE HELPERS
# ============================================

def normalize_base(base):
    base = base.strip().rstrip("/")
    parsed = urlparse(base)

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "The base IRI must be a valid HTTPS URI"
        )

    return base


def extension(base, key):
    return f"{base}/extensions/{key}"


def activity_iri(base, category, source_id):
    encoded = quote(str(source_id), safe="")
    return f"{base}/activities/{category}/{encoded}"


def read_rows(conn, table):
    return [
        dict(row)
        for row in conn.execute(
            f'SELECT * FROM "{table}"'
        )
    ]


def create_catalogue(records):
    catalogue = defaultdict(dict)

    for record in records:
        source_id = record["ID"]
        language = record["language"]

        if language not in LOCALES:
            raise ValueError(
                f"Unknown metadata language: {language}"
            )

        if language in catalogue[source_id]:
            raise ValueError(
                f"Duplicate translation: {source_id}"
            )

        catalogue[source_id][language] = record

    return dict(catalogue)


def english_or_first(translations):
    return (
        translations.get("en")
        or next(iter(translations.values()))
    )


def language_map(translations, field):
    return {
        LOCALES[language]: str(record[field])
        for language, record in sorted(
            translations.items()
        )
        if (
            record.get(field) is not None
            and str(record[field]).strip()
        )
    }


# ============================================
# 2. MULTILINGUAL QUESTION OPTIONS
# ============================================

def option_texts(record):
    return [
        line.strip()
        for line in (
            record.get("options") or ""
        ).splitlines()
        if line.strip()
    ]


def choices_from_source(translations):
    english = translations.get("en")

    if english is None:
        raise ValueError(
            "English metadata is required"
        )

    reference = option_texts(english)

    if not reference:
        raise ValueError(
            f"No choices for {english['ID']}"
        )

    choices = [
        {
            "id": f"opt_{index + 1}",
            "description": {
                "en-US": text
            }
        }
        for index, text in enumerate(reference)
    ]

    correct_positions = None

    for language, record in sorted(
        translations.items()
    ):
        localized = option_texts(record)

        if len(localized) != len(reference):
            raise ValueError(
                f"Different option counts: "
                f"{record['ID']}/{language}"
            )

        for choice, text in zip(
            choices, localized
        ):
            choice["description"][
                LOCALES[language]
            ] = text

        raw_correct = str(
            record.get("correctAnswer") or ""
        ).strip()

        if raw_correct:
            answers = [
                item.strip()
                for item in raw_correct.split("##")
            ]

            if not all(
                answer in localized
                for answer in answers
            ):
                raise ValueError(
                    "Correct answer not in options"
                )

            positions = tuple(
                localized.index(answer)
                for answer in answers
            )

            if correct_positions is None:
                correct_positions = positions

            elif positions != correct_positions:
                raise ValueError(
                    "Correct option positions differ "
                    "between translations"
                )

    return choices


def to_choice_ids(raw, choices):
    if (
        not raw
        or not str(raw).strip()
        or not choices
    ):
        return None

    lookup = {}

    for choice in choices:
        for text in choice[
            "description"
        ].values():

            if (
                text in lookup
                and lookup[text] != choice["id"]
            ):
                raise ValueError(
                    "Ambiguous translated choice"
                )

            lookup[text] = choice["id"]

    answers = [
        item.strip()
        for item in str(raw).split("##")
    ]

    if not all(
        answer in lookup
        for answer in answers
    ):
        return None

    return "[,]".join(
        lookup[answer]
        for answer in answers
    )


# ============================================
# 3. QUESTION ACTIVITY DEFINITION
# ============================================

def question_definition(translations, base):
    source = english_or_first(translations)
    question_type = source["questionType"]

    if any(
        row["questionType"] != question_type
        for row in translations.values()
    ):
        raise ValueError(
            "Inconsistent question types"
        )

    definition = {
        "type": (
            f"{XAPI_ACTIVITIES}/cmi.interaction"
        ),
        "name": (
            language_map(translations, "title")
            or {"en-US": source["ID"]}
        ),
        "description": language_map(
            translations, "questionText"
        ),
        "extensions": {
            extension(
                base, "source-question-id"
            ): source["ID"],

            extension(
                base, "source-question-type"
            ): question_type,

            extension(
                base, "source-course"
            ): source.get("course")
        }
    }

    if not definition["description"]:
        raise ValueError(
            "Question text missing"
        )

    choices = []

    if question_type in ("PO", "PM"):
        definition["interactionType"] = "choice"

        choices = choices_from_source(
            translations
        )

        definition["choices"] = choices

        correct = str(
            source.get("correctAnswer") or ""
        )

        pattern = to_choice_ids(
            correct, choices
        )

        if pattern:
            definition[
                "correctResponsesPattern"
            ] = [pattern]

    elif question_type == "SA":
        definition["interactionType"] = "fill-in"

    else:
        raise ValueError(
            f"Unsupported type: {question_type}"
        )

    # Correct answer is not the student's answer.
    if source.get("correctAnswer"):
        definition["extensions"][
            extension(base, "source-correct-answer")
        ] = source["correctAnswer"]

    return definition, choices, question_type


# ============================================
# 4. TEXT PAGE DEFINITION
# ============================================

def page_definition(translations, base):
    source = english_or_first(translations)

    return {
        "type": f"{XAPI_ACTIVITIES}/lesson",
        "name": (
            language_map(translations, "title")
            or {"en-US": source["ID"]}
        ),
        "extensions": {
            extension(
                base, "source-page-id"
            ): source["ID"]
        }
    }


# ============================================
# 5. COURSE / CHAPTER / NUGGET CONTEXT
# ============================================

def learning_context(
    source,
    chapters,
    nuggets,
    courses,
    base
):
    course_id = str(source["course"])

    chapter_id = (
        f"{course_id}Ch{source['chapter']}"
    )

    nugget_id = (
        f"{chapter_id}_{source['nugget']}"
    )

    for name, key, catalogue in (
        ("course", course_id, courses),
        ("chapter", chapter_id, chapters),
        ("nugget", nugget_id, nuggets)
    ):
        if key not in catalogue:
            raise ValueError(
                f"Missing {name}: {key}"
            )

    def make_activity(
        category, source_id,
        catalogue, activity_type
    ):
        return {
            "objectType": "Activity",
            "id": activity_iri(
                base, category, source_id
            ),
            "definition": {
                "name": language_map(
                    catalogue[source_id],
                    "title"
                ),
                "type": (
                    f"{XAPI_ACTIVITIES}/{activity_type}"
                )
            }
        }

    return {
        "parent": [
            make_activity(
                "nuggets",
                nugget_id,
                nuggets,
                "module"
            )
        ],
        "grouping": [
            make_activity(
                "chapters",
                chapter_id,
                chapters,
                "module"
            ),
            make_activity(
                "courses",
                course_id,
                courses,
                "course"
            )
        ]
    }


# ============================================
# 6. TIMESTAMP AND DURATION
# ============================================

def source_timestamp(value, source_timezone):
    if value is None or source_timezone is None:
        return None

    dt = datetime.fromisoformat(str(value))

    if dt.tzinfo is not None:
        return dt.isoformat(timespec="seconds")

    zone = ZoneInfo(source_timezone)

    first = dt.replace(tzinfo=zone, fold=0)
    second = dt.replace(tzinfo=zone, fold=1)

    if first.utcoffset() != second.utcoffset():
        raise ValueError(
            "Ambiguous local time: "
            + str(value)
        )

    roundtrip = (
        first.astimezone(timezone.utc)
        .astimezone(zone)
        .replace(tzinfo=None)
    )

    if roundtrip != dt:
        raise ValueError(
            "Nonexistent local time: "
            + str(value)
        )

    return first.isoformat(timespec="seconds")


def xapi_duration(value, duration_unit):
    if value is None or duration_unit == "unknown":
        return None

    factor = (
        1 if duration_unit == "seconds"
        else 60
    )

    seconds = float(value) * factor

    if seconds < 0 or not seconds.is_integer():
        raise ValueError("Invalid duration")

    return f"PT{int(seconds)}S"


# ============================================
# 7. CONVERT ONE LEARNER ACTION
# ============================================

def convert(
    action,
    questions,
    pages,
    base,
    source_timezone=None,
    duration_unit="unknown",
    chapters=None,
    nuggets=None,
    courses=None
):
    object_id = str(action["objectID"])

    if "::" not in object_id:
        raise ValueError(
            f"Invalid object ID: {object_id}"
        )

    source_id, suffix = object_id.rsplit(
        "::", 1
    )

    if suffix not in VERBS:
        raise ValueError(
            f"Unsupported action: {object_id}"
        )

    catalogue = (
        questions if suffix == "QS"
        else pages
    )

    translations = catalogue.get(source_id)

    if not translations:
        raise ValueError(
            f"Metadata missing: {source_id}"
        )

    source = english_or_first(translations)

    if suffix == "QS":
        definition, choices, question_type = (
            question_definition(
                translations, base
            )
        )
    else:
        definition = page_definition(
            translations, base
        )
        choices = []
        question_type = None

    # Original student response
    raw_response = action["statement"]

    # Store every original result field.
    result = {
        "extensions": {
            extension(
                base, "source-points"
            ): action["points"],

            extension(
                base, "source-duration"
            ): action["duration"],

            extension(
                base, "source-response"
            ): raw_response
        }
    }

    # Question score
    if (
        suffix == "QS"
        and action["points"] is not None
    ):
        score = {
            "raw": float(action["points"])
        }

        if source.get("points") is not None:
            score["max"] = float(
                source["points"]
            )

        result["score"] = score

    # Do not invent missing learner answers.
    if (
        suffix == "QS"
        and raw_response is not None
        and str(raw_response).strip()
    ):
        if question_type in ("PO", "PM"):
            mapped = to_choice_ids(
                str(raw_response),
                choices
            )

            if mapped is not None:
                result["response"] = mapped

        else:
            result["response"] = str(
                raw_response
            )

    # Only convert duration with confirmed unit.
    duration = xapi_duration(
        action["duration"],
        duration_unit
    )

    if duration is not None:
        result["duration"] = duration

    # Original source identifiers and metadata.
    context = {
        "extensions": {
            extension(
                base, "source-action-rowid"
            ): action["row_id"],

            extension(
                base, "source-object-id"
            ): object_id,

            extension(
                base, "source-endtime"
            ): action["endtime"],

            extension(
                base, "source-course"
            ): source.get("course"),

            extension(
                base, "source-chapter"
            ): source.get("chapter"),

            extension(
                base, "source-nugget"
            ): source.get("nugget")
        }
    }

    if (
        chapters is not None
        and nuggets is not None
        and courses is not None
    ):
        context["contextActivities"] = (
            learning_context(
                source,
                chapters,
                nuggets,
                courses,
                base
            )
        )

    statement = {
        "actor": {
            "objectType": "Agent",
            "account": {
                "homePage": base,
                "name": str(
                    action["learnerID"]
                )
            }
        },
        "verb": {
            "id": (
                f"{XAPI_VERBS}/{VERBS[suffix]}"
            ),
            "display": {
                "en-US": VERBS[suffix]
            }
        },
        "object": {
            "objectType": "Activity",
            "id": (
                f"{base}/activities/"
                + quote(object_id, safe="")
            ),
            "definition": definition
        },
        "result": result,
        "context": context
    }

    timestamp = source_timestamp(
        action["endtime"],
        source_timezone
    )

    if timestamp is not None:
        statement["timestamp"] = timestamp

    # Stable ID for the same statement content.
    canonical = json.dumps(
        statement,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    )

    statement["id"] = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"eLearnGrapher/v2/{canonical}"
        )
    )

    return statement


# ============================================
# 8. CONVERT THE WHOLE DATABASE
# ============================================

def export_all(
    db_path,
    base,
    source_timezone=None,
    duration_unit="unknown"
):
    base = normalize_base(base)
    db_path = Path(db_path)

    if duration_unit not in (
        "unknown",
        "seconds",
        "minutes"
    ):
        raise ValueError(
            "Invalid duration unit"
        )

    if source_timezone is not None:
        ZoneInfo(source_timezone)

    with closing(
        sqlite3.connect(
            f"file:{db_path.resolve()}?mode=ro",
            uri=True
        )
    ) as conn:
        conn.row_factory = sqlite3.Row

        actions = [
            dict(row)
            for row in conn.execute(
                "SELECT rowid AS row_id, "
                "objectID, learnerID, endtime, "
                "points, duration, statement "
                "FROM action ORDER BY rowid"
            )
        ]

        questions = create_catalogue(
            read_rows(conn, "question")
        )

        pages = create_catalogue(
            read_rows(conn, "page")
        )

        courses = create_catalogue(
            read_rows(conn, "course")
        )

        chapters = create_catalogue(
            read_rows(conn, "chapter")
        )

        nuggets = create_catalogue(
            read_rows(conn, "nugget")
        )

    statements = []

    for row in actions:
        statements.append(
            convert(
                row,
                questions,
                pages,
                base,
                source_timezone,
                duration_unit,
                chapters,
                nuggets,
                courses
            )
        )

    ids = {
        statement["id"]
        for statement in statements
    }

    if len(ids) != len(statements):
        raise ValueError(
            "Duplicate statement IDs detected"
        )

    verbs = Counter(
        statement["verb"]["display"]["en-US"]
        for statement in statements
    )

    question_types = Counter(
        english_or_first(
            questions[row["objectID"][:-4]]
        )["questionType"]
        for row in actions
        if row["objectID"].endswith("::QS")
    )

    available_responses = sum(
        bool(str(row["statement"] or "").strip())
        for row in actions
        if row["objectID"].endswith("::QS")
    )

    standardized_responses = sum(
        "response" in item["result"]
        for item in statements
        if item["verb"]["id"].endswith("/answered")
    )

    contextualized = sum(
        "contextActivities" in item["context"]
        for item in statements
    )

    multilingual_choices = sum(
        bool(
            item["object"]["definition"].get(
                "choices"
            )
        )
        for item in statements
        if item["verb"]["id"].endswith("/answered")
    )

    report = {
        "source": db_path.name,
        "source_sha256": hashlib.sha256(
            db_path.read_bytes()
        ).hexdigest(),
        "total_actions": len(actions),
        "verbs": dict(verbs),
        "question_types": dict(question_types),
        "question_actions_with_nonempty_source_response": (
            available_responses
        ),
        "question_actions_without_source_response": (
            verbs["answered"] - available_responses
        ),
        "question_actions_with_standard_xapi_response": (
            standardized_responses
        ),
        "questions_with_unmapped_nonempty_response": (
            available_responses - standardized_responses
        ),
        "timestamps_included": sum(
            "timestamp" in item
            for item in statements
        ),
        "duration_iso8601_included": sum(
            "duration" in item["result"]
            for item in statements
        ),
        "contextualized_actions": contextualized,
        "multilingual_choice_questions": multilingual_choices,
        "source_timezone": source_timezone,
        "duration_unit": duration_unit,
        "base_iri": base,
        "notes": [
            "Original action fields are preserved.",
            "Learning context is included.",
            "Missing answers are not invented.",
            "Timezone and duration unit need confirmation."
        ]
    }

    return statements, report


# ============================================
# 9. COMMAND LINE INTERFACE
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description="eLearnGrapher xAPI V2"
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT.parent / "test.sqlite"
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "xapi_statements_v2.json"
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "validation_report_v2.json"
    )

    parser.add_argument(
        "--base-iri",
        required=True
    )

    parser.add_argument(
        "--source-timezone"
    )

    parser.add_argument(
        "--duration-unit",
        choices=["unknown", "seconds", "minutes"],
        default="unknown"
    )

    args = parser.parse_args()

    statements, report = export_all(
        args.db,
        args.base_iri,
        args.source_timezone,
        args.duration_unit
    )

    args.output.parent.mkdir(
        parents=True, exist_ok=True
    )

    args.report.parent.mkdir(
        parents=True, exist_ok=True
    )

    args.output.write_text(
        json.dumps(
            statements,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        ),
        encoding="utf-8"
    )

    args.report.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        ),
        encoding="utf-8"
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        )
    )

    print(
        f"Export completed: "
        f"{len(statements)} xAPI statements"
    )


if __name__ == "__main__":
    main()
