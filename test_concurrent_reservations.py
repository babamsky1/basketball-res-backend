import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

# API endpoint
API_URL = "http://localhost:8000/api/create-reservations/"

# Both requests will attempt to book this exact schedule
payload = {
    "customer_name": "Concurrency Test",
    "customer_email": "concurrency@gmail.com",
    "customer_phone": "09171234567",
    "reservation_date": "2026-11-30",
    "start_time": "15:00",
    "end_time": "16:00",
}

# Synchronize both requests so they start together
start_barrier = Barrier(2)


def send_booking():
    # Wait until both threads are ready
    start_barrier.wait()

    # Prepare the POST request
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        # Send the booking request
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")

    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")

    except Exception as error:
        return "ERROR", str(error)


# Execute two requests concurrently
with ThreadPoolExecutor(max_workers=2) as executor:
    results = list(executor.map(lambda _: send_booking(), range(2)))

# Display both responses
for index, (status_code, response_body) in enumerate(results, start=1):
    print(f"\nRequest {index}")
    print(f"Status: {status_code}")
    print(f"Response: {response_body}")
