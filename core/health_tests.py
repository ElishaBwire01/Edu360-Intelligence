from django.test import TestCase


class HealthEndpointTests(TestCase):
    def test_health_endpoint_returns_non_sensitive_status(self):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertNotIn("SECRET_KEY", response.json())