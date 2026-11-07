"""Basic checks for eLearnGrapher xAPI JSON exports (not a full xAPI validator)."""
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

DURATION = re.compile(r"^P(?:\d+D|T(?:\d+(?:\.\d+)?H)?(?:\d+(?:\.\d+)?M)?(?:\d+(?:\.\d+)?S)?)$")


def is_http_url(value):
    return isinstance(value, str) and urlparse(value).scheme in ("http", "https") and bool(urlparse(value).netloc)


def validate(path):
    statements = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(statements, list):
        raise ValueError("Expected a JSON array of xAPI statements")

    errors, warnings, ids = [], [], set()
    verbs = Counter()
    for index, statement in enumerate(statements, start=1):
        prefix = f"Action {index}"
        if not isinstance(statement, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        try:
            sid = statement["id"]
            uuid.UUID(sid)
            if sid in ids:
                errors.append(f"{prefix}: duplicate id {sid}")
            ids.add(sid)
        except (KeyError, ValueError, TypeError, AttributeError):
            errors.append(f"{prefix}: missing or invalid UUID id")

        actor = statement.get("actor", {})
        verb = statement.get("verb", {})
        obj = statement.get("object", {})
        if not isinstance(actor, dict) or not isinstance(actor.get("account"), dict) or not actor["account"].get("name") or not is_http_url(actor["account"].get("homePage")):
            errors.append(f"{prefix}: invalid actor.account")
        if not isinstance(verb, dict) or not is_http_url(verb.get("id")):
            errors.append(f"{prefix}: invalid verb.id")
        else:
            verbs[verb["id"].rsplit("/", 1)[-1]] += 1
        if not isinstance(obj, dict) or not is_http_url(obj.get("id")):
            errors.append(f"{prefix}: invalid object.id")

        result = statement.get("result", {})
        if isinstance(result, dict):
            if "duration" in result and (not isinstance(result["duration"], str) or not DURATION.fullmatch(result["duration"])):
                errors.append(f"{prefix}: invalid result.duration")
            if "score" in result and (not isinstance(result["score"], dict) or not isinstance(result["score"].get("raw"), (int, float))):
                errors.append(f"{prefix}: invalid result.score.raw")
        else:
            errors.append(f"{prefix}: result must be an object")

    without_timestamp = sum("timestamp" not in s for s in statements if isinstance(s, dict))
    missing_response = sum(s.get("verb", {}).get("id", "").endswith("/answered") and not s.get("result", {}).get("response") for s in statements if isinstance(s, dict))
    with_placeholder = sum("example.org" in str(s.get("object", {}).get("id", "")) for s in statements if isinstance(s, dict))
    if without_timestamp:
        warnings.append(f"{without_timestamp} statements without timestamp (allowed in xAPI, but original event time is lost)")
    if missing_response:
        warnings.append(f"{missing_response} answered events without response text (check source data)")
    if with_placeholder:
        warnings.append(f"{with_placeholder} statements use temporary example.org activity IDs")
    warnings.append("Confirm that SQLite duration values are in seconds")

    print(f"Total statements: {len(statements)}")
    print(f"Unique IDs: {len(ids)}")
    print("Verbs:", dict(verbs))
    print(f"Basic validation errors: {len(errors)}")
    for item in errors[:15]:
        print("ERROR:", item)
    for item in warnings:
        print("WARNING:", item)
    print("Basic validation passed" if not errors else "Basic validation failed")
    return not errors


if __name__ == "__main__":
    file_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "xapi_statements.json"
    if not file_path.is_file():
        sys.exit(f"File not found: {file_path}")
    sys.exit(0 if validate(file_path) else 1)
