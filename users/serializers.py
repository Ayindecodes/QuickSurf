from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


def _check_password_strength(password: str, user) -> None:
    try:
        validate_password(password, user=user)
    except DjangoValidationError as e:
        raise serializers.ValidationError(list(e.messages))


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "password", "date_joined")
        read_only_fields = ("id", "date_joined")  # id & date_joined not required in requests
        extra_kwargs = {
            "email": {"required": True},
        }

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def validate(self, attrs):
        try:
            _check_password_strength(attrs["password"], User(email=attrs.get("email", "")))
        except serializers.ValidationError as e:
            raise serializers.ValidationError({"password": e.detail})
        return attrs

    def create(self, validated_data):
        # Ensure password is hashed using create_user
        return User.objects.create_user(**validated_data)


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)
    new_password = serializers.CharField(write_only=True)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False)
    everywhere = serializers.BooleanField(default=False)


class PinSerializer(serializers.Serializer):
    pin = serializers.CharField(max_length=4, min_length=4)
    password = serializers.CharField(write_only=True)


# ---- Schemas for Swagger docs ----

class LoginRequestSchema(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class TokenPairSchema(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()
