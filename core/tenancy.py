from contextvars import ContextVar
from contextlib import contextmanager
from django.db import connection,transaction,models
current_tenant=ContextVar("tenant",default=None)
class TenantManager(models.Manager):
    def get_queryset(self):
        tenant=current_tenant.get()
        return super().get_queryset().filter(tenant_id=tenant) if tenant else super().get_queryset().none()
@contextmanager
def scope(tenant):
    tid=str(getattr(tenant,"pk",tenant))
    previous=current_tenant.get()
    with transaction.atomic():
        token=current_tenant.set(tid)
        try:
            if connection.vendor=="postgresql":
                with connection.cursor() as cur: cur.execute("SELECT set_config('app.tenant_id', %s, true)",[tid])
            yield
        finally:
            current_tenant.reset(token)
            if connection.vendor=="postgresql" and not connection.needs_rollback:
                with connection.cursor() as cur: cur.execute("SELECT set_config('app.tenant_id', %s, true)",[previous or ""])
