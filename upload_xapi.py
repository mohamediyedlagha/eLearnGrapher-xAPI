
import argparse
import base64
import getpass
import json
import sys
import uuid

from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ==========================================
# STEP 1 - Configuration
# ==========================================

DEFAULT_ENDPOINT = (
    "https://elearngrapher-test.lrs.io/xapi/"
)

XAPI_VERSION = "1.0.3"

JSON_FILE = (
    Path(__file__).resolve().parent
    / "xapi_statements.json"
)

BATCH_SIZE = 20


# ==========================================
# STEP 2 - Communicate with LRS
# ==========================================

def call_lrs(method, url, username, secret, payload=None):

    credentials = f"{username}:{secret}"

    token = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("ascii")

    headers = {
        "Authorization": f"Basic {token}",
        "X-Experience-API-Version": XAPI_VERSION,
        "Accept": "application/json"
    }

    body = None

    if payload is not None:
        headers["Content-Type"] = "application/json"

        body = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

    request = Request(
        url,
        data=body,
        headers=headers,
        method=method
    )

    try:
        with urlopen(request, timeout=40) as response:

            raw = response.read().decode("utf-8")

            data = (
                json.loads(raw)
                if raw.strip()
                else None
            )

            return response.status, data

    except HTTPError as error:

        details = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"HTTP {error.code}: {details[:500]}"
        ) from error

    except URLError as error:

        raise RuntimeError(
            f"Connection error: {error.reason}"
        ) from error


# ==========================================
# STEP 3 - Load and validate JSON
# ==========================================

def load_and_validate(path):

    if not path.is_file():
        raise ValueError(
            f"JSON file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8"
    ) as file:

        statements = json.load(file)

    if (
        not isinstance(statements, list)
        or not statements
    ):
        raise ValueError(
            "JSON must contain a nonempty list"
        )

    seen = set()

    for index, item in enumerate(
        statements,
        start=1
    ):

        if not isinstance(item, dict):
            raise ValueError(
                f"Statement {index} is not an object"
            )

        for field in (
            "id",
            "actor",
            "verb",
            "object"
        ):

            if field not in item:
                raise ValueError(
                    f"Statement {index} missing {field}"
                )

        try:
            uuid.UUID(item["id"])

        except (
            ValueError,
            TypeError,
            AttributeError
        ):
            raise ValueError(
                f"Invalid ID at index {index}"
            ) from None

        if item["id"] in seen:
            raise ValueError(
                f"Duplicate ID: {item['id']}"
            )

        seen.add(item["id"])

        if not isinstance(item["actor"], dict):
            raise ValueError(
                f"Invalid actor in statement {index}"
            )

        if (
            not isinstance(item["verb"], dict)
            or not item["verb"].get("id")
        ):
            raise ValueError(
                f"Invalid verb in statement {index}"
            )

        if (
            not isinstance(item["object"], dict)
            or not item["object"].get("id")
        ):
            raise ValueError(
                f"Invalid object in statement {index}"
            )

    return statements


# ==========================================
# STEP 4 - Verify saved event
# ==========================================

def verify_saved(
    statement,
    url,
    username,
    secret
):

    lookup = url + "?" + urlencode({
        "statementId": statement["id"]
    })

    status, stored = call_lrs(
        "GET",
        lookup,
        username,
        secret
    )

    return (
        status == 200
        and isinstance(stored, dict)
        and stored.get("id") == statement["id"]
    )


