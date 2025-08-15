# services/views.py
from decimal import Decimal
import json
from typing import Any, Dict, Tuple

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiResponse

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


# ---------- Helpers ----------
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
    # Accept dict / bytes / str / requests.Response / custom obj
    if isinstance(maybe_json, dict):
        return maybe_json
    # Try requests.Response-like
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
            # best effort fallback
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
    # Try to grab HTTP status if present
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


def _map_provider_state(provider_body: Dict) -> str:
    # VTpass typically has code "000" success; state sometimes present
    state = str(provider_body.get("state", "")).lower()
    if state in {"success", "successful"}:
        return "successful"
    if state == "pending":
        return "pending"
    if state == "failed":
        return "failed"

    code = str(provider_body.get("code", "")).strip()
    if code == "000":
        return "successful"
    if code in {"099", "016"}:
        return "pending"
    return "failed"


def _provider_status_code(provider_body: Dict, http_status: int = None) -> str:
    code = provider_body.get("code")
    if code is not None:
        return str(code)
    if http_status is not None:
        return str(http_status)
    return str(provider_body.get("http_status", "unknown"))


def _extract_provider_reference(provider_body: Dict, default: str) -> str:
    return (
        provider_body.get("requestId")
        or provider_body.get("request_id")
        or provider_body.get("reference")
        or provider_body.get("transactionId")
        or default
    )


def _log_provider(user, service_type: str, client_reference: str, request_payload: Dict, response_payload: Dict, http_status: int = None):
    # Log outside atomic. Best-effort only.
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
        # Do not raise; avoid breaking main flow
        pass


# ---------- Airtime ----------
@extend_schema(
    description="Purchase airtime via VTpass (sandbox/live).",
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
        # keep your fallback scheme
        client_ref = serializer.validated_data.get("client_reference") or f"AIRTIME_{user.id}_{AirtimeTransaction.objects.count() + 1}"

        # Idempotency before any side-effects
        existing = AirtimeTransaction.objects.filter(client_reference=client_ref, user=user).first()
        if existing:
            return Response(AirtimeTransactionSerializer(existing).data, status=status.HTTP_200_OK)

        # ---- Provider call OUTSIDE atomic (so logs never roll back) ----
        raw_resp = purchase_airtime(network, phone, amt, request_id=client_ref)
        http_status = _provider_http_status(raw_resp)
        provider = _to_plain_json(raw_resp)  # normalize to dict for mapping/logging

        # Always log provider IO (best-effort)
        _log_provider(user, "airtime", client_ref, payload, provider, http_status=http_status)

        # Map status and extract provider ref before DB ops
        new_status = _map_provider_state(provider)
        provider_ref = _extract_provider_reference(provider, default=client_ref)
        provider_desc = str(provider.get("response_description", ""))[:64]

        # ---- Wallet + Transaction in a narrow atomic ----
        try:
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)

                # Re-check idempotency inside atomic, too (race-safety)
                existing = AirtimeTransaction.objects.filter(client_reference=client_ref, user=user).first()
                if existing:
                    return Response(AirtimeTransactionSerializer(existing).data, status=status.HTTP_200_OK)

                if wallet.balance < amt:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                # Deduct
                wallet.balance -= amt
                wallet.save(update_fields=["balance"])

                # Create txn
                txn = AirtimeTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    status=new_status if new_status in {"successful", "pending", "failed"} else "pending",
                    client_reference=client_ref,
                )
                # Optional fields if model has them
                if hasattr(txn, "provider_status"):
                    txn.provider_status = provider_desc
                if hasattr(txn, "provider_reference"):
                    txn.provider_reference = provider_ref
                if hasattr(txn, "raw_response"):
                    try:
                        # JSONField preferred
                        setattr(txn, "raw_response", provider)
                    except Exception:
                        pass
                txn.save()

                # Refund on immediate failed
                if new_status == "failed":
                    wallet.balance += amt
                    wallet.save(update_fields=["balance"])

                return Response(AirtimeTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # We already logged provider IO outside atomic; no need to log here again.
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Data ----------
@extend_schema(
    description="Purchase data via VTpass (sandbox/live).",
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
        client_ref = serializer.validated_data.get("client_reference") or f"DATA_{user.id}_{DataTransaction.objects.count() + 1}"

        # Idempotency before side effects
        existing = DataTransaction.objects.filter(client_reference=client_ref, user=user).first()
        if existing:
            return Response(DataTransactionSerializer(existing).data, status=status.HTTP_200_OK)

        # ---- Provider call OUTSIDE atomic ----
        raw_resp = purchase_data(network, phone, plan, request_id=client_ref)
        http_status = _provider_http_status(raw_resp)
        provider = _to_plain_json(raw_resp)

        _log_provider(user, "data", client_ref, payload, provider, http_status=http_status)

        new_status = _map_provider_state(provider)
        provider_ref = _extract_provider_reference(provider, default=client_ref)
        provider_desc = str(provider.get("response_description", ""))[:64]

        # ---- Wallet + Transaction (narrow atomic) ----
        try:
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)

                # Re-check idempotency inside atomic
                existing = DataTransaction.objects.filter(client_reference=client_ref, user=user).first()
                if existing:
                    return Response(DataTransactionSerializer(existing).data, status=status.HTTP_200_OK)

                if wallet.balance < amt:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                wallet.balance -= amt
                wallet.save(update_fields=["balance"])

                txn = DataTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    plan=plan,
                    status=new_status if new_status in {"successful", "pending", "failed"} else "pending",
                    client_reference=client_ref,
                )
                if hasattr(txn, "provider_status"):
                    txn.provider_status = provider_desc
                if hasattr(txn, "provider_reference"):
                    txn.provider_reference = provider_ref
                if hasattr(txn, "raw_response"):
                    try:
                        setattr(txn, "raw_response", provider)
                    except Exception:
                        pass
                txn.save()

                if new_status == "failed":
                    wallet.balance += amt
                    wallet.save(update_fields=["balance"])

                return Response(DataTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Status Requery ----------
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

        provider = _to_plain_json(requery_status(client_reference))
        _log_provider(request.user, "vtpass", client_reference, {"action": "requery"}, provider)

        new_status = _map_provider_state(provider)
        if new_status != txn.status:
            txn.status = new_status
            if hasattr(txn, "provider_status"):
                txn.provider_status = str(provider.get("response_description", ""))[:64]
            txn.save()

            if new_status == "failed":
                try:
                    with transaction.atomic():
                        wallet = Wallet.objects.select_for_update().get(user=request.user)
                        wallet.balance += txn.amount
                        wallet.save(update_fields=["balance"])
                except Wallet.DoesNotExist:
                    pass

        serializer_cls = AirtimeTransactionSerializer if isinstance(txn, AirtimeTransaction) else DataTransactionSerializer
        return Response(serializer_cls(txn).data)


# ---------- Service Variations ----------
@extend_schema(description="Get provider variations/plans for a given serviceID.")
class ServiceVariationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id: str):
        if get_service_variations is None:
            return Response({"detail": "Variations not available."}, status=501)

        res = get_service_variations(service_id) or {}
        ok = bool(res.get("ok")) or str(res.get("code", "")) == "000"

        return Response(res, status=200 if ok else 400)
