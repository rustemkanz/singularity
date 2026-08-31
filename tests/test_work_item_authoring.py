import json
import os
import tempfile
import unittest

from errors import CliError
from work_item_authoring import (
    html_to_summary,
    markdown_to_html,
    parse_item_specs,
)


def _write(tmpdir: str, name: str, text: str) -> str:
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class MarkdownToHtmlTests(unittest.TestCase):
    def test_paragraphs_bullets_and_inline(self):
        html = markdown_to_html(
            "First para line one\nline two\n\n"
            "- item **one**\n- item `two`\n\n"
            "Trailing para <script>"
        )
        self.assertIn("<p>First para line one<br>line two</p>", html)
        self.assertIn("<ul><li>item <strong>one</strong></li><li>item <code>two</code></li></ul>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_subheading_becomes_bold_paragraph(self):
        self.assertIn("<p><strong>Acceptance criteria</strong></p>", markdown_to_html("### Acceptance criteria"))

    def test_empty_input(self):
        self.assertEqual(markdown_to_html(""), "")


class ParseMarkdownPlanTests(unittest.TestCase):
    def test_sections_titles_types_and_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write(
                tmpdir,
                "plan.md",
                "# Backfill\n\nIntro text ignored\n\n"
                "## P1 — Wire the toggle [Bug]\n\nRepro:\n- open modal\n\n"
                "## Add the settings page\n\nBuild the page.\n",
            )
            specs = parse_item_specs(path, default_type="User Story")

        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].type, "Bug")
        self.assertEqual(specs[0].title, "Wire the toggle")
        self.assertIn("<li>open modal</li>", specs[0].description_html)
        self.assertEqual(specs[1].type, "User Story")
        self.assertEqual(specs[1].title, "Add the settings page")

    def test_no_sections_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write(tmpdir, "plan.md", "# Title only\n\nsome prose\n")
            with self.assertRaisesRegex(CliError, "No '## <title>' sections"):
                parse_item_specs(path, default_type="User Story")

    def test_unrecognized_bracket_token_stays_in_the_title(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write(tmpdir, "plan.md", "## Something [Nonsense]\n\nbody\n")
            specs = parse_item_specs(path, default_type="User Story")
        self.assertEqual(specs[0].title, "Something [Nonsense]")
        self.assertEqual(specs[0].type, "User Story")

    def test_unknown_type_in_json_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write(tmpdir, "plan.json", json.dumps({"items": [{"title": "X", "type": "Nonsense"}]}))
            with self.assertRaisesRegex(CliError, "Unsupported work-item type"):
                parse_item_specs(path, default_type="User Story")


class ParseJsonPlanTests(unittest.TestCase):
    def test_items_with_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write(
                tmpdir,
                "plan.json",
                json.dumps({
                    "items": [
                        {"title": "One", "description": "hello **world**"},
                        {"title": "Two", "type": "bug", "tags": ["x", "y"], "assignedTo": "qa@example.com"},
                        {"title": "Three", "descriptionHtml": "<p>raw</p>"},
                    ]
                }),
            )
            specs = parse_item_specs(path, default_type="Task")

        self.assertEqual([s.type for s in specs], ["Task", "Bug", "Task"])
        self.assertIn("<strong>world</strong>", specs[0].description_html)
        self.assertEqual(specs[1].tags, ("x", "y"))
        self.assertEqual(specs[1].assigned_to, "qa@example.com")
        self.assertEqual(specs[2].description_html, "<p>raw</p>")

    def test_missing_items_key_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write(tmpdir, "plan.json", json.dumps({"nope": []}))
            with self.assertRaises(CliError):
                parse_item_specs(path, default_type="Task")


class HtmlToSummaryTests(unittest.TestCase):
    def test_strips_tags_and_truncates(self):
        self.assertEqual(html_to_summary("<p>Hello <strong>there</strong></p>"), "Hello there")
        self.assertTrue(html_to_summary("<p>" + "x" * 400 + "</p>").endswith("…"))


if __name__ == "__main__":
    unittest.main()
