import os
import tempfile
import unittest
from unittest import mock

from providers.azure_devops import work_item_context as provider_work_item_context


class WorkItemContextTests(unittest.TestCase):
    def test_work_item_text_fields_include_repro_steps(self):
        self.assertIn(
            ("Microsoft.VSTS.TCM.ReproSteps", "Repro Steps"),
            provider_work_item_context.WORK_ITEM_TEXT_FIELDS,
        )

    def test_render_html_text_preserves_line_breaks_and_entities(self):
        html = (
            "<div>Open UAT<br/>Go to step</div>"
            "<ul><li>Check blanks &amp; screenshots</li><li>Confirm again</li></ul>"
        )

        self.assertEqual(
            provider_work_item_context.render_html_text(html),
            "Open UAT\nGo to step\n- Check blanks & screenshots\n- Confirm again",
        )

    def test_collect_attachment_references_scans_repro_steps(self):
        item = {
            "fields": {
                "Microsoft.VSTS.TCM.ReproSteps": (
                    '<p>See the screenshot <img src="https://example.test/image.png" /></p>'
                    '<a href="https://example.test/mockup.jpg">mock</a>'
                    ' https://example.test/inline.gif'
                )
            },
            "relations": [],
        }

        references = provider_work_item_context.collect_attachment_references(item, [])

        self.assertEqual(len(references), 3)
        self.assertEqual({reference["label"] for reference in references}, {"Repro Steps"})
        self.assertEqual(
            {reference["name"] for reference in references},
            {"image.png", "mockup.jpg", "inline.gif"},
        )
        self.assertTrue(all(reference["is_image"] for reference in references))

    def test_summarize_references_reports_preview_counts(self):
        references = [
            provider_work_item_context.build_reference(
                "field",
                "Repro Steps",
                "https://example.test/image.png",
                name="image.png",
                is_image=True,
            ),
            provider_work_item_context.build_reference(
                "relation",
                "AttachedFile",
                "https://example.test/spec.pdf",
                name="spec.pdf",
                is_image=False,
            ),
        ]

        summary = provider_work_item_context.summarize_references(references)

        self.assertEqual(
            summary,
            (
                "  Context Refs: 2 total (1 images, 1 files/links)",
                "  Preview     : [image] Repro Steps (image.png); [attachment] AttachedFile (spec.pdf)",
            ),
        )

    def test_parse_git_artifact_link_handles_pull_request(self):
        artifact = provider_work_item_context.parse_git_artifact_link(
            "vstfs:///Git/PullRequestId/project-guid%2Frepo-guid%2F58470"
        )

        self.assertEqual(
            artifact,
            {
                "kind": "pullRequest",
                "projectRef": "project-guid",
                "repoRef": "repo-guid",
                "pullRequestId": 58470,
            },
        )

    def test_parse_git_artifact_link_handles_commit(self):
        artifact = provider_work_item_context.parse_git_artifact_link(
            "vstfs:///Git/Commit/project-guid%2Frepo-guid%2Faed2ae263573573aa9efb98d5ff16ac5a64a5e52"
        )

        self.assertEqual(
            artifact,
            {
                "kind": "commit",
                "projectRef": "project-guid",
                "repoRef": "repo-guid",
                "commitId": "aed2ae263573573aa9efb98d5ff16ac5a64a5e52",
            },
        )

    def test_reference_summary_data_reports_counts_and_remaining(self):
        summary = provider_work_item_context.reference_summary_data(
            [
                provider_work_item_context.build_reference(
                    "field",
                    "Description",
                    "https://example.test/a.png",
                    name="a.png",
                    is_image=True,
                ),
                provider_work_item_context.build_reference(
                    "field",
                    "Description",
                    "https://example.test/b.png",
                    name="b.png",
                    is_image=True,
                ),
                provider_work_item_context.build_reference(
                    "relation",
                    "AttachedFile",
                    "https://example.test/spec.pdf",
                    name="spec.pdf",
                    is_image=False,
                ),
            ],
            limit=2,
        )

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["imageCount"], 2)
        self.assertEqual(summary["fileCount"], 1)
        self.assertEqual(summary["remaining"], 1)

    def test_download_reference_uses_shared_configured_opener(self):
        class FakeResponse:
            def __init__(self):
                self.headers = {"Content-Type": "image/png"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"png-bytes"

        class FakeOpener:
            def __init__(self):
                self.request = None

            def open(self, request):
                self.request = request
                return FakeResponse()

        reference = provider_work_item_context.build_reference(
            "field",
            "Description",
            "https://example.test/rendered-image",
            name="rendered-image",
            is_image=True,
        )
        opener = FakeOpener()

        with tempfile.TemporaryDirectory() as download_dir:
            with mock.patch.object(provider_work_item_context, "OPENER", opener):
                target_path, reused = provider_work_item_context.download_reference(
                    "token-123",
                    reference,
                    download_dir,
                    1,
                    {},
                )

            self.assertFalse(reused)
            self.assertTrue(target_path.endswith(".png"))
            self.assertTrue(os.path.exists(target_path))
            self.assertEqual(opener.request.get_header("Authorization"), "Bearer token-123")


if __name__ == "__main__":
    unittest.main()
