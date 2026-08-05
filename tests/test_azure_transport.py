import io
import unittest
import urllib.error
from unittest import mock

from errors import CliError
from providers.azure_devops import auth as auth_module
from providers.azure_devops import http as http_module


class AzureTransportTests(unittest.TestCase):
    def test_api_text_with_headers_returns_plain_text(self):
        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"line one\nline two\n"
        fake_response.headers.items.return_value = [("Content-Type", "text/plain")]

        fake_opener = mock.Mock()
        fake_context = mock.MagicMock()
        fake_context.__enter__.return_value = fake_response
        fake_context.__exit__.return_value = False
        fake_opener.open.return_value = fake_context

        with mock.patch.object(http_module, "OPENER", fake_opener):
            body, headers = http_module.api_text_with_headers(
                "token",
                "GET",
                "https://example.test/log",
            )

        self.assertEqual(body, "line one\nline two\n")
        self.assertEqual(headers["Content-Type"], "text/plain")

    def test_get_token_raises_cli_error_with_probe_details(self):
        with mock.patch.object(
            auth_module,
            "probe_azure_token",
            return_value={
                "ok": False,
                "error": "Could not get Azure DevOps access token from Azure CLI.",
                "detail": "az: not logged in",
                "hints": ["Run 'az login'."],
            },
        ):
            with self.assertRaises(CliError) as exc:
                auth_module.get_token()

        self.assertIn("ERROR: Could not get Azure DevOps access token from Azure CLI.", str(exc.exception))
        self.assertIn("az: not logged in", str(exc.exception))
        self.assertIn("Hint: Run 'az login'.", str(exc.exception))

    def test_api_with_headers_raises_cli_error_for_http_404(self):
        response_body = io.BytesIO(b'{"message":"missing"}')
        http_error = urllib.error.HTTPError(
            url="https://example.test/resource",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=response_body,
        )

        fake_opener = mock.Mock()
        fake_opener.open.side_effect = http_error

        with mock.patch.object(http_module, "OPENER", fake_opener):
            with self.assertRaises(CliError) as exc:
                http_module.api_with_headers("token", "GET", "https://example.test/resource")

        self.assertIn("HTTP 404", str(exc.exception))
        self.assertIn("Hint: Check the selected project, repository, branch, or work item id.", str(exc.exception))


if __name__ == "__main__":
    unittest.main()