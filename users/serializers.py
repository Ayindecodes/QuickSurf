# users/serializers.py

from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model  = User
        fields = ('id', 'email', 'password')

    def create(self, validated_data):
        # Use your custom manager to handle password hashing
        return User.objects.create_user(**validated_data)
