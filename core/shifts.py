from datetime import timedelta
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import ShiftRule,Occurrence,Booking,Tenant
from .calendar import local_datetime
from .booking import BookingError
def materialize(rule):
    """Create recurring seating occurrences, never rewrite occupied turns."""
    tenant=rule.tenant
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        today=timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()
        existing=Occurrence.objects.filter(rule=rule,start__gte=timezone.now())
        booked=set(Booking.objects.filter(occurrence__in=existing,status="confirmed").values_list("occurrence_id",flat=True))
        if booked:
            raise BookingError("Questo turno ha prenotazioni future. Gestiscile prima di modificare la regola; puoi creare un nuovo turno per un periodo successivo.")
        existing.update(active=False)
        if not rule.active:return
        first=max(today,rule.valid_from or today)
        last=min(today+timedelta(days=min(rule.service.horizon_days,730)),rule.valid_until or today+timedelta(days=730))
        day=first
        while day<=last:
            if day.weekday()==rule.weekday:
                tz=ZoneInfo(tenant.timezone)
                start=local_datetime(day,rule.start,tz)
                end=local_datetime(day+timedelta(days=1) if rule.end<rule.start else day,rule.end,tz,fold=1)
                if start and end and end>start:
                    occurrence=Occurrence.objects.filter(rule=rule,start=start).first()
                    if occurrence:
                        occurrence.end=end;occurrence.capacity=rule.capacity;occurrence.active=True;occurrence.save()
                    else:Occurrence.objects.create(tenant=tenant,rule=rule,service=rule.service,start=start,end=end,capacity=rule.capacity)
            day+=timedelta(days=1)
def extend_rules(tenant):
    """Only append missing future turns; existing bookings and occurrences are untouched."""
    today=timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()
    for rule in ShiftRule.objects.filter(active=True,service__active=True).select_related("service"):
        first=max(today,rule.valid_from or today)
        last=min(today+timedelta(days=rule.service.horizon_days),rule.valid_until or today+timedelta(days=730))
        day=first
        while day<=last:
            if day.weekday()==rule.weekday:
                tz=ZoneInfo(tenant.timezone)
                a=local_datetime(day,rule.start,tz);b=local_datetime(day+timedelta(days=1) if rule.end<rule.start else day,rule.end,tz,fold=1)
                if a and b and not Occurrence.objects.filter(rule=rule,start=a).exists():
                    Occurrence.objects.create(tenant=tenant,rule=rule,service=rule.service,start=a,end=b,capacity=rule.capacity)
            day+=timedelta(days=1)
