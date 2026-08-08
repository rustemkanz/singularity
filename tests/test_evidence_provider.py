import unittest
from unittest import mock

from providers.azure_devops import evidence_provider
from providers.azure_devops.work_item_context import DownloadBudget
from providers.interfaces import EvidenceReference


def reference(index: int) -> EvidenceReference:
    return EvidenceReference(
        source="field",
        label=f"Image {index}",
        url=f"https://dev.azure.com/example-org/project/image-{index}.png",
        name=f"image-{index}.png",
        is_image=True,
    )


class EvidenceProviderDownloadTests(unittest.TestCase):
    def test_reference_count_limit_fails_before_any_download(self):
        provider = evidence_provider.AzureDevOpsEvidenceProvider("token")
        references = [reference(index) for index in range(3)]

        with (
            mock.patch.object(evidence_provider, "MAX_DOWNLOAD_REFERENCES", 2),
            mock.patch.object(evidence_provider, "download_reference") as download_reference,
        ):
            result = provider.download_references(
                references=references,
                download_dir="artifacts",
                open_after_download=False,
            )

        download_reference.assert_not_called()
        self.assertEqual(result.downloaded, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Refusing to download 3 references", result.failures[0])

    def test_aggregate_limit_is_shared_and_stops_remaining_downloads(self):
        provider = evidence_provider.AzureDevOpsEvidenceProvider("token")
        references = [reference(index) for index in range(3)]
        budget = DownloadBudget(max_total_bytes=3)
        attempted = []

        def fake_download(token, raw_reference, download_dir, sequence, downloaded_urls, *, budget):
            attempted.append(sequence)
            budget.record_bytes(2)
            return f"artifact-{sequence}.png", False

        with (
            mock.patch.object(evidence_provider, "DownloadBudget", return_value=budget),
            mock.patch.object(evidence_provider, "download_reference", side_effect=fake_download),
        ):
            result = provider.download_references(
                references=references,
                download_dir="artifacts",
                open_after_download=False,
            )

        self.assertEqual(attempted, [1, 2])
        self.assertEqual(len(result.downloaded), 1)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("aggregate size limit", result.failures[0])


if __name__ == "__main__":
    unittest.main()
