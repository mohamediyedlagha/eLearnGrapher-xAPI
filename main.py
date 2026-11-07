
import sqlite3
import json
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

# ==========================================
# STEP 1 - Configuration
# ==========================================

FOLDER = Path(__file__).resolve().parent
DATABASE = FOLDER / "test.sqlite"
OUTPUT = FOLDER / "xapi_statements.json"

# Temporary URL for testing
BASE_URL = "https://example.org/elearnGrapher"

# Source timezone must be confirmed
SOURCE_UTC_OFFSET = None


# ==========================================
# STEP 2 - Convert timestamp
# ==========================================

def xapi_timestamp(raw_value):

    if raw_value is None or SOURCE_UTC_OFFSET is None:
        return None

    sign = 1 if SOURCE_UTC_OFFSET.startswith("+") else -1

    hours, minutes = map(
        int, SOURCE_UTC_OFFSET[1:].split(":")
    )

    offset = timezone(
        sign * timedelta(hours=hours, minutes=minutes)
    )

    dt = datetime.fromisoformat(str(raw_value))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=offset)

    return dt.isoformat(timespec="seconds")


# ==========================================
# STEP 3 - Convert action into xAPI
# ==========================================

def convert_to_xapi(action):

    learner_id = str(action["learnerID"])
    object_id = str(action["objectID"])
    row_id = action["source_rowid"]

    # Identify activity type
    if object_id.endswith("::QS"):
        verb = "answered"
        activity_type = "cmi.interaction"

    elif object_id.endswith("::TX"):
        verb = "experienced"
        activity_type = "lesson"

    else:
        raise ValueError(
            f"Unknown action type: {object_id}"
        )

    # Create unique ID for each action
    unique_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"elearnGrapher/test.sqlite/action/{row_id}"
        )
    )

    # Build xAPI statement
    statement = {

        "id": unique_id,

        "actor": {
            "objectType": "Agent",
            "account": {
                "homePage": BASE_URL,
                "name": learner_id
            }
        },

        "verb": {
            "id": (
                "http://adlnet.gov/expapi/verbs/"
                + verb
            ),
            "display": {
                "en-US": verb
            }
        },

        "object": {
            "objectType": "Activity",
            "id": (
                f"{BASE_URL}/activities/"
                + quote(object_id, safe="")
            ),
            "definition": {
                "name": {
                    "en-US": object_id
                },
                "type": (
                    "http://adlnet.gov/expapi/activities/"
                    + activity_type
                )
            }
        }
    }

    # ======================================
    # STEP 4 - Add results
    # ======================================

    result = {}

    # Duration (assuming seconds)
    if action["duration"] is not None:

        duration = int(action["duration"])

        if duration < 0:
            raise ValueError(
                f"Negative duration in row {row_id}"
            )

        result["duration"] = f"PT{duration}S"

    # Score
    if action["points"] is not None:
        result["score"] = {
            "raw": float(action["points"])
        }

    # Learner answer
    answer = action["statement"]

    if answer is not None and str(answer).strip():
        result["response"] = str(answer)

    if result:
        statement["result"] = result

    # Add timestamp if timezone is confirmed
    timestamp = xapi_timestamp(action["endtime"])

    if timestamp:
        statement["timestamp"] = timestamp

    return statement


# ==========================================
# STEP 5 - Main function
# ==========================================

def main():

    if not DATABASE.exists():
        raise FileNotFoundError(
            f"Database not found: {DATABASE}"
        )

    print("Connected to SQLite database!")

    # Extract all learner actions
    with sqlite3.connect(DATABASE) as conn:

        conn.row_factory = sqlite3.Row

        cursor = conn.cursor()

        cursor.execute(
            "SELECT rowid AS source_rowid, "
            "objectID, learnerID, endtime, "
            "points, duration, statement "
            "FROM action ORDER BY rowid"
        )

        actions = cursor.fetchall()

    print(f"Total actions found: {len(actions)}")

    # Convert actions to xAPI
    xapi_statements = []

    for action in actions:

        xapi = convert_to_xapi(action)

        xapi_statements.append(xapi)

    # ======================================
    # STEP 6 - Validate xAPI statements
    # ======================================

    assert len(xapi_statements) == len(actions)

    unique_ids = {
        s["id"] for s in xapi_statements
    }

    assert len(unique_ids) == len(xapi_statements)

    for statement in xapi_statements:

        assert "actor" in statement
        assert "verb" in statement
        assert "object" in statement

    # ======================================
    # STEP 7 - Export JSON
    # ======================================

    with open(
        OUTPUT,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            xapi_statements,
            file,
            indent=4,
            ensure_ascii=False,
            allow_nan=False
        )

    # ======================================
    # STEP 8 - Display statistics
    # ======================================

    stats = Counter(
        s["verb"]["display"]["en-US"]
        for s in xapi_statements
    )

    answers = sum(
        "response" in s.get("result", {})
        for s in xapi_statements
    )

    print("\n===== xAPI EXPORT SUMMARY =====")

    print(f"Total actions: {len(actions)}")
    print(f"Pages experienced: {stats['experienced']}")
    print(f"Questions answered: {stats['answered']}")
    print(f"Answers available: {answers}")
    print(f"Unique IDs: {len(unique_ids)}")
    print(f"Output file: {OUTPUT}")

    if SOURCE_UTC_OFFSET is None:
        print("Timestamps omitted: timezone not confirmed.")

    print("\nConversion completed successfully!")


# ==========================================
# RUN PROGRAM
# ==========================================

if __name__ == "__main__":
    main()
