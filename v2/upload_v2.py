
"""eLearnGrapher V2 - Safe Veracity LRS uploader."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import sys

from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from validate_xapi_v2 import validate


ROOT = Path(__file__).resolve().parent
DEFAULT_JSON = ROOT / "xapi_statements_v2.json"

OLD_V1_ENDPOINT = (
    "https://elearngrapher-test.lrs.io/xapi/"
)

XAPI_VERSION = "1.0.3"


# ============================================
# 1. LOAD AND VALIDATE
# ============================================

def read_statements(path):

    if not path.is_file():
        raise ValueError(
            f"File not found: {path}"
        )

    statements = json.loads(
        path.read_text(encoding="utf-8")
    )

    if (
        not isinstance(statements, list)
        or not statements
    ):
        raise ValueError(
            "JSON must contain a nonempty list"
        )

    report = validate(statements)

    if not report["passed"]:
        errors = "; ".join(
            report["errors"][:5]
        )
        raise ValueError(
            f"Local validation failed: {errors}"
        )

    return statements, report


# ============================================
# 2. CHECK LRS ENDPOINT
# ============================================

def check_endpoint(endpoint, allow_existing):

    endpoint = (
        endpoint.strip().rstrip("/") + "/"
    )

    parsed = urlparse(endpoint)

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "The endpoint must use HTTPS"
        )

    if not parsed.path.endswith("/xapi/"):
        raise ValueError(
            "Endpoint must end with /xapi/"
        )

    if (
        endpoint.lower() == OLD_V1_ENDPOINT
        and not allow_existing
    ):
        raise ValueError(
            "This is the existing V1 LRS. "
            "Create a separate V2 test LRS. "
            "Use --allow-existing-lrs only "
            "if mixing V1 and V2 is intentional."
        )

    return endpoint


# ============================================
# 3. COMMUNICATE WITH VERACITY
# ============================================

def call_lrs(
    method,
    url,
    username,
    secret,
    payload=None
):

    credentials = (
        f"{username}:{secret}".encode()
    )

    token = base64.b64encode(
        credentials
    ).decode("ascii")

    headers = {
        "Authorization": f"Basic {token}",
        "X-Experience-API-Version": XAPI_VERSION,
        "Accept": "application/json"
    }

    body = None

    if payload is not None:

        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False
        ).encode("utf-8")

        headers["Content-Type"] = (
            "application/json"
        )

    request = Request(
        url,
        data=body,
        headers=headers,
        method=method
    )

    try:

        with urlopen(
            request, timeout=40
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            data = (
                json.loads(raw)
                if raw.strip()
                else None
            )

            return response.status, data

    except HTTPError as error:

        details = error.read(300).decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"HTTP {error.code}: {details}"
        ) from error

    except URLError as error:

        raise RuntimeError(
            f"Connection error: {error.reason}"
        ) from error


# ============================================
# 4. COMPARE SENT AND RECEIVED DATA
# ============================================

def contains_expected(expected, actual):

    if isinstance(expected, dict):

        return (
            isinstance(actual, dict)
            and all(
                key in actual
                and contains_expected(
                    value,
                    actual[key]
                )
                for key, value in expected.items()
            )
        )

    if isinstance(expected, list):

        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(
                contains_expected(a, b)
                for a, b in zip(
                    expected, actual
                )
            )
        )

    return expected == actual


def verify_statement(
    statement,
    statements_url,
    username,
    secret
):

    query = urlencode({
        "statementId": statement["id"]
    })

    url = (
        statements_url + "?" + query
    )

    status, stored = call_lrs(
        "GET",
        url,
        username,
        secret
    )

    if (
        status != 200
        or not isinstance(stored, dict)
    ):
        raise RuntimeError(
            "Could not retrieve statement: "
            + statement["id"]
        )

    fields = (
        "id",
        "actor",
        "verb",
        "object",
        "result",
        "context"
    )

    for field in fields:

        if field not in statement:
            continue

        if not contains_expected(
            statement[field],
            stored.get(field)
        ):
            raise RuntimeError(
                f"Field '{field}' differs "
                f"for {statement['id']}"
            )

    if (
        "timestamp" in statement
        and statement["timestamp"]
        != stored.get("timestamp")
    ):
        raise RuntimeError(
            "Timestamp differs after retrieval"
        )


# ============================================
# 5. MAIN
# ============================================

def main():

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_JSON
    )

    parser.add_argument(
        "--endpoint"
    )

    parser.add_argument(
        "--limit",
        type=int
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=20
    )

    parser.add_argument(
        "--upload",
        action="store_true"
    )

    parser.add_argument(
        "--confirm-upload",
        action="store_true"
    )

    parser.add_argument(
        "--verify-all",
        action="store_true"
    )

    parser.add_argument(
        "--allow-existing-lrs",
        action="store_true"
    )

    args = parser.parse_args()

    if (
        args.limit is not None
        and args.limit < 1
    ):
        parser.error(
            "--limit must be positive"
        )

    if not 1 <= args.batch_size <= 100:
        parser.error(
            "--batch-size must be 1-100"
        )

    try:

        all_statements, report = (
            read_statements(args.input)
        )

    except (
        ValueError,
        OSError,
        json.JSONDecodeError
    ) as error:

        print("ERROR:", error)
        return 1

    statements = (
        all_statements[:args.limit]
        if args.limit
        else all_statements
    )

    verbs = Counter(
        s["verb"]["id"].rsplit("/", 1)[-1]
        for s in statements
    )

    unanswered = sum(
        s["verb"]["id"].endswith("/answered")
        and "response" not in s.get(
            "result", {}
        )
        for s in statements
    )

    print(
        "\n=== eLearnGrapher V2 Upload ==="
    )

    print("File:", args.input)

    print(
        "Available statements:",
        len(all_statements)
    )

    print(
        "Selected statements:",
        len(statements)
    )

    print("Verbs:", dict(verbs))
    print("Local validation: PASSED")

    print(
        "Questions without response:",
        unanswered
    )

    for warning in report["warnings"]:
        print("WARNING:", warning)

    # ----------------------------------------
    # DEFAULT: SIMULATION ONLY
    # ----------------------------------------

    if not args.upload:

        print(
            "\nDRY RUN: No connection to LRS."
        )

        print(
            "No statements were sent."
        )

        return 0

    # ----------------------------------------
    # EXPLICIT UPLOAD PERMISSION
    # ----------------------------------------

    if not args.confirm_upload:

        print(
            "Upload blocked. "
            "Use --confirm-upload."
        )

        return 1

    if not args.endpoint:

        print(
            "Upload blocked. "
            "A dedicated LRS endpoint is required."
        )

        return 1

    try:

        endpoint = check_endpoint(
            args.endpoint,
            args.allow_existing_lrs
        )

    except ValueError as error:

        print("Upload blocked:", error)
        return 1

    print(
        "\nWARNING: Real learner data "
        "will be sent to the LRS."
    )

    print("Destination:", endpoint)

    phrase = (
        f"ENVOYER V2 {len(statements)}"
    )

    confirmation = input(
        f"Type exactly '{phrase}': "
    ).strip()

    if confirmation != phrase:

        print(
            "Cancelled. Nothing was sent."
        )

        return 0

    username = input(
        "Access Key / Username: "
    ).strip()

    secret = getpass.getpass(
        "Access Secret / Password: "
    )

    if not username or not secret:

        print("Missing credentials.")
        return 1

    statements_url = (
        endpoint + "statements"
    )

    sent = 0
    verified = 0

    # ----------------------------------------
    # UPLOAD IN BATCHES
    # ----------------------------------------

    for offset in range(
        0,
        len(statements),
        args.batch_size
    ):

        batch = statements[
            offset:offset + args.batch_size
        ]

        try:

            status, response = call_lrs(
                "POST",
                statements_url,
                username,
                secret,
                batch
            )

            if status not in (200, 204):
                raise RuntimeError(
                    f"Unexpected POST status {status}"
                )

            if isinstance(response, list):

                expected_ids = {
                    s["id"] for s in batch
                }

                if set(response) != expected_ids:
                    raise RuntimeError(
                        "Returned IDs differ"
                    )

            sent += len(batch)

            print(
                f"POST HTTP {status}: "
                f"{sent}/{len(statements)}"
            )

            # Verify sent statements via GET

            if args.verify_all:

                to_verify = batch

            elif len(batch) == 1:

                to_verify = [batch[0]]

            else:

                to_verify = [
                    batch[0],
                    batch[-1]
                ]

            for statement in to_verify:

                verify_statement(
                    statement,
                    statements_url,
                    username,
                    secret
                )

                verified += 1

            print(
                "GET verified:", verified
            )

        except RuntimeError as error:

            print("LRS ERROR:", error)

            print(
                f"Stopped after {sent} "
                "POST-accepted statements. "
                "Check LRS before retrying."
            )

            return 1

    print(
        "\n=== UPLOAD COMPLETED ==="
    )

    print(
        "Statements sent:", sent
    )

    print(
        "Statements verified:", verified
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
