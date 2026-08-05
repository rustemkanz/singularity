import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import app_config


class AppConfigTests(unittest.TestCase):
    def test_parse_env_assignment_supports_export_and_quotes(self):
        self.assertEqual(
            app_config.parse_env_assignment('export AZURE_DEVOPS_PROJECT="Example Project"'),
            ("AZURE_DEVOPS_PROJECT", "Example Project"),
        )
        self.assertEqual(
            app_config.parse_env_assignment("AZURE_DEVOPS_USER='me@example.com'"),
            ("AZURE_DEVOPS_USER", "me@example.com"),
        )
        self.assertIsNone(app_config.parse_env_assignment("# comment"))
        self.assertIsNone(app_config.parse_env_assignment("not-an-assignment"))

    def test_load_local_env_defaults_uses_file_without_overriding_process_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env.local"
            env_file.write_text(
                "AZURE_DEVOPS_ORG=file-org\n"
                'export AZURE_DEVOPS_PROJECT="file-project"\n'
                "AZURE_DEVOPS_USER=file@example.com\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"AZURE_DEVOPS_ORG": "shell-org"}, clear=True):
                loaded_files = app_config.load_local_env_defaults(Path(tmp_dir), (".env.local",))

                self.assertEqual(loaded_files, [".env.local"])
                self.assertEqual(os.environ["AZURE_DEVOPS_ORG"], "shell-org")
                self.assertEqual(os.environ["AZURE_DEVOPS_PROJECT"], "file-project")
                self.assertEqual(os.environ["AZURE_DEVOPS_USER"], "file@example.com")

    def test_missing_required_config_reports_effective_missing_values(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            missing = app_config.missing_required_config(("AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT", "AZURE_DEVOPS_USER"))

        self.assertEqual(missing, ["AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT", "AZURE_DEVOPS_USER"])

    def test_configured_value_does_not_fall_back_to_legacy_ado_names(self):
        with mock.patch.dict(os.environ, {"ADO_ORG": "legacy-org"}, clear=True):
            value = app_config.configured_value("AZURE_DEVOPS_ORG")

        self.assertIsNone(value)

    def test_configuration_warnings_do_not_embed_example_placeholders(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            warnings = app_config.configuration_warnings(("AZURE_DEVOPS_TEAM_ID", "AZURE_DEVOPS_USER"))

        self.assertEqual(
            warnings,
            [
                "AZURE_DEVOPS_TEAM_ID is not set.",
                "AZURE_DEVOPS_USER is not set.",
            ],
        )

    def test_build_ssl_context_prefers_certifi_bundle_when_available(self):
        sentinel_context = mock.Mock()
        fake_certifi = mock.Mock()
        fake_certifi.where.return_value = "/tmp/certifi.pem"

        with mock.patch.dict(sys.modules, {"certifi": fake_certifi}):
            with mock.patch.object(app_config.importlib, "import_module", return_value=fake_certifi):
                with mock.patch.object(app_config.ssl, "create_default_context", return_value=sentinel_context) as create_default_context:
                    context = app_config.build_ssl_context()

        self.assertIs(context, sentinel_context)
        create_default_context.assert_called_once_with()
        sentinel_context.load_verify_locations.assert_called_once_with(cafile="/tmp/certifi.pem")


if __name__ == "__main__":
    unittest.main()
