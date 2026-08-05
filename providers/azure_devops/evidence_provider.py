import urllib.error

from providers.azure_devops.work_item_context import (
    collect_attachment_references,
    default_download_dir,
    download_reference,
    open_with_default_app,
)
from providers.azure_devops.work_items import fetch_work_item, fetch_work_item_comments
from providers.interfaces import (
    EvidenceDownloadEntry,
    EvidenceDownloadResult,
    EvidenceProvider,
    EvidenceReference,
    WorkItemEvidenceSnapshot,
)


def _serialize_reference(reference: dict) -> EvidenceReference:
    return EvidenceReference(
        source=reference.get("source") or "",
        label=reference.get("label") or "",
        url=reference.get("url") or "",
        name=reference.get("name") or "",
        is_image=bool(reference.get("is_image")),
    )


def _raw_reference(reference: EvidenceReference) -> dict:
    return {
        "source": reference.source,
        "label": reference.label,
        "url": reference.url,
        "name": reference.name,
        "is_image": reference.is_image,
    }


class AzureDevOpsEvidenceProvider(EvidenceProvider):
    def __init__(self, token: str):
        self.token = token

    def get_work_item_evidence(self, *, item_id: int) -> WorkItemEvidenceSnapshot:
        item = fetch_work_item(self.token, item_id, expand="all")
        title = item.get("fields", {}).get("System.Title", "")
        comments = fetch_work_item_comments(self.token, item_id)
        references = collect_attachment_references(item, comments)
        return WorkItemEvidenceSnapshot(
            work_item_id=item_id,
            title=title,
            references=[_serialize_reference(reference) for reference in references],
        )

    def default_download_dir(self, *, item_id: int) -> str:
        return default_download_dir(item_id)

    def download_references(
        self,
        *,
        references: list[EvidenceReference],
        download_dir: str,
        open_after_download: bool,
    ) -> EvidenceDownloadResult:
        downloaded: list[EvidenceDownloadEntry] = []
        failures: list[str] = []
        downloaded_urls: dict[str, str] = {}
        opened = False
        open_error: str | None = None

        for index, reference in enumerate(references, start=1):
            try:
                target_path, reused = download_reference(
                    self.token,
                    _raw_reference(reference),
                    download_dir,
                    index,
                    downloaded_urls,
                )
                downloaded.append(EvidenceDownloadEntry(
                    label=reference.label,
                    url=reference.url,
                    path=target_path,
                    reused=reused,
                ))
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
                failures.append(f"{reference.label}: {exc}")

        if open_after_download and downloaded:
            opened, open_error = open_with_default_app(download_dir)

        return EvidenceDownloadResult(
            downloaded=downloaded,
            download_dir=download_dir if downloaded else None,
            failures=failures,
            opened=opened,
            open_error=open_error,
        )