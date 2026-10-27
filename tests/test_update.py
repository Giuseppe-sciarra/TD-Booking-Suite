# 2.8.0 — the nightly update (scripts/aggiorna-tutto.sh) checks the new image with selftest and mails update_report
import io,json
import pytest
from django.conf import settings
from django.core.management import call_command
from core.models import SiteSettings
from tests.test_core import sample
pytestmark=pytest.mark.django_db

def test_selftest_passes_on_this_build(sample):
    out=io.StringIO();call_command("selftest",stdout=out)
    lines=out.getvalue().splitlines()
    assert len(lines)==8 and all(l.startswith("OK      ") for l in lines[:-1]),lines
    result=json.loads(lines[-1])
    assert result["ok"] and result["versioni"]["TD Booking Suite"]==settings.VERSION and result["versioni"]["Django"].startswith("5.")
    assert json.loads(_out("selftest","--versioni"))["pillow"]

def test_selftest_names_what_broke(monkeypatch):
    from core.management.commands import selftest
    monkeypatch.setattr(selftest,"CHECKS",[("cifratura",lambda:1/0),("password",lambda:None)])
    out=io.StringIO()
    with pytest.raises(SystemExit):call_command("selftest",stdout=out)
    lines=out.getvalue().splitlines()
    assert lines[0].startswith("ERRORE  cifratura: ZeroDivisionError") and lines[1]=="OK      password" and json.loads(lines[-1])["ok"] is False

def test_update_report_goes_to_the_agency(monkeypatch):
    sent=[]
    monkeypatch.setattr("core.mail.send_message",lambda config,payload,message_id:sent.append(payload))
    monkeypatch.setattr("core.site.agency_smtp",lambda site=None:{"sender":"portale@example.com"})
    SiteSettings.objects.update_or_create(pk=1,defaults={"support_email":"agenzia@example.com","trial_email":""})
    monkeypatch.setattr("sys.stdin",io.StringIO("Torna a ieri"))
    assert json.loads(_out("update_report","--esito","rollback"))=={"ok":True,"a":"agenzia@example.com","msg":""}
    assert sent==[{"to":"agenzia@example.com","subject":SiteSettings.current().name+": aggiornamento annullato, resta la versione di prima","body":"Torna a ieri"}]
    monkeypatch.setattr("core.site.agency_smtp",lambda site=None:None)
    assert json.loads(_out("update_report","--a","io@example.com"))["msg"]=="invio email dell'agenzia non configurato"

def _out(*args):
    out=io.StringIO();call_command(*args,stdout=out);return out.getvalue()

def test_update_scripts_parse_and_run_from_a_copy():
    import subprocess
    from pathlib import Path
    root=Path(settings.BASE_DIR)
    for name in ("aggiorna-tutto.sh","installa-aggiornamento.sh"):
        script=root/"scripts"/name
        assert script.stat().st_mode&0o111 and subprocess.run(["bash","-n",str(script)]).returncode==0,name
    # git merge may rewrite the script while bash is reading it: it must re-exec from /tmp first
    text=(root/"scripts"/"aggiorna-tutto.sh").read_text()
    assert text.index('exec bash "$COPIA"')<text.index("git merge -q")
