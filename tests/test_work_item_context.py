import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from providers.azure_devops import work_item_context as provider_work_item_context


def public_dns(hostname, port, *, type):
    return [(socket.AF_INET, type, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeDownloadResponse:
    def __init__(self, payload=b"png-bytes", headers=None, status=200):
        self.payload = payload
        self.headers = headers or {"Content-Type": "image/png"}
        self.status = status
        self.read_size = None
        self.read_sizes = []
        self.offset = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        self.read_size = size
        self.read_sizes.append(size)
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class CapturingDownloadOpener:
    def __init__(self, response=None):
        self.request = None
        self.timeout = None
        self.response = response or FakeDownloadResponse()

    def open(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class SequenceDownloadOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def open(self, request, *, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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

    def test_collect_attachment_references_rejects_excessive_html_matches(self):
        item = {
            "fields": {
                "System.Description": "".join(
                    f'<img src="https://example.test/image-{index}.png" />'
                    for index in range(3)
                )
            },
            "relations": [],
        }

        with self.assertRaisesRegex(
            provider_work_item_context.DownloadLimitError,
            "2-reference limit",
        ):
            provider_work_item_context.collect_attachment_references(
                item,
                [],
                max_references=2,
            )

    def test_html_reference_parser_stops_accumulating_at_limit(self):
        parser = provider_work_item_context.HtmlReferenceParser(max_references=2)

        with self.assertRaises(provider_work_item_context.DownloadLimitError):
            parser.feed(
                '<img src="https://example.test/one.png" />'
                '<a href="https://example.test/two.png">two</a>'
                '<img src="https://example.test/three.png" />'
            )

        self.assertEqual(len(parser.image_urls) + len(parser.link_urls), 2)

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

    def test_download_reference_uses_hardened_opener_and_authenticates_ado(self):
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
                mock.patch.object(provider_work_item_context, "DOWNLOAD_OPENER", opener),
                mock.patch.object(provider_work_item_context, "ORG", "example-org"),
                mock.patch.object(provider_work_item_context.socket, "getaddrinfo", side_effect=public_dns),
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
                opener.response.read_sizes[0],
                provider_work_item_context.DOWNLOAD_READ_CHUNK_BYTES,
            )

    def test_download_reference_denies_untrusted_urls_before_network_access(self):
        urls = (
            "https://external.example/image.png",
            "https://dev.azure.com.evil.example/example-org/image.png",
            "https://dev.azure.com/another-org/image.png",
            "http://dev.azure.com/example-org/image.png",
            "https://user@dev.azure.com/example-org/image.png",
            "https://dev.azure.com:8443/example-org/image.png",
            "https://dev.azure.com/example-org/../another-org/image.png",
            "https://dev.azure.com/example-org/%2e%2e/another-org/image.png",
            "https://127.0.0.1/image.png",
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
                        mock.patch.object(provider_work_item_context, "DOWNLOAD_OPENER", opener),
                        mock.patch.object(provider_work_item_context, "ORG", "example-org"),
                    ):
                        with self.assertRaises(ValueError):
                            provider_work_item_context.download_reference(
                                "token-123",
                                reference,
                                download_dir,
                                index,
                                {},
                            )

                    self.assertIsNone(opener.request)

    def test_explicit_external_origin_is_exact_and_downloaded_without_auth(self):
        opener = CapturingDownloadOpener()
        reference = provider_work_item_context.build_reference(
            "field",
            "Description",
            "https://media.example.com/image.png",
            name="image.png",
            is_image=True,
        )

        with tempfile.TemporaryDirectory() as download_dir:
            with (
                mock.patch.object(provider_work_item_context, "DOWNLOAD_OPENER", opener),
                mock.patch.object(
                    provider_work_item_context,
                    "AZURE_DEVOPS_EXTERNAL_MEDIA_ORIGINS",
                    ("https://media.example.com",),
                ),
                mock.patch.object(provider_work_item_context.socket, "getaddrinfo", side_effect=public_dns),
            ):
                provider_work_item_context.download_reference(
                    "token-123",
                    reference,
                    download_dir,
                    1,
                    {},
                )

        self.assertIsNone(opener.request.get_header("Authorization"))
        self.assertNotIn("Authorization", opener.request.unredirected_hdrs)

        suffix_trap = "https://media.example.com.evil.test/image.png"
        with self.assertRaisesRegex(ValueError, "not allowed"):
            provider_work_item_context.validate_media_download_url(
                suffix_trap,
                allowed_origins=("https://media.example.com",),
                resolver=public_dns,
            )

        mismatched_urls = (
            "http://media.example.com/image.png",
            "https://user@media.example.com/image.png",
            "https://media.example.com:8443/image.png",
        )
        for url in mismatched_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    provider_work_item_context.validate_media_download_url(
                        url,
                        allowed_origins=("https://media.example.com",),
                        resolver=public_dns,
                    )

    def test_media_validation_rejects_non_public_dns_results(self):
        blocked_addresses = (
            "127.0.0.1",
            "10.0.0.10",
            "169.254.169.254",
            "192.0.2.10",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fe80::1",
        )

        for address in blocked_addresses:
            with self.subTest(address=address):
                family = socket.AF_INET6 if ":" in address else socket.AF_INET

                def blocked_dns(hostname, port, *, type, address=address, family=family):
                    return [(family, type, socket.IPPROTO_TCP, "", (address, port))]

                with self.assertRaisesRegex(ValueError, "non-public address"):
                    provider_work_item_context.validate_media_download_url(
                        "https://media.example.com/image.png",
                        allowed_origins=("https://media.example.com",),
                        resolver=blocked_dns,
                    )

    def test_media_validation_rejects_mixed_public_and_private_dns_results(self):
        def mixed_dns(hostname, port, *, type):
            return [
                (socket.AF_INET, type, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
                (socket.AF_INET, type, socket.IPPROTO_TCP, "", ("10.0.0.10", port)),
            ]

        with self.assertRaisesRegex(ValueError, "non-public address"):
            provider_work_item_context.validate_media_download_url(
                "https://media.example.com/image.png",
                allowed_origins=("https://media.example.com",),
                resolver=mixed_dns,
            )

    def test_media_validation_rejects_ip_literals_and_dns_failure(self):
        with self.assertRaisesRegex(ValueError, "IP literal"):
            provider_work_item_context.validate_media_download_url(
                "https://8.8.8.8/image.png",
                allowed_origins=("https://8.8.8.8",),
                resolver=public_dns,
            )

        def failed_dns(hostname, port, *, type):
            raise socket.gaierror("not found")

        with self.assertRaisesRegex(ValueError, "Could not resolve"):
            provider_work_item_context.validate_media_download_url(
                "https://media.example.com/image.png",
                allowed_origins=("https://media.example.com",),
                resolver=failed_dns,
            )

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

    def test_azure_devops_auth_scope_rejects_path_scope_bypasses(self):
        deceptive_urls = (
            "https://dev.azure.com/example-org/../another-org/file.png",
            "https://dev.azure.com/example-org/%2e%2e/another-org/file.png",
            "https://dev.azure.com/example-org/%252e%252e/another-org/file.png",
            "https://dev.azure.com/example-org/%2Fanother-org/file.png",
            "https://dev.azure.com/example-org/%5Canother-org/file.png",
        )

        for url in deceptive_urls:
            with self.subTest(url=url):
                self.assertFalse(
                    provider_work_item_context.is_trusted_azure_devops_url(
                        url,
                        "example-org",
                    )
                )

    def test_redirect_revalidates_destination_and_drops_ado_authorization(self):
        redirect = FakeDownloadResponse(
            status=302,
            headers={"Location": "https://media.example.com/image.png"},
        )
        final = FakeDownloadResponse()
        opener = SequenceDownloadOpener((redirect, final))

        with mock.patch.object(provider_work_item_context, "ORG", "example-org"):
            response, final_url = provider_work_item_context.open_validated_download(
                "token-123",
                "https://dev.azure.com/example-org/project/_apis/wit/attachments/123",
                opener=opener,
                allowed_origins=("https://media.example.com",),
                resolver=public_dns,
            )

        self.assertIs(response, final)
        self.assertEqual(final_url, "https://media.example.com/image.png")
        self.assertTrue(redirect.closed)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer token-123")
        self.assertIsNone(opener.requests[1].get_header("Authorization"))
        self.assertNotIn("Authorization", opener.requests[1].unredirected_hdrs)

    def test_default_transport_pins_the_validated_dns_address(self):
        response = FakeDownloadResponse()
        with (
            mock.patch.object(provider_work_item_context, "ORG", "example-org"),
            mock.patch.object(
                provider_work_item_context,
                "open_pinned_https_request",
                return_value=response,
            ) as pinned_open,
        ):
            actual, final_url = provider_work_item_context.open_validated_download(
                "token-123",
                "https://dev.azure.com/example-org/project/image.png",
                resolver=public_dns,
            )

        self.assertIs(actual, response)
        self.assertEqual(final_url, "https://dev.azure.com/example-org/project/image.png")
        request, addresses = pinned_open.call_args.args
        self.assertEqual(addresses, ("93.184.216.34",))
        self.assertEqual(request.get_header("Authorization"), "Bearer token-123")

    def test_redirect_http_error_is_handled_as_a_validated_hop(self):
        redirect = urllib.error.HTTPError(
            "https://media.example.com/one.png",
            302,
            "Found",
            {"Location": "https://media.example.com/two.png"},
            None,
        )
        final = FakeDownloadResponse()
        opener = SequenceDownloadOpener((redirect, final))

        response, final_url = provider_work_item_context.open_validated_download(
            "token-123",
            "https://media.example.com/one.png",
            opener=opener,
            allowed_origins=("https://media.example.com",),
            resolver=public_dns,
        )

        self.assertIs(response, final)
        self.assertEqual(final_url, "https://media.example.com/two.png")
        self.assertEqual(len(opener.requests), 2)

    def test_redirect_to_unapproved_origin_is_rejected_before_second_request(self):
        redirect = FakeDownloadResponse(
            status=302,
            headers={"Location": "https://unapproved.example/image.png"},
        )
        opener = SequenceDownloadOpener((redirect, FakeDownloadResponse()))

        with mock.patch.object(provider_work_item_context, "ORG", "example-org"):
            with self.assertRaisesRegex(ValueError, "not allowed"):
                provider_work_item_context.open_validated_download(
                    "token-123",
                    "https://dev.azure.com/example-org/project/_apis/wit/attachments/123",
                    opener=opener,
                    allowed_origins=(),
                    resolver=public_dns,
                )

        self.assertEqual(len(opener.requests), 1)
        self.assertTrue(redirect.closed)

    def test_redirect_to_private_destination_is_rejected_before_second_request(self):
        redirect = FakeDownloadResponse(
            status=302,
            headers={"Location": "https://media.example.com/image.png"},
        )
        opener = SequenceDownloadOpener((redirect, FakeDownloadResponse()))

        def split_dns(hostname, port, *, type):
            address = "10.0.0.10" if hostname == "media.example.com" else "93.184.216.34"
            return [(socket.AF_INET, type, socket.IPPROTO_TCP, "", (address, port))]

        with mock.patch.object(provider_work_item_context, "ORG", "example-org"):
            with self.assertRaisesRegex(ValueError, "non-public address"):
                provider_work_item_context.open_validated_download(
                    "token-123",
                    "https://dev.azure.com/example-org/project/_apis/wit/attachments/123",
                    opener=opener,
                    allowed_origins=("https://media.example.com",),
                    resolver=split_dns,
                )

        self.assertEqual(len(opener.requests), 1)

    def test_redirect_loop_and_limit_are_bounded(self):
        loop_redirect = FakeDownloadResponse(
            status=302,
            headers={"Location": "https://media.example.com/start.png"},
        )
        loop_opener = SequenceDownloadOpener((loop_redirect,))
        with self.assertRaisesRegex(ValueError, "redirect loop"):
            provider_work_item_context.open_validated_download(
                "token-123",
                "https://media.example.com/start.png",
                opener=loop_opener,
                allowed_origins=("https://media.example.com",),
                resolver=public_dns,
            )

        limit_redirect = FakeDownloadResponse(
            status=302,
            headers={"Location": "https://media.example.com/two.png"},
        )
        limit_opener = SequenceDownloadOpener((limit_redirect,))
        with self.assertRaisesRegex(ValueError, "redirect limit"):
            provider_work_item_context.open_validated_download(
                "token-123",
                "https://media.example.com/one.png",
                opener=limit_opener,
                allowed_origins=("https://media.example.com",),
                resolver=public_dns,
                max_redirects=0,
            )

    def test_download_budget_caps_aggregate_bytes_across_responses(self):
        budget = provider_work_item_context.DownloadBudget(
            max_total_bytes=6,
            clock=FakeClock(),
        )
        first = FakeDownloadResponse(payload=b"1234")
        second = FakeDownloadResponse(payload=b"567")

        self.assertEqual(
            provider_work_item_context.read_bounded_download(first, budget=budget),
            b"1234",
        )
        with self.assertRaisesRegex(
            provider_work_item_context.DownloadLimitError,
            "aggregate size limit",
        ):
            provider_work_item_context.read_bounded_download(second, budget=budget)

        self.assertEqual(budget.bytes_received, 6)

    def test_download_budget_caps_validated_address_attempts(self):
        request = urllib.request.Request("https://media.example.com/image.png")
        budget = provider_work_item_context.DownloadBudget(
            max_address_attempts=2,
            clock=FakeClock(),
        )
        connection = mock.Mock()
        connection.request.side_effect = OSError("unreachable")

        with mock.patch.object(
            provider_work_item_context,
            "PinnedHTTPSConnection",
            return_value=connection,
        ) as connection_factory:
            with self.assertRaisesRegex(
                provider_work_item_context.DownloadLimitError,
                "address attempt limit",
            ):
                provider_work_item_context.open_pinned_https_request(
                    request,
                    ("93.184.216.34", "93.184.216.35", "93.184.216.36"),
                    budget=budget,
                )

        self.assertEqual(connection_factory.call_count, 2)

    def test_download_budget_enforces_total_monotonic_deadline_while_reading(self):
        clock = FakeClock()
        budget = provider_work_item_context.DownloadBudget(
            max_total_seconds=1,
            clock=clock,
        )
        response = FakeDownloadResponse(payload=b"payload")
        original_read = response.read

        def slow_read(size=-1):
            clock.advance(2)
            return original_read(size)

        response.read = slow_read
        with self.assertRaisesRegex(
            provider_work_item_context.DownloadLimitError,
            "wall-clock time limit",
        ):
            provider_work_item_context.read_bounded_download(response, budget=budget)

    def test_unique_artifact_creation_does_not_follow_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            download_dir = os.path.join(temp_dir, "artifacts")
            os.makedirs(download_dir)
            outside_path = os.path.join(temp_dir, "outside.png")
            symlink_path = os.path.join(download_dir, "image.png")
            try:
                os.symlink(outside_path, symlink_path)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Symlink creation is unavailable: {exc}")

            target_path = provider_work_item_context.write_unique_download(
                download_dir,
                "image.png",
                b"safe payload",
            )

            self.assertFalse(os.path.exists(outside_path))
            self.assertTrue(target_path.endswith("image-2.png"))
            with open(target_path, "rb") as handle:
                self.assertEqual(handle.read(), b"safe payload")

    def test_download_directory_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            actual_dir = os.path.join(temp_dir, "actual")
            symlink_dir = os.path.join(temp_dir, "linked")
            os.makedirs(actual_dir)
            try:
                os.symlink(actual_dir, symlink_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
                provider_work_item_context.write_unique_download(
                    symlink_dir,
                    "image.png",
                    b"payload",
                )

            self.assertEqual(os.listdir(actual_dir), [])

    def test_download_directory_rejects_symlinked_anchor_before_creating_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            actual_dir = os.path.join(temp_dir, "actual")
            symlink_dir = os.path.join(temp_dir, "linked")
            escaped_child = os.path.join(actual_dir, "new-child")
            os.makedirs(actual_dir)
            try:
                os.symlink(actual_dir, symlink_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
                provider_work_item_context.prepare_download_directory(
                    os.path.join(symlink_dir, "new-child")
                )

            self.assertFalse(os.path.exists(escaped_child))

    def test_download_directory_validates_existing_anchor_before_mkdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            anchor_dir = os.path.join(temp_dir, "anchor")
            os.makedirs(anchor_dir)
            requested_dir = os.path.join(anchor_dir, "new-child")
            original_realpath = os.path.realpath

            def redirected_realpath(path):
                if os.path.normcase(os.path.abspath(path)) == os.path.normcase(anchor_dir):
                    return os.path.join(temp_dir, "outside")
                return original_realpath(path)

            with (
                mock.patch.object(
                    provider_work_item_context.os.path,
                    "realpath",
                    side_effect=redirected_realpath,
                ),
                mock.patch.object(provider_work_item_context.os, "mkdir") as mkdir,
            ):
                with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
                    provider_work_item_context.prepare_download_directory(requested_dir)

            mkdir.assert_not_called()

    def test_artifact_creation_is_exclusive_and_containment_checked(self):
        with tempfile.TemporaryDirectory() as download_dir:
            original_path = os.path.join(download_dir, "image.png")
            with open(original_path, "wb") as handle:
                handle.write(b"original")

            target_path = provider_work_item_context.write_unique_download(
                download_dir,
                "image.png",
                b"new",
            )
            with open(original_path, "rb") as handle:
                self.assertEqual(handle.read(), b"original")
            self.assertTrue(target_path.endswith("image-2.png"))

            with self.assertRaisesRegex(ValueError, "escapes"):
                provider_work_item_context.write_unique_download(
                    download_dir,
                    "../outside.png",
                    b"payload",
                )

    def test_download_rejects_payload_larger_than_limit(self):
        response = FakeDownloadResponse(
            payload=b"12345",
            headers={"Content-Type": "image/png"},
        )

        with self.assertRaisesRegex(ValueError, "size limit"):
            provider_work_item_context.read_bounded_download(response, max_bytes=4)


if __name__ == "__main__":
    unittest.main()
