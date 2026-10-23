# rewards/views.py
from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.throttling import SafeAnonRateThrottle, SafeScopedRateThrottle, SafeUserRateThrottle
from wallets.models import Wallet

from .models import LoyaltyLedger
from .services import RedemptionError, points_balance, points_value, redeem


class RedeemSerializer(serializers.Serializer):
    points = serializers.IntegerField(min_value=1)


@extend_schema(
    description="Loyalty points balance, what it's worth, and the latest point movements.",
    request=None,
    responses={200: OpenApiResponse(description="Points")},
)
class RewardsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        points = points_balance(request.user)
        recent = LoyaltyLedger.objects.filter(user=request.user).order_by("-created_at", "-id")[:20]
        return Response(
            {
                "points": points,
                "naira_value": str(points_value(max(points, 0))),
                "point_value": str(settings.REWARD_POINT_VALUE),
                "min_redemption": settings.REWARDS_MIN_REDEMPTION,
                "recent": [
                    {"date": entry.created_at, "points": entry.points, "reason": entry.reason} for entry in recent
                ],
            }
        )


@extend_schema(
    description="Redeem points into the wallet (at least REWARDS_MIN_REDEMPTION).",
    request=RedeemSerializer,
    responses={200: OpenApiResponse(description="Redeemed")},
)
class RedeemView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SafeUserRateThrottle, SafeAnonRateThrottle, SafeScopedRateThrottle]
    throttle_scope = "redeem"

    def post(self, request):
        serializer = RedeemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            credited = redeem(request.user, serializer.validated_data["points"])
        except RedemptionError as e:
            return Response({"error": str(e), "code": "redemption_refused"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "credited": str(credited),
                "points": points_balance(request.user),
                "balance": str(Wallet.objects.get(user=request.user).balance),
            }
        )
