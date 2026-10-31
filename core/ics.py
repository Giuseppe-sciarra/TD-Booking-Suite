"""2.14.0: a booking as a calendar file (RFC 5545) and the links that add it to Google Calendar or Outlook, written by
hand with the standard library. Service, business and place only: nothing the client does not know already."""
from datetime import timezone as TZ
from urllib.parse import urlencode
from django.conf import settings
from django.utils import timezone

def escape(text):
    return str(text).replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\r\n","\n").replace("\n","\\n")

def fold(line):
    """At most 75 octets a line, the next ones starting with a space, never inside a UTF-8 character."""
    out=[""]
    for ch in line:
        if len((out[-1]+ch).encode())>(75 if len(out)==1 else 74):out.append(ch)
        else:out[-1]+=ch
    return "\r\n ".join(out)

# UTC times ("Z"): exact in every calendar without a VTIMEZONE block to write by hand
def utc(moment):return moment.astimezone(TZ.utc).strftime("%Y%m%dT%H%M%SZ")
def title(booking):return f"{booking.snapshot.get('service') or booking.service.name} · {booking.tenant.name}"
def place(booking):return ", ".join(x for x in (booking.location.name,booking.location.address) if x)

def ics(booking):
    """The same UID for every version of the booking: a calendar that imports the moved one updates the event."""
    host=settings.PUBLIC_ORIGIN.split("://",1)[-1].split("/")[0].split(":")[0]
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Tastiere Digitali//TD Booking Suite//IT","METHOD:PUBLISH","BEGIN:VEVENT",
        f"UID:{booking.pk}@{host}","DTSTAMP:"+utc(timezone.now()),"DTSTART:"+utc(booking.start),"DTEND:"+utc(booking.end),
        "SUMMARY:"+escape(title(booking)),"LOCATION:"+escape(place(booking)),"END:VEVENT","END:VCALENDAR"]
    return "\r\n".join(fold(x) for x in lines)+"\r\n"

def links(booking):
    """Add to Google Calendar / Outlook: the event opened, to save, in the client's own calendar."""
    start,end=booking.start.astimezone(TZ.utc),booking.end.astimezone(TZ.utc)
    google=urlencode({"action":"TEMPLATE","text":title(booking),"dates":utc(start)+"/"+utc(end),"location":place(booking)})
    outlook=urlencode({"path":"/calendar/action/compose","rru":"addevent","subject":title(booking),"location":place(booking),
        "startdt":start.strftime("%Y-%m-%dT%H:%M:%SZ"),"enddt":end.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {"google":"https://calendar.google.com/calendar/render?"+google,"outlook":"https://outlook.live.com/calendar/action/compose?"+outlook}
