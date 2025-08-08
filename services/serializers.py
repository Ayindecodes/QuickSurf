from rest_framework import serializers
from .models import AirtimeTransaction, DataTransaction


class AirtimeTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AirtimeTransaction
        fields = ['id', 'user', 'network', 'phone', 'amount', 'status', 'timestamp']
        read_only_fields = ['user', 'status', 'timestamp']


class DataTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataTransaction
        fields = ['id', 'user', 'network', 'phone', 'plan', 'amount', 'status', 'timestamp']
        read_only_fields = ['user', 'status', 'timestamp']
