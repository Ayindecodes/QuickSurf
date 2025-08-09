from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "password", "date_joined")
        read_only_fields = ("id", "date_joined")  # id & date_joined not required in requests
        extra_kwargs = {
            "email": {"required": True},
        }

    def create(self, validated_data):
        # Ensure password is hashed using create_user
        return User.objects.create_user(**validated_data)


# ---- Schemas for Swagger docs ----

class LoginRequestSchema(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class TokenPairSchema(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()
