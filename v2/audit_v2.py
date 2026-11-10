
"""Audit the V2 xAPI export against SQLite source data."""

import argparse
import json
import sqlite3
import uuid

from collections import Counter
from contextlib import closing
from pathlib import Path

from main_v2 import extension, normalize_base


ROOT = Path(__file__).resolve().parent


def audit(db_path: Path, statements_path: Path):

    statements = json.loads(
        statements_path.read_text(encoding="utf-8")
    )

    if not isinstance(statements, list):
        raise ValueError("JSON must contain a list")

    # Read original database without modifying it
    with closing(
        sqlite3.connect(
            f"file:{db_path.resolve()}?mode=ro",
            uri=True
        )
    ) as conn:

        conn.row_factory = sqlite3.Row

        source = {
            row["row_id"]: dict(row)
            for row in conn.execute(
                "SELECT rowid AS row_id, "
                "learnerID, objectID, endtime, "
                "duration, points, statement "
                "FROM action ORDER BY rowid"
            )
        }

        questions = {
            row["ID"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM question "
                "WHERE language='en'"
            )
        }

        pages = {
            row["ID"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM page "
                "WHERE language='en'"
            )
        }

    errors = []
    warnings = []

    event_ids = set()
    processed_rows = set()

    verbs = Counter()

    # Check every statement
    for index, event in enumerate(statements, 1):

        prefix = f"Statement {index}"

        try:
            event_id = event["id"]
            uuid.UUID(event_id)

            if event_id in event_ids:
                errors.append(
                    f"{prefix}: duplicate event UUID"
                )

            event_ids.add(event_id)

            base = normalize_base(
                event["actor"]["account"]["homePage"]
            )

            src = event["context"]["extensions"]
            raw = event["result"]["extensions"]

            row_id = src[
                extension(base, "source-action-rowid")
            ]

            if row_id in processed_rows:
                errors.append(
                    f"{prefix}: duplicate rowid {row_id}"
                )

            processed_rows.add(row_id)

            action = source.get(row_id)

            if action is None:
                errors.append(
                    f"{prefix}: source row not found"
                )
                continue

            # Compare original information
            expected = {
                "actor": action["learnerID"],
                "object": action["objectID"],
                "date": action["endtime"],
                "duration": action["duration"],
                "points": action["points"],
                "response": action["statement"],
            }

            observed = {
                "actor": event["actor"]["account"]["name"],
                "object": src[
                    extension(base, "source-object-id")
                ],
                "date": src[
                    extension(base, "source-endtime")
                ],
                "duration": raw[
                    extension(base, "source-duration")
                ],
                "points": raw[
                    extension(base, "source-points")
                ],
                "response": raw[
                    extension(base, "source-response")
                ],
            }

            for field, expected_value in expected.items():

                if observed[field] != expected_value:
                    errors.append(
                        f"{prefix} / row {row_id}: "
                        f"{field} differs from SQLite"
                    )

            # Check action type
            is_question = action["objectID"].endswith(
                "::QS"
            )

            verb = (
                "answered"
                if is_question
                else "experienced"
            )

            verbs[verb] += 1

            if not event["verb"]["id"].endswith(
                "/" + verb
            ):
                errors.append(
                    f"{prefix}: verb mismatch"
                )

            definition = event["object"]["definition"]

            if is_question:

                # Check question text and type
                if not definition.get(
                    "description", {}
                ).get("en-US"):
                    errors.append(
                        f"{prefix}: question text missing"
                    )

                if not definition.get("interactionType"):
                    errors.append(
                        f"{prefix}: interaction type missing"
                    )

                question_id = action["objectID"][:-4]
                question = questions[question_id]

                # Check score
                score = event["result"].get(
                    "score", {}
                )

                if score.get("raw") != action["points"]:
                    errors.append(
                        f"{prefix}: score mismatch"
                    )

                if score.get("max") != question["points"]:
                    errors.append(
                        f"{prefix}: maximum score mismatch"
                    )

                # Check available question options
                if question["questionType"] in ("PO", "PM"):

                    expected_options = len([
                        item
                        for item in question["options"].splitlines()
                        if item.strip()
                    ])

                    actual_options = len(
                        definition.get("choices", [])
                    )

                    if actual_options != expected_options:
                        errors.append(
                            f"{prefix}: question options missing"
                        )

            else:

                # Check page metadata
                if not definition.get(
                    "name", {}
                ).get("en-US"):
                    errors.append(
                        f"{prefix}: page title missing"
                    )

                page_id = action["objectID"][:-4]

                if page_id not in pages:
                    errors.append(
                        f"{prefix}: page not found"
                    )

        except (KeyError, TypeError, ValueError) as error:
            errors.append(
                f"{prefix}: invalid structure ({error})"
            )

    # Check that all actions were converted
    missing_rows = set(source) - processed_rows

    if missing_rows:
        errors.append(
            f"{len(missing_rows)} source rows missing"
        )

    if len(statements) != len(source):
        errors.append(
            "Statement count differs from SQLite"
        )

    # Identify unresolved information
    no_timestamp = sum(
        "timestamp" not in statement
        for statement in statements
    )

    no_duration = sum(
        "duration" not in statement.get("result", {})
        for statement in statements
    )

    no_response = sum(
        statement["verb"]["id"].endswith("/answered")
        and "response" not in statement.get("result", {})
        for statement in statements
    )

    if no_timestamp:
        warnings.append(
            f"{no_timestamp} standard timestamps absent"
        )

    if no_duration:
        warnings.append(
            f"{no_duration} standard durations absent"
        )

    if no_response:
        warnings.append(
            f"{no_response} question responses absent"
        )

    return {
        "passed": not errors,
        "sqlite_actions": len(source),
        "xapi_statements": len(statements),
        "source_rows_matched": len(
            processed_rows.intersection(source)
        ),
        "verbs": dict(verbs),
        "errors": errors,
        "warnings": warnings,
        "scope": (
            "Source-data fidelity audit; "
            "not full xAPI conformance certification"
        )
    }


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Audit eLearnGrapher V2"
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT.parent / "test.sqlite"
    )

    parser.add_argument(
        "--statements",
        type=Path,
        default=ROOT / "xapi_statements_v2.json"
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "audit_report_v2.json"
    )

    args = parser.parse_args()

    result = audit(
        args.db,
        args.statements
    )

    args.report.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )

    raise SystemExit(
        0 if result["passed"] else 1
    )
