
import base64
import getpass
import json
import uuid

from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ==========================================
# STEP 1 - LRS Configuration
# ==========================================

DEFAULT_ENDPOINT = (
    "https://elearngrapher-test.lrs.io/xapi/"
)

XAPI_VERSION = "1.0.3"


# ==========================================
# STEP 2 - Send HTTP requests to LRS
# ==========================================

def request_lrs(method, url, username, password, payload=None):

    credentials = f"{username}:{password}"

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
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        url,
        data=body,
        headers=headers,
        method=method
    )

    try:
        with urlopen(request, timeout=25) as response:
            content = response.read().decode("utf-8")

            data = (
                json.loads(content)
                if content.strip()
                else None
            )

            return response.status, data

    except HTTPError as error:
        details = error.read().decode(
            "utf-8", errors="replace"
        )

        raise RuntimeError(
            f"HTTP {error.code}: {details[:800]}"
        ) from error

    except URLError as error:
        raise RuntimeError(
            f"Connection failed: {error.reason}"
        ) from error


# ==========================================
# STEP 3 - Create demo xAPI statements
# ==========================================

def create_statement(verb, activity, result):

    return {
        "id": str(uuid.uuid4()),

        "actor": {
            "objectType": "Agent",
            "account": {
                "homePage": "https://example.org/demo",
                "name": "demo-learner-001"
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
                "https://example.org/demo/activities/"
                + activity
            ),
            "definition": {
                "name": {
                    "en-US": activity
                }
            }
        },

        "result": result,

        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    }


# ==========================================
# STEP 4 - Main function
# ==========================================

def main():

    print("===== eLearnGrapher LRS TEST =====")

    endpoint = input(
        f"xAPI Endpoint [{DEFAULT_ENDPOINT}]: "
    ).strip() or DEFAULT_ENDPOINT

    endpoint = endpoint.rstrip("/") + "/"

    username = input("Access Key / Username: ").strip()

    password = getpass.getpass(
        "Access Secret / Password: "
    )

    if not username or not password:
        print("Missing API credentials.")
        return

    statements_url = endpoint + "statements"

    # Demo event 1: Page experienced
    page_statement = create_statement(
        "experienced",
        "demo-page-1",
        {
            "duration": "PT31S"
        }
    )

    # Demo event 2: Question answered
    question_statement = create_statement(
        "answered",
        "demo-question-1",
        {
            "duration": "PT45S",
            "response": "Demo answer",
            "score": {
                "raw": 1
            }
        }
    )

    statements = [
        page_statement,
        question_statement
    ]

    # ======================================
    # STEP 5 - Send statements
    # ======================================

    print("\nSending 2 xAPI statements...")

    try:
        status, response = request_lrs(
            "POST",
            statements_url,
            username,
            password,
            statements
        )

        print(f"POST HTTP status: {status}")

        if status not in (200, 204):
            print("Unexpected response:", response)
            return

        # ==================================
        # STEP 6 - Verify stored statements
        # ==================================

        verified = 0

        for statement in statements:

            url = statements_url + "?" + urlencode({
                "statementId": statement["id"]
            })

            get_status, stored = request_lrs(
                "GET",
                url,
                username,
                password
            )

            if (
                get_status == 200
                and isinstance(stored, dict)
                and stored.get("id") == statement["id"]
            ):
                verified += 1

                print(
                    "Verified:",
                    statement["verb"]["display"]["en-US"]
                )

            else:
                print(
                    "Verification failed:",
                    statement["id"]
                )

        print(
            f"\nVerified statements: {verified}/2"
        )

        if verified == 2:
            print("LRS TEST SUCCESSFUL!")

    except RuntimeError as error:

        print("LRS ERROR:", error)

        print(
            "Check endpoint, API credentials "
            "and Read/Write permissions."
        )


# ==========================================
# RUN PROGRAM
# ==========================================

if __name__ == "__main__":
    main()
