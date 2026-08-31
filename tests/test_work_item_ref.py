import argparse
import unittest

from work_item_ref import parse_work_item_reference, work_item_id_arg


class ParseWorkItemReferenceTests(unittest.TestCase):
    def test_bare_id(self):
        self.assertEqual(parse_work_item_reference("812345"), 812345)
        self.assertEqual(parse_work_item_reference("  42 "), 42)

    def test_azure_devops_edit_url(self):
        url = "https://dev.azure.com/contoso/Widgets/_workitems/edit/812345"
        self.assertEqual(parse_work_item_reference(url), 812345)

    def test_azure_devops_edit_url_with_trailing_path_and_query(self):
        url = "https://dev.azure.com/contoso/Widgets/_workitems/edit/812345/?triage=true#comment"
        self.assertEqual(parse_work_item_reference(url), 812345)

    def test_visualstudio_host(self):
        url = "https://contoso.visualstudio.com/Widgets/_workitems/edit/17"
        self.assertEqual(parse_work_item_reference(url), 17)

    def test_query_id_form(self):
        url = "https://dev.azure.com/contoso/Widgets/_workitems?id=555&_a=edit"
        self.assertEqual(parse_work_item_reference(url), 555)

    def test_gitlab_issue_url(self):
        url = "https://gitlab.example.com/group/project/-/issues/88"
        self.assertEqual(parse_work_item_reference(url), 88)

    def test_rejects_zero_and_negative(self):
        for value in ("0", "-3"):
            with self.assertRaises(ValueError):
                parse_work_item_reference(value)

    def test_rejects_unrelated_text_and_urls(self):
        for value in ("", "not-an-id", "https://example.com/nothing/here", "ftp://x/y/1"):
            with self.assertRaises(ValueError):
                parse_work_item_reference(value)

    def test_argparse_wrapper_raises_argument_type_error(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            work_item_id_arg("nope")
        self.assertEqual(work_item_id_arg("7"), 7)


if __name__ == "__main__":
    unittest.main()
