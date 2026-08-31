import os
import tempfile
import unittest
from unittest import mock

import profiles


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self._config_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._config_home.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self._config_home.name}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SG_PROFILE", None)
        profiles.profiles_dir().mkdir(parents=True, exist_ok=True)

    def _write_profile(self, name: str, body: str = "AZURE_DEVOPS_ORG=x\n") -> None:
        profiles.profile_env_file(name).write_text(body, encoding="utf-8")

    def test_name_validation(self):
        self.assertTrue(profiles.is_valid_profile_name("web-eu-2"))
        for bad in ("", "../etc", "a/b", "x" * 65, "with space"):
            self.assertFalse(profiles.is_valid_profile_name(bad))

    def test_list_profiles(self):
        self._write_profile("web-eu")
        self._write_profile("api-eu")
        self.assertEqual(profiles.list_profiles(), ["api-eu", "web-eu"])

    def test_env_var_takes_precedence_over_marker_file(self):
        self._write_profile("web-eu")
        self._write_profile("api-eu")
        profiles.set_active_profile("api-eu")
        with mock.patch.dict(os.environ, {"SG_PROFILE": "web-eu"}):
            self.assertEqual(profiles.resolve_active_profile(), ("web-eu", "env"))
        self.assertEqual(profiles.resolve_active_profile(), ("api-eu", "file"))

    def test_invalid_env_var_is_ignored_with_warning(self):
        with mock.patch.dict(os.environ, {"SG_PROFILE": "../x"}), mock.patch("sys.stderr"):
            self.assertEqual(profiles.resolve_active_profile(), (None, None))

    def test_set_and_clear_active_profile(self):
        self._write_profile("web-eu")
        profiles.set_active_profile("web-eu")
        self.assertEqual(profiles.resolve_active_profile(), ("web-eu", "file"))
        profiles.set_active_profile(None)
        self.assertEqual(profiles.resolve_active_profile(), (None, None))

    def test_set_active_profile_rejects_bad_name(self):
        with self.assertRaises(ValueError):
            profiles.set_active_profile("../evil")


class ApplyActiveProfileEnvTests(unittest.TestCase):
    def test_profile_env_layers_under_real_env_over_absent_keys(self):
        import app_config

        with tempfile.TemporaryDirectory() as config_home:
            with mock.patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": config_home,
                    "SG_PROFILE": "demo",
                    "SG_PROFILE_TEST_PRESET": "real-value",
                },
                clear=False,
            ):
                os.environ.pop("SG_PROFILE_TEST_GAP", None)
                profiles.profiles_dir().mkdir(parents=True, exist_ok=True)
                profiles.profile_env_file("demo").write_text(
                    "SG_PROFILE_TEST_PRESET=profile-value\nSG_PROFILE_TEST_GAP=profile-gap\n",
                    encoding="utf-8",
                )
                name, source = app_config.apply_active_profile_env()

                self.assertEqual((name, source), ("demo", "env"))
                self.assertEqual(os.environ["SG_PROFILE_TEST_PRESET"], "real-value")  # real env wins
                self.assertEqual(os.environ["SG_PROFILE_TEST_GAP"], "profile-gap")  # profile fills the gap


if __name__ == "__main__":
    unittest.main()
