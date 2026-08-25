"""Provider-neutral, immutable plans for approval-gated mutations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from typing import Any

from errors import CliError


MUTATION_PLAN_VERSION = 1
PLAN_ID_PREFIX = "sha256:"
PLAN_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
PLAN_APPROVAL_TTL_SECONDS = 60 * 60
PLAN_STORE_ENVIRONMENT_VARIABLE = "SG_MUTATION_PLAN_STORE"
_CONSUMED_PLAN_IDS: set[str] = set()


class MutationPlanError(CliError):
    """Raised when a mutation plan cannot be represented safely."""


class PlanApprovalError(CliError):
    """Raised when an apply request does not match its previewed plan."""


def _normalize_json(value: Any, *, path: str) -> Any:
    """Return a detached JSON value or reject ambiguous/non-finite input."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MutationPlanError(f"ERROR: Mutation plan {path} contains a non-finite number.")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MutationPlanError(
                    f"ERROR: Mutation plan {path} contains a non-string object key: {key!r}."
                )
            normalized[key] = _normalize_json(item, path=f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _normalize_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise MutationPlanError(
        f"ERROR: Mutation plan {path} contains a non-JSON value of type {type(value).__name__}."
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _git_checkout_identity(path: str) -> dict[str, str] | None:
    def git_output(*arguments: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", path, *arguments],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    top_level = git_output("rev-parse", "--show-toplevel")
    git_directory = git_output("rev-parse", "--absolute-git-dir")
    if not top_level or not git_directory:
        return None
    origin = git_output("remote", "get-url", "origin")
    identity = {
        "kind": "git",
        "topLevel": os.path.realpath(top_level),
        "gitDirectory": os.path.realpath(git_directory),
    }
    if origin:
        identity["originHash"] = hashlib.sha256(origin.encode("utf-8")).hexdigest()
    return identity


def resolve_checkout_identity(path: str | None = None) -> dict[str, str]:
    """Normalize a cwd to its Git worktree identity, or a plain directory."""
    resolved_path = os.path.realpath(path or os.getcwd())
    return _git_checkout_identity(resolved_path) or {
        "kind": "directory",
        "path": resolved_path,
    }


def checkout_identity_path(identity: Mapping[str, str]) -> str:
    return identity.get("topLevel") or identity.get("path") or ""


@dataclass(frozen=True, slots=True, init=False)
class MutationPlan:
    """An immutable snapshot of one exact external mutation.

    ``canonical_json`` is the hash input. ``envelope`` returns a fresh copy so
    callers cannot mutate the plan after it has been previewed and approved.
    """

    _canonical_json_value: str
    _semantic_canonical_json_value: str
    _plan_id: str

    def __init__(
        self,
        action: str,
        target: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        nonce: str | None = None,
        issued_at: int | None = None,
        checkout: str | None = None,
        ttl_seconds: int = PLAN_APPROVAL_TTL_SECONDS,
    ) -> None:
        if not isinstance(action, str) or not action.strip():
            raise MutationPlanError("ERROR: Mutation plan action must be a non-empty string.")
        if not isinstance(target, Mapping):
            raise MutationPlanError("ERROR: Mutation plan target must be a JSON object.")
        if not isinstance(payload, Mapping):
            raise MutationPlanError("ERROR: Mutation plan payload must be a JSON object.")
        if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise MutationPlanError("ERROR: Mutation plan approval lifetime must be a positive integer.")

        normalized_nonce = nonce or secrets.token_hex(16)
        if not isinstance(normalized_nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", normalized_nonce):
            raise MutationPlanError("ERROR: Mutation plan nonce must contain 32 lowercase hexadecimal characters.")
        normalized_issued_at = int(time.time()) if issued_at is None else issued_at
        if not isinstance(normalized_issued_at, int) or normalized_issued_at < 0:
            raise MutationPlanError("ERROR: Mutation plan issue time must be a non-negative integer.")
        checkout_identity = (
            {"kind": "directory", "path": os.path.realpath(checkout)}
            if checkout is not None
            else resolve_checkout_identity()
        )
        normalized_checkout = checkout_identity_path(checkout_identity)

        semantic_envelope = {
            "version": MUTATION_PLAN_VERSION,
            "action": action,
            "target": _normalize_json(target, path="target"),
            "payload": _normalize_json(payload, path="payload"),
        }
        envelope = {
            **semantic_envelope,
            "approval": {
                "nonce": normalized_nonce,
                "issuedAtUnix": normalized_issued_at,
                "expiresAtUnix": normalized_issued_at + ttl_seconds,
                "checkout": normalized_checkout,
                "checkoutIdentity": checkout_identity,
            },
        }
        canonical = _canonical_json(envelope)
        semantic_canonical = _canonical_json(semantic_envelope)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        object.__setattr__(self, "_canonical_json_value", canonical)
        object.__setattr__(self, "_semantic_canonical_json_value", semantic_canonical)
        object.__setattr__(self, "_plan_id", f"{PLAN_ID_PREFIX}{digest}")

    @property
    def version(self) -> int:
        return MUTATION_PLAN_VERSION

    @property
    def action(self) -> str:
        return self.envelope["action"]

    @property
    def target(self) -> dict[str, Any]:
        return self.envelope["target"]

    @property
    def payload(self) -> dict[str, Any]:
        return self.envelope["payload"]

    @property
    def approval(self) -> dict[str, Any]:
        return self.envelope["approval"]

    @property
    def envelope(self) -> dict[str, Any]:
        """Return a detached version/action/target/payload plan envelope."""
        return json.loads(self._canonical_json_value)

    @property
    def canonical_json(self) -> str:
        """Return the compact, sorted UTF-8 JSON text used to derive the ID."""
        return self._canonical_json_value

    @property
    def semantic_canonical_json(self) -> str:
        """Return the canonical action/target/payload used for apply-time comparison."""
        return self._semantic_canonical_json_value

    @property
    def plan_id(self) -> str:
        return self._plan_id

    def to_preview_dict(self) -> dict[str, Any]:
        """Return the plan plus its approval token for structured output."""
        return {
            "planId": self.plan_id,
            "applyArgument": f"--apply {self.plan_id}",
            "expiresAtUnix": self.approval["expiresAtUnix"],
            "plan": self.envelope,
        }


def _plan_store_root() -> str:
    store_root = os.environ.get(PLAN_STORE_ENVIRONMENT_VARIABLE)
    if store_root:
        return os.path.abspath(store_root)
    if hasattr(os, "getuid"):
        user_suffix = str(os.getuid())
    else:
        username = os.environ.get("USERNAME", "user")
        user_suffix = hashlib.sha256(username.encode("utf-8")).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), f"singularity-mutation-plans-{user_suffix}")


def _plan_store_directory(checkout: str) -> str:
    root = _plan_store_root()
    checkout_key = hashlib.sha256(os.path.normcase(checkout).encode("utf-8")).hexdigest()[:24]
    return os.path.join(root, checkout_key)


def _validate_private_store_directory(path: str) -> None:
    absolute_path = os.path.abspath(path)
    if not os.path.isdir(absolute_path) or os.path.islink(absolute_path):
        raise PlanApprovalError("ERROR: Mutation plan store is not a safe directory.")
    if os.name != "nt":
        metadata = os.stat(absolute_path, follow_symlinks=False)
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise PlanApprovalError("ERROR: Mutation plan store is not owned by the current user.")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PlanApprovalError("ERROR: Mutation plan store permissions must be private (0700).")


def _ensure_plan_store(checkout: str) -> str:
    store_root = _plan_store_root()
    try:
        os.makedirs(store_root, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise PlanApprovalError("ERROR: Could not create the mutation plan store.") from exc
    _validate_private_store_directory(store_root)

    store_dir = _plan_store_directory(checkout)
    try:
        os.mkdir(store_dir, mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PlanApprovalError("ERROR: Could not create the checkout mutation plan store.") from exc
    _validate_private_store_directory(store_dir)
    return store_dir


def _plan_record_path(plan_id: str, checkout: str, *, state: str) -> str:
    digest = plan_id.removeprefix(PLAN_ID_PREFIX)
    return os.path.join(_ensure_plan_store(checkout), f"{digest}.{state}.json")


def _read_plan_record(path: str) -> dict[str, Any]:
    if os.path.islink(path):
        raise PlanApprovalError("ERROR: Mutation plan record is a symbolic link and was rejected.")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanApprovalError("ERROR: Mutation plan record is unreadable or corrupt.") from exc
    if not isinstance(record, dict):
        raise PlanApprovalError("ERROR: Mutation plan record is invalid.")
    return record


def _prune_plan_store(store_dir: str) -> None:
    """Remove expired/corrupt pending records and old payload-free markers."""
    now = time.time()
    try:
        entries = list(os.scandir(store_dir))
    except OSError:
        return
    used_digests = {
        entry.name.removesuffix(".used.json")
        for entry in entries
        if entry.is_file(follow_symlinks=False) and entry.name.endswith(".used.json")
    }
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        should_remove = False
        if entry.name.endswith(".pending.json"):
            digest = entry.name.removesuffix(".pending.json")
            if digest in used_digests:
                should_remove = True
            else:
                try:
                    record = _read_plan_record(entry.path)
                    expires_at = record.get("expiresAtUnix")
                    should_remove = isinstance(expires_at, int) and now > expires_at
                except PlanApprovalError:
                    try:
                        should_remove = now - entry.stat(follow_symlinks=False).st_mtime > PLAN_APPROVAL_TTL_SECONDS
                    except OSError:
                        pass
        elif entry.name.endswith(".used.json"):
            try:
                should_remove = now - entry.stat(follow_symlinks=False).st_mtime > 24 * 60 * 60
            except OSError:
                pass
        if should_remove:
            try:
                os.unlink(entry.path)
            except OSError:
                pass


def register_plan_preview(plan: MutationPlan) -> None:
    """Persist a short-lived, Git-worktree-identity-bound, one-shot approval record."""
    checkout = plan.approval["checkout"]
    current_identity = resolve_checkout_identity()
    if current_identity != plan.approval.get("checkoutIdentity"):
        raise PlanApprovalError("ERROR: Mutation plans must be previewed from their bound checkout.")
    pending_path = _plan_record_path(plan.plan_id, checkout, state="pending")
    used_path = _plan_record_path(plan.plan_id, checkout, state="used")
    _prune_plan_store(os.path.dirname(pending_path))
    if os.path.exists(used_path):
        raise PlanApprovalError("ERROR: This mutation plan has already been used; create a fresh preview.")
    record = {
        "planId": plan.plan_id,
        "semanticSha256": hashlib.sha256(
            plan.semantic_canonical_json.encode("utf-8")
        ).hexdigest(),
        "approval": plan.approval,
        "checkout": checkout,
        "expiresAtUnix": plan.approval["expiresAtUnix"],
    }
    encoded_record = (_canonical_json(record) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(pending_path, flags, 0o600)
    except FileExistsError:
        existing = _read_plan_record(pending_path)
        if existing != record:
            raise PlanApprovalError("ERROR: Mutation plan record collision detected.")
        return
    except OSError as exc:
        raise PlanApprovalError("ERROR: Could not persist the mutation plan approval record.") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded_record)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.unlink(pending_path)
        except OSError:
            pass
        raise


def _create_used_record(path: str, plan_id: str) -> None:
    """Atomically claim a Plan ID with a payload-free replay marker."""
    marker = (_canonical_json({
        "planId": plan_id,
        "usedAtUnix": int(time.time()),
    }) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(marker)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def require_approved_plan(plan: MutationPlan, apply_plan_id: str | None) -> bool:
    """Return whether to mutate, rejecting an apply ID for any other plan.

    ``None`` is preview mode. The caller must invoke its mutation only when this
    function returns ``True``.
    """
    if apply_plan_id is None:
        return False
    if not isinstance(apply_plan_id, str) or PLAN_ID_PATTERN.fullmatch(apply_plan_id) is None:
        raise PlanApprovalError(
            "ERROR: --apply Plan ID does not match the required lowercase sha256 format from a fresh preview."
        )

    current_identity = resolve_checkout_identity()
    checkout = checkout_identity_path(current_identity)
    if current_identity != plan.approval.get("checkoutIdentity"):
        raise PlanApprovalError("ERROR: The current Git checkout identity changed while planning.")
    pending_path = _plan_record_path(apply_plan_id, checkout, state="pending")
    used_path = _plan_record_path(apply_plan_id, checkout, state="used")
    if os.path.exists(used_path):
        raise PlanApprovalError(
            "ERROR: This Plan ID has already been used. Preview and approve a fresh plan."
        )
    try:
        record = _read_plan_record(pending_path)
    except FileNotFoundError as exc:
        raise PlanApprovalError(
            "ERROR: This Plan ID does not match a pending plan in the current checkout. Run the command without "
            "--apply, review the fresh preview, and approve its new Plan ID."
        ) from exc

    recorded_plan_id = record.get("planId")
    semantic_sha256 = record.get("semanticSha256")
    recorded_approval = record.get("approval")
    recorded_checkout = record.get("checkout")
    expires_at = record.get("expiresAtUnix")
    if not all(
        isinstance(value, str)
        for value in (recorded_plan_id, semantic_sha256, recorded_checkout)
    ) or not isinstance(recorded_approval, dict):
        raise PlanApprovalError("ERROR: Mutation plan record is invalid.")
    if (
        PLAN_ID_PATTERN.fullmatch(recorded_plan_id) is None
        or re.fullmatch(r"[0-9a-f]{64}", semantic_sha256) is None
    ):
        raise PlanApprovalError("ERROR: Mutation plan record failed its integrity check.")
    if not hmac.compare_digest(
        recorded_plan_id.encode("ascii"), apply_plan_id.encode("ascii")
    ):
        raise PlanApprovalError("ERROR: Mutation plan record failed its integrity check.")

    current_semantic = plan.semantic_canonical_json
    current_semantic_sha256 = hashlib.sha256(current_semantic.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(
        semantic_sha256.encode("ascii"), current_semantic_sha256.encode("ascii")
    ):
        raise PlanApprovalError(
            "ERROR: --apply Plan ID does not match the current target or payload. "
            "Run the command without --apply and approve the fresh preview."
        )
    try:
        semantic_envelope = json.loads(current_semantic)
        canonical = _canonical_json({
            **semantic_envelope,
            "approval": recorded_approval,
        })
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PlanApprovalError("ERROR: Mutation plan record failed its integrity check.") from exc
    expected_id = PLAN_ID_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(
        expected_id.encode("ascii"), apply_plan_id.encode("ascii")
    ):
        raise PlanApprovalError("ERROR: Mutation plan record failed its integrity check.")
    if (
        recorded_approval.get("checkout") != recorded_checkout
        or recorded_approval.get("expiresAtUnix") != expires_at
    ):
        raise PlanApprovalError("ERROR: Mutation plan record failed its integrity check.")
    if recorded_checkout != checkout:
        raise PlanApprovalError("ERROR: This Plan ID belongs to a different checkout.")
    if recorded_approval.get("checkoutIdentity") != current_identity:
        raise PlanApprovalError("ERROR: This Plan ID belongs to a different Git checkout identity.")
    if not isinstance(expires_at, int) or time.time() > expires_at:
        raise PlanApprovalError("ERROR: This Plan ID has expired. Preview and approve a fresh plan.")
    try:
        _create_used_record(used_path, apply_plan_id)
    except FileExistsError as exc:
        raise PlanApprovalError(
            "ERROR: This Plan ID was already consumed by another process. Preview a fresh plan."
        ) from exc
    except OSError as exc:
        raise PlanApprovalError("ERROR: Could not consume the one-shot Plan ID safely.") from exc
    _CONSUMED_PLAN_IDS.add(apply_plan_id)
    try:
        os.unlink(pending_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise PlanApprovalError(
            "ERROR: The Plan ID was consumed, but its pending local record could not be removed. "
            "Preview and approve a fresh plan before retrying."
        ) from exc
    return True


def plan_id_was_consumed(plan_id: str | None) -> bool:
    """Return whether this process atomically consumed the supplied Plan ID."""
    return isinstance(plan_id, str) and plan_id in _CONSUMED_PLAN_IDS


def render_human_preview(plan: MutationPlan) -> str:
    """Render a copyable, human-readable preview without applying it."""
    register_plan_preview(plan)
    target = json.dumps(plan.target, ensure_ascii=False, sort_keys=True, indent=2)
    payload = json.dumps(plan.payload, ensure_ascii=False, sort_keys=True, indent=2)
    return "\n".join(
        (
            f"Plan ID : {plan.plan_id}",
            f"Action  : {plan.action}",
            f"Checkout: {plan.approval['checkout']}",
            f"Expires : {plan.approval['expiresAtUnix']} (Unix time)",
            "Target  :",
            target,
            "Payload :",
            payload,
            "Preview only: no external state was changed.",
            f"After approving this exact plan, apply it with: --apply {plan.plan_id}",
        )
    )


def render_json_preview(plan: MutationPlan) -> str:
    """Render a structured preview without applying it."""
    register_plan_preview(plan)
    return json.dumps(plan.to_preview_dict(), ensure_ascii=False, indent=2)


def render_plan_preview(plan: MutationPlan, *, json_output: bool = False) -> str:
    """Render either supported preview format."""
    return render_json_preview(plan) if json_output else render_human_preview(plan)
