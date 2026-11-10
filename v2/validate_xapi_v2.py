
"""Local xAPI 1.0.x structural and semantic checks for eLearnGrapher V2.

This is NOT an official xAPI conformance certification or a live LRS test.
Only reads the JSON export; never sends learner data to a server.

Usage (from v2/):
    python validate_xapi_v2.py
"""

from __future__ import annotations

import argparse
import json
import re
import uuid

from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent

DURATION_RE = re.compile(r"^PT\d+(?:\.\d+)?S$")
LANG_RE = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$")

VALID_TYPES = {
    "choice",
    "fill-in",
    "long-fill-in"
}


# ============================================
# 1. VALIDATE IRIs
# ============================================

def iri(value):

    if (
        not isinstance(value, str)
        or not value.strip()
        or any(c.isspace() for c in value)
    ):
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme in ("https", "http")
        and bool(parsed.netloc)
    )


# ============================================
# 2. VALIDATE LANGUAGE MAPS
# ============================================

def lang_map(value):

    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(k, str)
            and LANG_RE.fullmatch(k)
            and isinstance(v, str)
            and bool(v.strip())
            for k, v in value.items()
        )
    )


# ============================================
# 3. VALIDATE ALL XAPI STATEMENTS
# ============================================

def validate(statements):

    if not isinstance(statements, list):
        raise ValueError(
            "The JSON root must be an array of statements"
        )

    errors = []

    ids = set()
    verbs = Counter()
    interaction_types = Counter()

    missing_timestamps = 0
    missing_durations = 0
    unanswered = 0
    contextualized = 0

    def error(index, message):
        errors.append(
            f"Statement {index}: {message}"
        )

    # Check each xAPI statement
    for index, s in enumerate(statements, 1):

        if not isinstance(s, dict):
            error(index, "not a JSON object")
            continue

        # ====================================
        # A. STATEMENT ID
        # ====================================

        try:

            sid = s["id"]
            uuid.UUID(sid)

            if sid in ids:
                error(
                    index,
                    "duplicate statement UUID"
                )

            ids.add(sid)

        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError
        ):
            error(
                index,
                "missing/invalid UUID"
            )

        # ====================================
        # B. ACTOR
        # ====================================

        actor = s.get("actor", {})

        account = (
            actor.get("account", {})
            if isinstance(actor, dict)
            else {}
        )

        if (
            not isinstance(actor, dict)
            or actor.get("objectType") != "Agent"
        ):
            error(
                index,
                "actor must be an Agent"
            )

        if (
            not isinstance(account, dict)
            or not iri(account.get("homePage"))
            or not isinstance(
                account.get("name"), str
            )
            or not account["name"].strip()
        ):
            error(
                index,
                "actor.account must have "
                "homePage IRI and a name"
            )

        # ====================================
        # C. VERB
        # ====================================

        verb = s.get("verb", {})

        verb_id = (
            verb.get("id")
            if isinstance(verb, dict)
            else None
        )

        if not iri(verb_id):
            error(
                index,
                "invalid verb.id"
            )

        if (
            not isinstance(verb, dict)
            or not lang_map(
                verb.get("display")
            )
        ):
            error(
                index,
                "invalid verb.display language map"
            )

        verb_name = str(
            verb_id or ""
        ).rsplit("/", 1)[-1]

        verbs[verb_name] += 1

        if verb_name not in (
            "experienced",
            "answered"
        ):
            error(
                index,
                f"unexpected verb: {verb_name}"
            )

        # ====================================
        # D. OBJECT / ACTIVITY
        # ====================================

        obj = s.get("object", {})

        definition = (
            obj.get("definition", {})
            if isinstance(obj, dict)
            else {}
        )

        if (
            not isinstance(obj, dict)
            or obj.get("objectType") != "Activity"
            or not iri(obj.get("id"))
        ):
            error(
                index,
                "object must be an Activity with an IRI"
            )

        if not isinstance(definition, dict):
            error(
                index,
                "missing object.definition"
            )
            continue

        if not iri(definition.get("type")):
            error(
                index,
                "invalid activity definition.type"
            )

        if not lang_map(
            definition.get("name")
        ):
            error(
                index,
                "invalid activity definition.name"
            )

        # ====================================
        # E. RESULT / SCORE
        # ====================================

        result = s.get("result", {})

        if not isinstance(result, dict):
            error(
                index,
                "result must be an object"
            )
            continue

        score = result.get("score")

        if score is not None:

            if (
                not isinstance(score, dict)
                or isinstance(
                    score.get("raw"), bool
                )
                or not isinstance(
                    score.get("raw"),
                    (int, float)
                )
            ):
                error(
                    index,
                    "score.raw must be numeric"
                )

            elif "max" in score and (
                not isinstance(
                    score["max"],
                    (int, float)
                )
                or isinstance(
                    score["max"], bool
                )
                or score["raw"] > score["max"]
            ):
                error(
                    index,
                    "score.max invalid or below score.raw"
                )

        # ====================================
        # F. TIMESTAMP
        # ====================================

        if "timestamp" in s:

            try:

                when = datetime.fromisoformat(
                    s["timestamp"].replace(
                        "Z", "+00:00"
                    )
                )

                if when.tzinfo is None:
                    error(
                        index,
                        "timestamp has no timezone offset"
                    )

            except (
                ValueError,
                AttributeError,
                TypeError
            ):
                error(
                    index,
                    "invalid timestamp"
                )

        else:
            missing_timestamps += 1

        # ====================================
        # G. DURATION
        # ====================================

        if "duration" in result:

            if (
                not isinstance(
                    result["duration"], str
                )
                or not DURATION_RE.fullmatch(
                    result["duration"]
                )
            ):
                error(
                    index,
                    "invalid duration (expected e.g. PT35S)"
                )

        else:
            missing_durations += 1

        # ====================================
        # H. LEARNING CONTEXT
        # ====================================

        context = s.get("context", {})

        activities = (
            context.get("contextActivities")
            if isinstance(context, dict)
            else None
        )

        if isinstance(activities, dict):

            contextualized += 1

            for group in (
                "parent",
                "grouping"
            ):

                values = activities.get(
                    group, []
                )

                if not isinstance(values, list):

                    error(
                        index,
                        f"contextActivities.{group} "
                        "must be a list"
                    )

                elif any(
                    not isinstance(v, dict)
                    or not iri(v.get("id"))
                    for v in values
                ):

                    error(
                        index,
                        f"contextActivities.{group} "
                        "has invalid activity IDs"
                    )

        # ====================================
        # I. QUESTION INTERACTIONS
        # ====================================

        if verb_name == "answered":

            if definition.get("type") != (
                "http://adlnet.gov/expapi/"
                "activities/cmi.interaction"
            ):
                error(
                    index,
                    "answered activity should "
                    "be cmi.interaction"
                )

            # Question text
            if not lang_map(
                definition.get("description")
            ):
                error(
                    index,
                    "missing question text "
                    "in definition.description"
                )

            # Question interaction type
            typ = definition.get(
                "interactionType"
            )

            interaction_types[str(typ)] += 1

            if typ not in VALID_TYPES:
                error(
                    index,
                    f"unsupported interactionType: {typ}"
                )

            # Multiple-choice / single-choice
            if typ == "choice":

                choices = definition.get(
                    "choices"
                )

                if (
                    not isinstance(
                        choices, list
                    )
                    or not choices
                ):

                    error(
                        index,
                        "choice question needs choices"
                    )

                    choice_ids = set()

                else:

                    choice_ids = set()

                    for choice in choices:

                        if (
                            not isinstance(
                                choice, dict
                            )
                            or not isinstance(
                                choice.get("id"),
                                str
                            )
                            or not choice["id"]
                            or not lang_map(
                                choice.get("description")
                            )
                        ):

                            error(
                                index,
                                "invalid choice definition"
                            )
                            continue

                        if choice["id"] in choice_ids:
                            error(
                                index,
                                "duplicate choice ID"
                            )

                        choice_ids.add(
                            choice["id"]
                        )

                # Correct answers
                patterns = definition.get(
                    "correctResponsesPattern",
                    []
                )

                if not isinstance(
                    patterns, list
                ):

                    error(
                        index,
                        "correctResponsesPattern "
                        "must be a list"
                    )

                else:

                    for pattern in patterns:

                        if (
                            not isinstance(
                                pattern, str
                            )
                            or not set(
                                pattern.split("[,]")
                            ) <= choice_ids
                        ):

                            error(
                                index,
                                "correctResponsesPattern "
                                "contains unknown choice IDs"
                            )

                # Student response
                if "response" in result:

                    if (
                        not isinstance(
                            result["response"],
                            str
                        )
                        or not set(
                            result["response"].split("[,]")
                        ) <= choice_ids
                    ):

                        error(
                            index,
                            "result.response contains "
                            "unknown choice IDs"
                        )

            # Free-text response
            elif (
                "response" in result
                and not isinstance(
                    result["response"],
                    str
                )
            ):

                error(
                    index,
                    "free-text response must be a string"
                )

            if "response" not in result:
                unanswered += 1

    # ========================================
    # 4. WARNINGS
    # ========================================

    warnings = []

    if missing_timestamps:

        warnings.append(
            f"{missing_timestamps} statements "
            "lack standard timestamp "
            "(source timezone not confirmed)"
        )

    if missing_durations:

        warnings.append(
            f"{missing_durations} statements "
            "lack standard duration "
            "(source unit not confirmed)"
        )

    if unanswered:

        warnings.append(
            f"{unanswered} answered statements "
            "lack result.response in source"
        )

    # ========================================
    # 5. VALIDATION REPORT
    # ========================================

    return {
        "passed": not errors,

        "scope": (
            "Local xAPI 1.0.x structural/"
            "semantic checks only; "
            "not official conformance"
        ),

        "statements_checked": len(
            statements
        ),

        "unique_statement_ids": len(
            ids
        ),

        "verbs": dict(verbs),

        "interaction_types": dict(
            interaction_types
        ),

        "contextualized_actions": (
            contextualized
        ),

        "errors": errors,

        "warnings": warnings
    }


# ============================================
# 6. MAIN FUNCTION
# ============================================

def main():

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=(
            ROOT / "xapi_statements_v2.json"
        )
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=(
            ROOT / "xapi_validation_report_v2.json"
        )
    )

    args = parser.parse_args()

    statements = json.loads(
        args.input.read_text(
            encoding="utf-8"
        )
    )

    report = validate(
        statements
    )

    args.report.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        )
    )

    raise SystemExit(
        0 if report["passed"] else 1
    )


if __name__ == "__main__":
    main()
