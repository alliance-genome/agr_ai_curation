import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from src.api import auth


class TraceReviewAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_service_bearer_token_authenticates_before_cognito(self):
        request = SimpleNamespace(
            headers={"authorization": "Bearer shared-service-token"},
            cookies={},
        )

        with patch.dict(
            auth.os.environ,
            {"TRACE_REVIEW_INTERNAL_API_TOKEN": "shared-service-token"},
            clear=False,
        ), patch("src.api.auth.is_dev_mode", return_value=False):
            user = await auth._get_user_from_cookie_impl(request)

        self.assertEqual(user["sub"], "trace-review-internal-service")
        self.assertEqual(user["token_use"], "internal_service")

    async def test_invalid_internal_service_bearer_token_is_rejected(self):
        request = SimpleNamespace(
            headers={"authorization": "Bearer wrong-token"},
            cookies={},
        )

        with patch.dict(
            auth.os.environ,
            {"TRACE_REVIEW_INTERNAL_API_TOKEN": "shared-service-token"},
            clear=False,
        ), patch("src.api.auth.is_dev_mode", return_value=False):
            with self.assertRaises(HTTPException) as context:
                await auth._get_user_from_cookie_impl(request)

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Invalid TraceReview service token.")


if __name__ == "__main__":
    unittest.main()
