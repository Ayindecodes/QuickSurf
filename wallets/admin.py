# wallets/admin.py
from django.contrib import admin
from django.db.models import Field
from .models import Wallet, Transaction


def _field_names(model):
    return [f.name for f in model._meta.get_fields() if isinstance(f, Field)]


def _present(model, names):
    avail = set(_field_names(model))
    return [n for n in names if n in avail]


class SafeModelAdmin(admin.ModelAdmin):
    """
    Defensive admin:
    - Builds list_display / filters / readonly / ordering only from existing fields
    - Skips date_hierarchy unless the field exists and is Date/DateTime
    """

    list_display_candidates = []
    list_filter_candidates = []
    search_fields_candidates = []
    readonly_candidates = []
    ordering_candidates = []
    date_hierarchy_candidate = None  # e.g. "created"

    def get_list_display(self, request):
        fields = _field_names(self.model)
        if not fields:
            return ("id",)
        preferred = _present(self.model, self.list_display_candidates)
        # Fill with remaining fields (but keep list short)
        rest = [f for f in fields if f not in preferred]
        cols = preferred + rest
        return tuple(cols[:8]) if cols else ("id",)

    def get_list_filter(self, request):
        return tuple(_present(self.model, self.list_filter_candidates))

    def get_search_fields(self, request):
        # Keep user email/username if related field likely exists
        extras = []
        rels = {f.name for f in self.model._meta.fields}
        if "user" in rels:
            extras += ["user__email", "user__username", "user__id"]
        return tuple(set(extras + _present(self.model, self.search_fields_candidates)))

    def get_readonly_fields(self, request, obj=None):
        return tuple(_present(self.model, self.readonly_candidates))

    def get_ordering(self, request):
        for cand in self.ordering_candidates:
            base = cand.lstrip("-")
            if base in _field_names(self.model):
                return (cand,)
        return None

    def get_date_hierarchy(self, request):
        dh = self.date_hierarchy_candidate
        return dh if dh and dh in _field_names(self.model) else None


@admin.register(Wallet)
class WalletAdmin(SafeModelAdmin):
    # Try these first if they exist on your model
    list_display_candidates = ["user", "balance", "locked_amount", "updated", "created", "id"]
    list_filter_candidates = ["created", "updated"]
    search_fields_candidates = ["id"]
    readonly_candidates = ["created", "updated"]
    ordering_candidates = ["-updated", "-created", "-id"]
    date_hierarchy_candidate = "created"


@admin.register(Transaction)
class TransactionAdmin(SafeModelAdmin):
    # Include common possibilities; only existing ones will be used
    list_display_candidates = [
        "ref", "reference", "id", "user", "type", "txn_type",
        "amount", "status", "created", "created_at", "timestamp", "updated", "updated_at",
    ]
    list_filter_candidates = ["type", "txn_type", "status", "created", "created_at", "timestamp"]
    search_fields_candidates = ["ref", "reference", "id"]
    readonly_candidates = ["ref", "reference", "created", "created_at", "updated", "updated_at"]
    ordering_candidates = ["-created", "-created_at", "-timestamp", "-id"]
    date_hierarchy_candidate = "created"
