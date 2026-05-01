"""Locust scenario for D6 perf testing — webhook ingestion path.

Run (D6):
  locust -f scripts/perf/webhook_locust.py --users=100 --spawn-rate=10 --run-time=30m \
    --host=http://localhost:8080
"""

import json

from locust import HttpUser, between, task  # type: ignore[import-not-found]


class WebhookUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(5)
    def post_ksm_webhook(self) -> None:
        payload = {
            "billId": "perf-test-bill",
            "noticeNum": "perf-notice",
            "subscribeNum": "perf-sub",
        }
        self.client.post(
            "/webhook/ksm?access_token=perf-token",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    @task(1)
    def health(self) -> None:
        self.client.get("/health")