# ==========================================
# STEP 5 - Main function
# ==========================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Upload eLearnGrapher xAPI "
            "statements to Veracity LRS"
        )
    )

    parser.add_argument(
        "--upload",
        action="store_true",
        help="Send events to LRS"
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Upload only first N events"
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="Events per batch"
    )

    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error(
            "--limit must be positive"
        )

    if args.batch_size < 1 or args.batch_size > 100:
        parser.error(
            "--batch-size must be between 1 and 100"
        )

    # ======================================
    # STEP 6 - Load data
    # ======================================

    try:
        all_statements = load_and_validate(
            JSON_FILE
        )

    except (
        ValueError,
        OSError,
        json.JSONDecodeError
    ) as error:

        print(
            f"VALIDATION ERROR: {error}"
        )

        return 1

    if args.limit:

        statements = all_statements[
            :args.limit
        ]

    else:

        statements = all_statements

    # ======================================
    # STEP 7 - Calculate statistics
    # ======================================

    verbs = Counter(
        s["verb"].get(
            "display", {}
        ).get(
            "en-US", "unknown"
        )
        for s in statements
    )

    no_timestamp = sum(
        "timestamp" not in s
        for s in statements
    )

    no_response = sum(
        s["verb"]["id"].endswith("/answered")
        and "response" not in s.get("result", {})
        for s in statements
    )

    temporary_ids = sum(
        "example.org" in s["object"].get(
            "id", ""
        )
        for s in statements
    )

    print("\n===== eLearnGrapher xAPI UPLOAD =====")

    print(
        f"JSON file: {JSON_FILE}"
    )

    print(
        f"Available events: {len(all_statements)}"
    )

    print(
        f"Selected events: {len(statements)}"
    )

    print(
        f"Verbs: {dict(verbs)}"
    )

    print(
        f"Without timestamp: {no_timestamp}"
    )

    print(
        f"Questions without response: {no_response}"
    )

    print(
        f"Temporary activity IDs: {temporary_ids}"
    )

    print(
        "NOTE: Duration values are assumed "
        "to be seconds."
    )

    # ======================================
    # STEP 8 - Preview mode
    # ======================================

    if not args.upload:

        print(
            "\nPREVIEW ONLY: No data sent."
        )

        print(
            "Test 10 events: "
            "python upload_xapi.py --upload --limit 10"
        )

        print(
            "Upload all: "
            "python upload_xapi.py --upload"
        )

        return 0

    # ======================================
    # STEP 9 - Confirm real data upload
    # ======================================

    print(
        "\nWARNING: You are about to send "
        "learner data to an external LRS."
    )

    print(
        "Ensure you have permission "
        "to upload these records."
    )

    phrase = f"ENVOYER {len(statements)}"

    confirmation = input(
        f"Type exactly '{phrase}' to continue: "
    ).strip()

    if confirmation != phrase:

        print(
            "Cancelled. No data sent."
        )

        return 0

    # ======================================
    # STEP 10 - LRS authentication
    # ======================================

    endpoint = input(
        f"xAPI Endpoint [{DEFAULT_ENDPOINT}]: "
    ).strip() or DEFAULT_ENDPOINT

    endpoint = endpoint.rstrip("/") + "/"

    if not endpoint.startswith("https://"):

        print(
            "ERROR: HTTPS endpoint required."
        )

        return 1

    username = input(
        "Access Key / Username: "
    ).strip()

    secret = getpass.getpass(
        "Access Secret / Password: "
    )

    if not username or not secret:

        print(
            "ERROR: Missing API credentials."
        )

        return 1

    url = endpoint + "statements"

    uploaded = 0
    checked = 0

    # ======================================
    # STEP 11 - Upload in batches
    # ======================================

    for start in range(
        0,
        len(statements),
        args.batch_size
    ):

        batch = statements[
            start:start + args.batch_size
        ]

        number = (
            start // args.batch_size
        ) + 1

        print(
            f"\nBatch {number}: "
            f"sending {len(batch)} events..."
        )

        try:

            status, response = call_lrs(
                "POST",
                url,
                username,
                secret,
                batch
            )

            if status not in (200, 204):

                raise RuntimeError(
                    f"Unexpected POST status: {status}"
                )

            if isinstance(response, list):

                expected_ids = {
                    s["id"] for s in batch
                }

                if set(response) != expected_ids:

                    raise RuntimeError(
                        "LRS returned different IDs"
                    )

            uploaded += len(batch)

            print(
                f"POST HTTP {status}. "
                f"Total sent: {uploaded}/{len(statements)}"
            )

            # ==================================
            # STEP 12 - Verify first/last event
            # ==================================

            if len(batch) == 1:

                samples = [batch[0]]

            else:

                samples = [
                    batch[0],
                    batch[-1]
                ]

            for sample in samples:

                verified = verify_saved(
                    sample,
                    url,
                    username,
                    secret
                )

                if not verified:

                    raise RuntimeError(
                        "Could not retrieve statement "
                        + sample["id"]
                    )

                checked += 1

            print(
                f"Verified {len(samples)} "
                "event(s) via GET."
            )

        except RuntimeError as error:

            print(
                f"LRS ERROR: {error}"
            )

            print(
                f"Stopped after {uploaded} "
                "POST-accepted events."
            )

            return 1

    # ======================================
    # STEP 13 - Display final results
    # ======================================

    print("\n===== UPLOAD FINISHED =====")

    print(
        f"POST accepted: "
        f"{uploaded}/{len(statements)}"
    )

    print(
        f"GET spot-checks passed: {checked}"
    )

    print(
        "Open Veracity Data & Management "
        "to inspect the uploaded statements."
    )

    return 0


# ==========================================
# RUN PROGRAM
# ==========================================

if __name__ == "__main__":

    sys.exit(main())
