# services/views.py
from decimal import Decimal
import json
from typing import Any, Dict

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema

from wallets.models import Wallet
from .models import AirtimeTransaction, DataTransaction, ProviderLog
from .serializers import (
    AirtimeTransactionSerializer,
    DataTransactionSerializer,
    AirtimePurchaseRequestSerializer,
    DataPurchaseRequestSerializer,
)

from .vtpass import purchase_airtime, purchase_data
try:
    from .vtpass import requery_status, get_service_variations
except ImportError:
    requery_status = None
    get_service_variations = None


# ===================== Helpers =====================
class SafePaginator(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _to_plain_dict(maybe_mapping: Any) -> Dict:
    if isinstance(maybe_mapping, dict):
        return maybe_mapping
    if hasattr(maybe_mapping, "items"):
        return {k: (v[0] if isinstance(v, (list, tuple)) else v) for k, v in maybe_mapping.items()}
    if isinstance(maybe_mapping, (bytes, bytearray)):
        try:
            return json.loads(maybe_mapping.decode("utf-8"))
        except Exception:
            return {}
    if isinstance(maybe_mapping, str):
        try:
            return json.loads(maybe_mapping)
        except Exception:
            return {}
    return {}


def _to_plain_json(maybe_json: Any) -> Dict:
    if isinstance(maybe_json, dict):
        return maybe_json
    try:
        import requests  # type: ignore
        if isinstance(maybe_json, requests.Response):  # pragma: no cover
            try:
                return maybe_json.json()
            except Exception:
                return {"raw": maybe_json.text, "http_status": maybe_json.status_code}
    except Exception:
        pass
    if hasattr(maybe_json, "json") and callable(getattr(maybe_json, "json")):
        try:
            return maybe_json.json()
        except Exception:
            txt = getattr(maybe_json, "text", None)
            code = getattr(maybe_json, "status_code", None)
            return {"raw": txt or str(maybe_json), "http_status": code or "unknown"}
    if isinstance(maybe_json, (bytes, bytearray)):
        try:
            return json.loads(maybe_json.decode("utf-8"))
        except Exception:
            return {"raw": maybe_json.decode("utf-8", errors="ignore")}
    if isinstance(maybe_json, str):
        try:
            return json.loads(maybe_json)
        except Exception:
            return {"raw": maybe_json}
    return {"raw": str(maybe_json)}


def _provider_http_status(maybe_resp: Any) -> int:
    code = getattr(maybe_resp, "status_code", None)
    if isinstance(code, int):
        return code
    if isinstance(maybe_resp, dict) and "http_status" in maybe_resp:
        try:
            return int(maybe_resp["http_status"])
        except Exception:
            return 200
    return 200


def apply_txn_filters(qs, request):
    status_val = request.query_params.get("status")
    network = request.query_params.get("network")
    search = request.query_params.get("search")
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")

    if status_val:
        qs = qs.filter(status=status_val)
    if network:
        qs = qs.filter(network=network)
    if search:
        qs = qs.filter(Q(phone__icontains=search) | Q(client_reference__icontains=search))

    if date_from:
        d = parse_date(date_from)
        if d:
            qs = qs.filter(timestamp__date__gte=d)
    if date_to:
        d = parse_date(date_to)
        if d:
            qs = qs.filter(timestamp__date__lte=d)

    ordering = request.query_params.get("ordering")
    allowed = {"timestamp", "-timestamp", "amount", "-amount"}
    return qs.order_by(ordering) if ordering in allowed else qs.order_by("-timestamp")


# ---------- VTpass result mapping ----------
def _unwrap_provider(payload: Dict) -> Dict:
    """
    Our vtpass helpers often return a wrapper:
      {"ok": ..., "state": ..., "provider": {...}, "http_status": ...}
    Always unwrap to the inner provider dict for mapping.
    """
    if isinstance(payload, dict) and "provider" in payload and isinstance(payload["provider"], dict):
        return payload["provider"]
    return payload


def _map_vtpass_outcome(body: Dict) -> str:
    """
    Success only when: code=="000" AND content.transactions.status=="delivered".
    Pending when: transactions.status=="pending" OR code in {"099","016"}.
    Failed otherwise.
    """
    try:
        code = str(body.get("code", "")).strip()
        tx = body.get("content", {}).get("transactions", {})
        tx_status = str(tx.get("status", "")).lower()
        if code == "000" and tx_status == "delivered":
            return "successful"
        if tx_status == "pending" or code in {"099", "016"}:
            return "pending"
    except Exception:
        pass
    return "failed"


def _provider_status_code(provider_body: Dict, http_status: int | None = None) -> str:
    code = provider_body.get("code")
    if code is not None:
        return str(code)
    if http_status is not None:
        return str(http_status)
    return str(provider_body.get("http_status", "unknown"))


def _extract_provider_reference(provider_body: Dict, default: str) -> str:
    tx = provider_body.get("content", {}).get("transactions", {})
    return (
        provider_body.get("requestId")
        or provider_body.get("request_id")
        or provider_body.get("reference")
        or tx.get("transactionId")
        or default
    )


def _log_provider(user, service_type: str, client_reference: str, request_payload: Dict, response_payload: Dict, http_status: int | None = None):
    try:
        ProviderLog.objects.create(
            user=user,
            service_type=service_type,
            client_reference=client_reference,
            request_payload=request_payload,
            response_payload=response_payload,
            status_code=_provider_status_code(response_payload, http_status),
        )
    except Exception:
        pass


# ---------- Single-flight lock & cooldown ----------
LOCK_TTL = 60  # seconds
COOLDOWN_SEC = 45  # short window to block same (user+phone+network)

def _lock_key_ref(user_id, service, client_ref):
    return f"lock:{service}:ref:{user_id}:{client_ref}"

def _lock_key_line(user_id, service, network, phone):
    return f"lock:{service}:line:{user_id}:{network}:{phone}"


# ===================== Airtime =====================
@extend_schema(
    description="Purchase airtime via VTpass (sandbox/live) with strict single-flight + idempotency.",
    request=AirtimePurchaseRequestSerializer,
    responses={201: AirtimeTransactionSerializer, 200: AirtimeTransactionSerializer}
)
class AirtimePurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = apply_txn_filters(AirtimeTransaction.objects.filter(user=request.user), request)
        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(AirtimeTransactionSerializer(page, many=True).data)

    def post(self, request):
        user = request.user
        payload = _to_plain_dict(request.data)

        serializer = AirtimePurchaseRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        amt = Decimal(str(serializer.validated_data["amount"]))
        network = serializer.validated_data["network"]
        phone = serializer.validated_data["phone"]
        client_ref = serializer.validated_data.get("client_reference") or f"AIRTIME_{user.id}_{AirtimeTransaction.objects.filter(user=user).count() + 1}"

        # Locks: (1) by client_ref, (2) by user+line for cooldown window
        k_ref = _lock_key_ref(user.id, "airtime", client_ref)
        k_line = _lock_key_line(user.id, "airtime", network, phone)
        if not cache.add(k_ref, "1", LOCK_TTL) or not cache.add(k_line, "1", COOLDOWN_SEC):
            return Response({"detail": "Another purchase is in progress for this reference/line."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            # True idempotency: if this reference exists, return it
            existing = AirtimeTransaction.objects.filter(user=user, client_reference=client_ref).first()
            if existing:
                return Response(AirtimeTransactionSerializer(existing).data, status=status.HTTP_200_OK)

            # Also avoid parallel different refs on the same line by returning the most recent pending
            cooloff_after = timezone.now() - timedelta(seconds=COOLDOWN_SEC)
            line_pending = AirtimeTransaction.objects.filter(
                user=user, phone=phone, network=network, status="pending", timestamp__gte=cooloff_after
            ).order_by("-timestamp").first()
            if line_pending:
                return Response(AirtimeTransactionSerializer(line_pending).data, status=status.HTTP_200_OK)

            # Reserve funds + create pending txn (race-safe)
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)

                # re-check idempotency inside the lock
                again = AirtimeTransaction.objects.filter(user=user, client_reference=client_ref).first()
                if again:
                    return Response(AirtimeTransactionSerializer(again).data, status=status.HTTP_200_OK)

                if wallet.balance < amt:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                if hasattr(wallet, "locked"):
                    wallet.balance -= amt
                    wallet.locked = (wallet.locked or Decimal("0")) + amt
                    wallet.save(update_fields=["balance", "locked"])
                else:
                    wallet.balance -= amt
                    wallet.save(update_fields=["balance"])

                txn = AirtimeTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    status="pending",   # use only defined choices
                    client_reference=client_ref,
                )

            # Provider call (once)
            raw_resp = purchase_airtime(network, phone, float(amt), request_id=client_ref)
            http_status = _provider_http_status(raw_resp)
            wrap = _to_plain_json(raw_resp)
            provider = _unwrap_provider(wrap)
            _log_provider(user, "airtime", client_ref, payload, wrap, http_status=http_status)

            outcome = _map_vtpass_outcome(provider)
            provider_ref = _extract_provider_reference(provider, default=client_ref)
            provider_desc = str(provider.get("response_description", ""))[:64]

            # Finalize result (+refund on failure)
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)
                txn = AirtimeTransaction.objects.select_for_update().get(user=user, client_reference=client_ref)

                if hasattr(txn, "provider_status"):
                    txn.provider_status = provider_desc
                if hasattr(txn, "provider_request_id"):
                    txn.provider_request_id = provider_ref
                if hasattr(txn, "raw_response"):
                    try:
                        setattr(txn, "raw_response", provider)
                    except Exception:
                        pass

                if outcome == "successful":
                    txn.status = "successful"
                    if hasattr(wallet, "locked"):
                        wallet.locked -= amt
                        wallet.save(update_fields=["locked"])
                elif outcome == "pending":
                    txn.status = "pending"
                    # keep funds reserved (locked)
                else:
                    txn.status = "failed"
                    if hasattr(wallet, "locked"):
                        wallet.locked -= amt
                        wallet.balance += amt
                        wallet.save(update_fields=["locked", "balance"])
                    else:
                        wallet.balance += amt
                        wallet.save(update_fields=["balance"])

                txn.save()

            return Response(AirtimeTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # If something blew up before finalize, keep it pending (funds reserved),
            # the requery job or manual requery can reconcile.
            try:
                with transaction.atomic():
                    txn = AirtimeTransaction.objects.filter(user=user, client_reference=client_ref).first()
                    if txn and txn.status == "pending":
                        if hasattr(txn, "provider_status"):
                            txn.provider_status = f"ERROR: {str(e)[:60]}"
                        txn.save(update_fields=["provider_status", "updated"])
            except Exception:
                pass
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        finally:
            cache.delete(k_ref)
            cache.delete(k_line)


# ===================== Data =====================
@extend_schema(
    description="Purchase data via VTpass (sandbox/live) with strict single-flight + idempotency.",
    request=DataPurchaseRequestSerializer,
    responses={201: DataTransactionSerializer, 200: DataTransactionSerializer}
)
class DataPurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = apply_txn_filters(DataTransaction.objects.filter(user=request.user), request)
        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(DataTransactionSerializer(page, many=True).data)

    def post(self, request):
        user = request.user
        payload = _to_plain_dict(request.data)

        serializer = DataPurchaseRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        amt = Decimal(str(serializer.validated_data["amount"]))
        network = serializer.validated_data["network"]
        phone = serializer.validated_data["phone"]
        plan = serializer.validated_data["plan"]
        client_ref = serializer.validated_data.get("client_reference") or f"DATA_{user.id}_{DataTransaction.objects.filter(user=user).count() + 1}"

        k_ref = _lock_key_ref(user.id, "data", client_ref)
        k_line = _lock_key_line(user.id, "data", network, phone)
        if not cache.add(k_ref, "1", LOCK_TTL) or not cache.add(k_line, "1", COOLDOWN_SEC):
            return Response({"detail": "Another purchase is in progress for this reference/line."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            existing = DataTransaction.objects.filter(user=user, client_reference=client_ref).first()
            if existing:
                return Response(DataTransactionSerializer(existing).data, status=status.HTTP_200_OK)

            cooloff_after = timezone.now() - timedelta(seconds=COOLDOWN_SEC)
            line_pending = DataTransaction.objects.filter(
                user=user, phone=phone, network=network, status="pending", timestamp__gte=cooloff_after
            ).order_by("-timestamp").first()
            if line_pending:
                return Response(DataTransactionSerializer(line_pending).data, status=status.HTTP_200_OK)

            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)

                again = DataTransaction.objects.filter(user=user, client_reference=client_ref).first()
                if again:
                    return Response(DataTransactionSerializer(again).data, status=status.HTTP_200_OK)

                if wallet.balance < amt:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                if hasattr(wallet, "locked"):
                    wallet.balance -= amt
                    wallet.locked = (wallet.locked or Decimal("0")) + amt
                    wallet.save(update_fields=["balance", "locked"])
                else:
                    wallet.balance -= amt
                    wallet.save(update_fields=["balance"])

                txn = DataTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    plan=plan,
                    status="pending",
                    client_reference=client_ref,
                )

            raw_resp = purchase_data(network, phone, plan, request_id=client_ref)
            http_status = _provider_http_status(raw_resp)
            wrap = _to_plain_json(raw_resp)
            provider = _unwrap_provider(wrap)
            _log_provider(user, "data", client_ref, payload, wrap, http_status=http_status)

            outcome = _map_vtpass_outcome(provider)
            provider_ref = _extract_provider_reference(provider, default=client_ref)
            provider_desc = str(provider.get("response_description", ""))[:64]

            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)
                txn = DataTransaction.objects.select_for_update().get(user=user, client_reference=client_ref)

                if hasattr(txn, "provider_status"):
                    txn.provider_status = provider_desc
                if hasattr(txn, "provider_request_id"):
                    txn.provider_request_id = provider_ref
                if hasattr(txn, "raw_response"):
                    try:
                        setattr(txn, "raw_response", provider)
                    except Exception:
                        pass

                if outcome == "successful":
                    txn.status = "successful"
                    if hasattr(wallet, "locked"):
                        wallet.locked -= amt
                        wallet.save(update_fields=["locked"])
                elif outcome == "pending":
                    txn.status = "pending"
                else:
                    txn.status = "failed"
                    if hasattr(wallet, "locked"):
                        wallet.locked -= amt
                        wallet.balance += amt
                        wallet.save(update_fields=["locked", "balance"])
                    else:
                        wallet.balance += amt
                        wallet.save(update_fields=["balance"])

                txn.save()

            return Response(DataTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            try:
                with transaction.atomic():
                    txn = DataTransaction.objects.filter(user=user, client_reference=client_ref).first()
                    if txn and txn.status == "pending":
                        if hasattr(txn, "provider_status"):
                            txn.provider_status = f"ERROR: {str(e)[:60]}"
                        txn.save(update_fields=["provider_status", "updated"])
            except Exception:
                pass
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        finally:
            cache.delete(k_ref)
            cache.delete(k_line)


# ===================== Status Requery =====================
@extend_schema(description="Re-query provider status and reconcile the transaction.")
class PurchaseStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_reference: str):
        if requery_status is None:
            return Response({"detail": "Requery not available."}, status=501)

        txn = (AirtimeTransaction.objects.filter(user=request.user, client_reference=client_reference).first() or
               DataTransaction.objects.filter(user=request.user, client_reference=client_reference).first())
        if not txn:
            return Response({"detail": "Transaction not found."}, status=404)

        wrap = _to_plain_json(requery_status(client_reference))
        provider = _unwrap_provider(wrap)
        _log_provider(request.user, "vtpass", client_reference, {"action": "requery"}, wrap)

        outcome = _map_vtpass_outcome(provider)
        if outcome != txn.status:
            txn.status = outcome
            if hasattr(txn, "provider_status"):
                txn.provider_status = str(provider.get("response_description", ""))[:64]
            txn.save(update_fields=["status", "provider_status", "updated"])

            if outcome == "failed":
                try:
                    with transaction.atomic():
                        wallet = Wallet.objects.select_for_update().get(user=request.user)
                        if hasattr(wallet, "locked"):
                            if wallet.locked and wallet.locked >= txn.amount:
                                wallet.locked -= txn.amount
                                wallet.balance += txn.amount
                                wallet.save(update_fields=["locked", "balance"])
                        else:
                            wallet.balance += txn.amount
                            wallet.save(update_fields=["balance"])
                except Wallet.DoesNotExist:
                    pass
            elif outcome == "successful":
                try:
                    with transaction.atomic():
                        wallet = Wallet.objects.select_for_update().get(user=request.user)
                        if hasattr(wallet, "locked") and wallet.locked and wallet.locked >= txn.amount:
                            wallet.locked -= txn.amount
                            wallet.save(update_fields=["locked"])
                except Wallet.DoesNotExist:
                    pass

        serializer_cls = AirtimeTransactionSerializer if isinstance(txn, AirtimeTransaction) else DataTransactionSerializer
        return Response(serializer_cls(txn).data)


# ===================== Service Variations =====================
@extend_schema(description="Get provider variations/plans for a given serviceID.")
class ServiceVariationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id: str):
        if get_service_variations is None:
            return Response({"detail": "Variations not available."}, status=501)

        res = get_service_variations(service_id) or {}
        ok = bool(res.get("ok")) or str(res.get("code", "")) == "000"
        return Response(res, status=200 if ok else 400)
