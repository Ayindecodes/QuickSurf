from django.contrib import admin
from .models import AirtimeTransaction, DataTransaction, ProviderLog

@admin.register(AirtimeTransaction)
class AirtimeTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'network', 'phone', 'amount', 'status', 'client_reference', 'timestamp']
    search_fields = ['user__email', 'phone', 'client_reference']
    list_filter = ['network', 'status', 'timestamp']

@admin.register(DataTransaction)
class DataTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'network', 'phone', 'plan', 'amount', 'status', 'client_reference', 'timestamp']
    search_fields = ['user__email', 'phone', 'plan', 'client_reference']
    list_filter = ['network', 'status', 'timestamp']

@admin.register(ProviderLog)
class ProviderLogAdmin(admin.ModelAdmin):
    list_display = ['service_type', 'status_code', 'timestamp']
    list_filter = ['service_type', 'status_code', 'timestamp']
    readonly_fields = ['request_payload', 'response_payload', 'timestamp']

