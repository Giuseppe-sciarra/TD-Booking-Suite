# 2.8.0 — the nightly update (scripts/aggiorna-tutto.sh) checks the new image with selftest and mails update_report
import io,json
from pathlib import Path
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

def test_update_scripts_parse_and_never_fetch_code():
    import subprocess
    from pathlib import Path
    root=Path(settings.BASE_DIR)
    for name in ("aggiorna-tutto.sh","installa-aggiornamento.sh"):
        script=root/"scripts"/name
        assert script.stat().st_mode&0o111 and subprocess.run(["bash","-n",str(script)]).returncode==0,name
    # code comes only from the public release zip, never from git
    text=(root/"scripts"/"aggiorna-tutto.sh").read_text()
    assert "git fetch" not in text and "git merge" not in text and "git pull" not in text

# 2.14.0 — new code from the public GitHub release: never older, never a pre-release; .env and logs stay put
def _codice():
    import importlib.util
    spec=importlib.util.spec_from_file_location("codice_nuovo",Path(settings.BASE_DIR)/"scripts"/"codice_nuovo.py")
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def _zip(path,top,version,extra=()):
    import zipfile
    with zipfile.ZipFile(path,"w") as z:
        z.writestr(f"{top}/VERSION",version+"\n");z.writestr(f"{top}/core/new.py","nuovo")
        info=zipfile.ZipInfo(f"{top}/scripts/aggiorna-tutto.sh");info.external_attr=0o755<<16;z.writestr(info,"#!/bin/bash")
        for name in extra:z.writestr(f"{top}/{name}","dallo zip")
    return path

def test_code_update_picks_only_newer_final_releases():
    c=_codice()
    rel=lambda tag,**k:{"tag_name":tag,"html_url":"https://github.com/x/releases/"+tag,"zipball_url":"https://api.github.com/zip/"+tag,
                        "assets":[{"name":f"TD-Booking-Suite-{tag.lstrip('v')}-sorgenti.zip","browser_download_url":"https://dl/"+tag}],**k}
    assert c.controlla(rel("v2.15.0"),"2.14.0")==["2.15.0","https://dl/v2.15.0","https://api.github.com/zip/v2.15.0","https://github.com/x/releases/v2.15.0"]
    assert c.controlla(rel("2.14.10"),"2.14.9")[0]=="2.14.10"   # numbers, not strings
    for tag,current,extra in (("v2.14.0","2.14.0",{}),("v2.13.9","2.14.0",{}),("v2.15.0-rc1","2.14.0",{}),("v2.15.0","2.14.0",{"prerelease":True}),
                              ("v2.15.0","2.14.0",{"draft":True}),("v2.15.0","",{})):
        assert c.controlla(rel(tag,**extra),current) is None,(tag,current,extra)
    assert c.controlla({"tag_name":"v2.15.0","zipball_url":"https://api.github.com/zip"},"2.14.0")[1:3]==["","https://api.github.com/zip"]

def test_code_update_unpacks_swaps_and_rolls_back(tmp_path):
    c=_codice()
    inst=tmp_path/"td-booking-suite";(inst/"core").mkdir(parents=True);(inst/"logs").mkdir()
    for name,text in (("VERSION","2.14.0\n"),("core/old.py","vecchio"),(".env","SEGRETO"),("logs/aggiorna-tutto.log","log"),("compose.override.yaml","mio")):
        (inst/name).write_text(text)
    with pytest.raises(SystemExit,match="2.15.1"):c.prepara(_zip(tmp_path/"a.zip","td-booking-suite","2.15.0"),tmp_path/"a","2.15.1")
    with pytest.raises(SystemExit,match="td-booking-suite/"):c.prepara(_zip(tmp_path/"b.zip","altro","2.15.0"),tmp_path/"b","2.15.0")
    assert c.prepara(_zip(tmp_path/"z.zip","Giuseppe-sciarra-td-booking-suite-1a2b3c4","2.15.0"),tmp_path/"z","2.15.0").name.endswith("1a2b3c4")
    new=c.prepara(_zip(tmp_path/"n.zip","td-booking-suite","2.15.0",extra=(".env","compose.override.yaml")),inst/".agg-codice"/"zip","2.15.0")
    assert (new/"scripts"/"aggiorna-tutto.sh").stat().st_mode&0o111
    c.scambia(new,inst,inst/".agg-codice-prima")
    assert (inst/"VERSION").read_text()=="2.15.0\n" and (inst/"core"/"new.py").exists() and not (inst/"core"/"old.py").exists()
    assert (inst/".env").read_text()=="SEGRETO" and (inst/"compose.override.yaml").read_text()=="mio" and (inst/"logs"/"aggiorna-tutto.log").exists()
    assert (inst/".agg-codice-prima"/"core"/"old.py").exists()
    c.annulla(inst,inst/".agg-codice-prima")
    assert (inst/"VERSION").read_text()=="2.14.0\n" and (inst/"core"/"old.py").exists() and not (inst/"core"/"new.py").exists()
    assert not (inst/"scripts").exists() and not (inst/".agg-codice-prima").exists() and (inst/".env").read_text()=="SEGRETO"
