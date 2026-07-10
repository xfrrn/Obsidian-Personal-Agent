from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime

from oka_application.operations.errors import (
    OperationConfirmationError,
    OperationPermissionDenied,
)
from oka_application.operations.models import ConfirmationClaims, ExecutionContext
from oka_domain.common import Identifier, OperationId, PlanId, Sha256Hash, utc_now
from oka_domain.operations import ActorType, KnowledgeOperation, OperationPlan, OperationType, RiskLevel


class HmacConfirmationTokenService:
    """Small signed, one-time token implementation without a JWT dependency."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("Confirmation-token secret must contain at least 32 bytes")
        self.secret = secret
        self._consumed: set[str] = set()

    def issue(self, claims: ConfirmationClaims) -> str:
        payload = {
            "planId": str(claims.plan_id),
            "planVersion": claims.plan_version,
            "integrityHash": str(claims.integrity_hash),
            "approverId": str(claims.approver_id),
            "approvedOperationIds": [str(item) for item in claims.approved_operation_ids],
            "issuedAt": claims.issued_at.isoformat(),
            "expiresAt": claims.expires_at.isoformat(),
            "nonce": claims.nonce,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded = self._b64(raw)
        signature = self._b64(hmac.new(self.secret, encoded, hashlib.sha256).digest())
        return encoded.decode("ascii") + "." + signature.decode("ascii")

    def verify(self, token: str, *, consume: bool = True) -> ConfirmationClaims:
        try:
            encoded_text, signature_text = token.split(".", 1)
            encoded = encoded_text.encode("ascii")
            signature = self._unb64(signature_text.encode("ascii"))
            expected = hmac.new(self.secret, encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise OperationConfirmationError("Confirmation token signature is invalid")
            payload = json.loads(self._unb64(encoded).decode("utf-8"))
            nonce = payload["nonce"]
            if nonce in self._consumed:
                raise OperationConfirmationError("Confirmation token has already been consumed")
            expires_at = datetime.fromisoformat(payload["expiresAt"])
            if utc_now() >= expires_at:
                raise OperationConfirmationError("Confirmation token has expired")
            claims = ConfirmationClaims(
                plan_id=PlanId(payload["planId"]),
                plan_version=int(payload["planVersion"]),
                integrity_hash=Sha256Hash(payload["integrityHash"]),
                approver_id=Identifier(payload["approverId"]),
                approved_operation_ids=tuple(OperationId(item) for item in payload["approvedOperationIds"]),
                issued_at=datetime.fromisoformat(payload["issuedAt"]),
                expires_at=expires_at,
                nonce=nonce,
            )
            if consume:
                self._consumed.add(nonce)
            return claims
        except OperationConfirmationError:
            raise
        except Exception as exc:
            raise OperationConfirmationError("Malformed confirmation token") from exc

    @staticmethod
    def _b64(value: bytes) -> bytes:
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    @staticmethod
    def _unb64(value: bytes) -> bytes:
        return base64.urlsafe_b64decode(value + b"=" * (-len(value) % 4))


class StaticPermissionAuthorizer:
    OPERATION_PERMISSIONS = {
        OperationType.CREATE_NOTE: "note.create",
        OperationType.UPDATE_NOTE: "note.update",
        OperationType.MOVE_NOTE: "note.move",
        OperationType.UPDATE_METADATA: "note.update",
        OperationType.CREATE_TASK: "task.create",
        OperationType.INVOKE_PLUGIN: "plugin.invoke",
    }

    async def authorize_plan(self, plan: OperationPlan, context: ExecutionContext) -> None:
        if "operation.execute" not in context.permissions:
            raise OperationPermissionDenied("Missing operation.execute permission")
        if plan.risk_level >= RiskLevel.HIGH and "operation.execute.high-risk" not in context.permissions:
            raise OperationPermissionDenied("Missing high-risk execution permission")
        if plan.risk_level is RiskLevel.CRITICAL and "operation.execute.critical" not in context.permissions:
            raise OperationPermissionDenied("Missing critical execution permission")
        if plan.source.remote and plan.risk_level >= RiskLevel.HIGH and context.actor_type is not ActorType.USER:
            raise OperationPermissionDenied("Remote high-risk execution requires a local user context")

    async def authorize_operation(
        self,
        operation: KnowledgeOperation,
        context: ExecutionContext,
    ) -> None:
        required = self.OPERATION_PERMISSIONS[operation.operation_type]
        if required not in context.permissions:
            raise OperationPermissionDenied(
                f"Missing {required} permission",
                details={"operationId": str(operation.operation_id)},
            )
