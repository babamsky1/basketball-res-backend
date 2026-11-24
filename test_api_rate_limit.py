import json
import urllib.request
import urllib.error

# Existing paid reservation; this test will not create a payment
API_URL = (
    "http://localhost:8000/api/reservations/"
    "3b76ac15-1852-405e-89e4-60de3bfd084f/checkout/"
)

# Use the correct customer credentials
payload = {
    "customer_email": "paidtest@gmail.com",
    "customer_phone": "09171234567",
}

# Prepare the request body
request_body = json.dumps(payload).encode("utf-8")

# Send 11 requests to test the 10-per-hour limit
for attempt in range(1, 12):

    request = urllib.request.Request(
        API_URL,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"Request {attempt}: HTTP {response.status}")

    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8")

        print(f"Request {attempt}: HTTP {error.code}")
        print("Response:", response_body)

    except Exception as error:
        print(f"Request {attempt}: ERROR - {error}")
