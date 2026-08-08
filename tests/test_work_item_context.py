import os
import tempfile
import unittest
import urllib.request
from unittest import mock

from providers.azure_devops import work_item_context as provider_work_item_context


class FakeDownloadResponse:
    def __init__(self, payload=b"png-bytes", headers=None):
        self.payload = payload
        self.headers = headers or {"Content-Type": "image/png"}
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        self.read_size = size
        return self.payload


class CapturingDownloadOpener:
    def __init__(self, response=None):
        self.request = None
        self.timeout = None
        self.response = response or FakeDownloadResponse()

    def open(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


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
        reference = provider_work_item_context.build_reference(
            "field",
            "Description",
            "https://dev.azure.com/example-org/example-project/_apis/wit/attachments/123",
            name="rendered-image",
            is_image=True,
        )
        opener = CapturingDownloadOpener()

        with tempfile.TemporaryDirectory() as download_dir:
            with (
                mock.patch.object(provider_work_item_context, "OPENER", opener),
                mock.patch.object(provider_work_item_context, "ORG", "example-org"),
            ):
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
            self.assertNotIn("Authorization", opener.request.headers)
            self.assertEqual(
                opener.request.unredirected_hdrs["Authorization"],
                "Bearer token-123",
            )
            self.assertEqual(opener.timeout, provider_work_item_context.DOWNLOAD_TIMEOUT_SECONDS)
            self.assertEqual(
                opener.response.read_size,
                provider_work_item_context.MAX_DOWNLOAD_BYTES + 1,
            )

    def test_download_reference_never_authenticates_untrusted_urls(self):
        urls = (
            "https://external.example/image.png",
            "https://dev.azure.com.evil.example/example-org/image.png",
            "https://dev.azure.com/another-org/image.png",
            "http://dev.azure.com/example-org/image.png",
            "https://user@dev.azure.com/example-org/image.png",
            "https://dev.azure.com:8443/example-org/image.png",
        )

        with tempfile.TemporaryDirectory() as download_dir:
            for index, url in enumerate(urls, start=1):
                with self.subTest(url=url):
                    opener = CapturingDownloadOpener()
                    reference = provider_work_item_context.build_reference(
                        "field",
                        "Description",
                        url,
                        name=f"image-{index}.png",
                        is_image=True,
                    )
                    with (
                        mock.patch.object(provider_work_item_context, "OPENER", opener),
                        mock.patch.object(provider_work_item_context, "ORG", "example-org"),
                    ):
                        provider_work_item_context.download_reference(
                            "token-123",
                            reference,
                            download_dir,
                            index,
                            {},
                        )

                    self.assertIsNone(opener.request.get_header("Authorization"))
                    self.assertNotIn("Authorization", opener.request.unredirected_hdrs)

    def test_azure_devops_auth_scope_accepts_exact_modern_and_legacy_urls(self):
        trusted_urls = (
            "https://dev.azure.com/example-org/project/_apis/wit/attachments/123",
            "https://dev.azure.com:443/EXAMPLE-ORG/project/file.png",
            "https://dev.azure.com/example%2Dorg/project/file.png",
            "https://example-org.visualstudio.com/project/_apis/wit/attachments/123",
        )

        for url in trusted_urls:
            with self.subTest(url=url):
                self.assertTrue(
                    provider_work_item_context.is_trusted_azure_devops_url(
                        url,
                        "example-org",
                    )
                )

    def test_redirect_does_not_copy_azure_devops_authorization(self):
        reference = provider_work_item_context.build_reference(
            "field",
            "Description",
            "https://dev.azure.com/example-org/project/_apis/wit/attachments/123",
            name="image.png",
            is_image=True,
        )
        opener = CapturingDownloadOpener()

        with tempfile.TemporaryDirectory() as download_dir:
            with (
                mock.patch.object(provider_work_item_context, "OPENER", opener),
                mock.patch.object(provider_work_item_context, "ORG", "example-org"),
            ):
                provider_work_item_context.download_reference(
                    "token-123",
                    reference,
                    download_dir,
                    1,
                    {},
                )

        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            opener.request,
            None,
            302,
            "Found",
            {},
            "https://external.example/image.png",
        )

        self.assertEqual(opener.request.get_header("Authorization"), "Bearer token-123")
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_download_rejects_payload_larger_than_limit(self):
        response = FakeDownloadResponse(
            payload=b"12345",
            headers={"Content-Type": "image/png"},
        )

        with self.assertRaisesRegex(ValueError, "size limit"):
            provider_work_item_context.read_bounded_download(response, max_bytes=4)


if __name__ == "__main__":
    unittest.main()
