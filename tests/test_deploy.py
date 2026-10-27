"""2.0.2 — deploy helpers: configure.py never touches an existing .env and says why; compose files resolve as documented."""
import json,os,shutil,stat,subprocess,sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
ORIGIN="https://booking.tdbs-collaudo.it"

def project(tmp_path):
    (tmp_path/"scripts").mkdir()
    shutil.copy(ROOT/"scripts"/"configure.py",tmp_path/"scripts"/"configure.py")
    for name in ("compose.yaml","Dockerfile"):shutil.copy(ROOT/name,tmp_path/name)
    shutil.copytree(ROOT/"deploy",tmp_path/"deploy")
    return tmp_path

def configure(root,*extra):
    return subprocess.run([sys.executable,str(root/"scripts"/"configure.py"),"--edition","cloud","--origin",ORIGIN,*extra],capture_output=True,text=True)

def read_env(root):
    return dict(l.split("=",1) for l in (root/".env").read_text().splitlines() if "=" in l)

def test_fresh_env_is_complete_and_private(tmp_path):
    root=project(tmp_path);r=configure(root)
    assert r.returncode==0,r.stderr
    assert stat.S_IMODE(os.stat(root/".env").st_mode)==0o600
    env=read_env(root)
    for k in ("TDBS_EDITION","PUBLIC_ORIGIN","DJANGO_SECRET_KEY","TDBS_KEYS","TDBS_ACTIVE_KEY","TDBS_DEV","TRUST_PROXY","POSTGRES_PASSWORD","APP_DB_PASSWORD","DATABASE_URL"):
        assert env.get(k),k
    assert "SITE_ADDRESS" not in env and env["DATABASE_URL"].startswith("postgresql://tdbooking_app:")
    assert set(json.loads(env["TDBS_KEYS"]))=={"v1","index"}

def test_complete_env_is_never_rewritten(tmp_path):
    root=project(tmp_path);configure(root);before=(root/".env").read_bytes()
    r=configure(root)
    assert r.returncode!=0 and "completo" in r.stderr
    assert (root/".env").read_bytes()==before

@pytest.mark.parametrize("content",["","\n\n","# Generare .env con scripts/configure.py\n# TRANSACTIONAL_SMTP_HOST=smtp.fornitore.it\n","DJANGO_SECRET_KEY=\n"])
def test_empty_env_explains_how_to_recover(tmp_path,content):
    # 2.0.1 only said "esiste già" for an .env renamed by mistake from .env.example, and compose then failed on every variable
    root=project(tmp_path);(root/".env").write_text(content)
    r=configure(root)
    assert r.returncode!=0 and "vuoto" in r.stderr and "rm .env" in r.stderr
    assert (root/".env").read_text()==content

def test_partial_env_lists_what_is_missing_and_keeps_it(tmp_path):
    root=project(tmp_path);(root/".env").write_text("DB_PASSWORD=vecchia\nSECRET_KEY=vecchio\nPUBLIC_ORIGIN=https://x.it\n")
    r=configure(root)
    assert r.returncode!=0 and "incompleto" in r.stderr
    for k in ("DJANGO_SECRET_KEY","TDBS_KEYS","DATABASE_URL"):assert k in r.stderr
    assert "PUBLIC_ORIGIN" not in r.stderr.split("Mancano:")[1].split(".")[0]
    assert "mv .env .env.vecchio" in r.stderr and (root/".env").read_text().startswith("DB_PASSWORD=vecchia")

def compose(root,*files,env=None):
    args=["docker","compose","--env-file",str(root/".env")]
    for f in files:args+=["-f",str(root/f)]
    return subprocess.run(args+["config","--format","json"],capture_output=True,text=True,cwd=root,env={**os.environ,**(env or {})})

needs_compose=pytest.mark.skipif(not shutil.which("docker") or subprocess.run(["docker","compose","version"],capture_output=True).returncode,reason="docker compose non disponibile")

@needs_compose
def test_compose_names_the_fix_when_env_is_missing_values(tmp_path):
    root=project(tmp_path);(root/".env").write_text("")
    r=compose(root,"compose.yaml")
    assert r.returncode!=0 and "scripts/configure.py" in r.stderr

@needs_compose
@pytest.mark.parametrize("extra,host_ip,port",[("","0.0.0.0","8085"),("TDBS_BIND=10.1.1.50\nTDBS_PORT=8090\n","10.1.1.50","8090")])
def test_web_is_published_straight_on_the_host(tmp_path,extra,host_ip,port):
    # 2.1.0: no bundled Caddy; the proxy in front (NPMplus) forwards to this port
    root=project(tmp_path);configure(root)
    (root/".env").write_text((root/".env").read_text()+extra)
    r=compose(root,"compose.yaml")
    assert r.returncode==0,r.stderr
    cfg=json.loads(r.stdout)
    assert set(cfg["services"])=={"db","web","worker"} and set(cfg.get("volumes",{}))=={"pgdata"}
    ports=cfg["services"]["web"]["ports"]
    assert len(ports)==1 and ports[0]["host_ip"]==host_ip and ports[0]["published"]==port and ports[0]["target"]==8000
    assert all("ports" not in cfg["services"][x] for x in ("db","worker"))

def test_no_bundled_proxy_left():
    text="\n".join((ROOT/f).read_text() for f in ("compose.yaml","image-lock.json","scripts/configure.py"))
    assert "caddy" not in text.lower() and "SITE_ADDRESS" not in text
    assert not (ROOT/"deploy"/"Caddyfile").exists() and not (ROOT/"deploy"/"compose.npmplus.yaml").exists()

# 2.0.3 — the 2.0.2 zip shipped files readable only by root: PostgreSQL (user postgres) could not read the
# mounted deploy/init-db.sh, so the application role was never created.
SKIP={".env","dev.sqlite3"}
def shipped(root):
    for p in root.rglob("*"):
        rel=p.relative_to(root).parts
        if p.name in SKIP or any(x in ("__pycache__",".pytest_cache","static-collected",".git") for x in rel):continue
        yield p

def test_every_shipped_file_is_world_readable():
    bad=[str(p.relative_to(ROOT)) for p in shipped(ROOT) if (p.is_file() and not p.stat().st_mode&stat.S_IROTH) or (p.is_dir() and not p.stat().st_mode&stat.S_IXOTH)]
    assert not bad,bad

def test_files_mounted_into_containers_are_readable_by_their_users():
    assert (ROOT/"deploy/init-db.sh").stat().st_mode&stat.S_IROTH
    assert (ROOT/"deploy/init-db.sh").stat().st_mode&stat.S_IXOTH

@pytest.mark.django_db
def test_grant_runtime_keeps_role_unprivileged_and_in_sync(monkeypatch):
    from django.db import connection
    from django.core.management import call_command
    from io import StringIO
    if connection.vendor!="postgresql":pytest.skip("richiede PostgreSQL")
    monkeypatch.setenv("APP_DB_PASSWORD","password-di-prova-"+"x"*20)
    out=StringIO();call_command("grant_runtime",stdout=out)
    assert "Permessi runtime configurati." in out.getvalue()
    with connection.cursor() as c:
        c.execute("SELECT rolsuper,rolbypassrls,rolcanlogin,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname='tdbooking_app'")
        assert c.fetchone()==(False,False,True,False,False)
        c.execute("SELECT has_database_privilege('tdbooking_app',current_database(),'CONNECT')");assert c.fetchone()[0]

# 2.3.1 — "docker compose build" skipped the migrate service (tools profile): it kept the old code and
# answered "No migrations to apply" while web, already updated, crashed on the missing table.
@needs_compose
def test_one_image_for_web_worker_and_migrate(tmp_path):
    root=project(tmp_path);configure(root)
    r=subprocess.run(["docker","compose","--env-file",str(root/".env"),"-f",str(root/"compose.yaml"),"--profile","tools","config","--format","json"],capture_output=True,text=True,cwd=root)
    assert r.returncode==0,r.stderr
    services=json.loads(r.stdout)["services"]
    assert {services[s]["image"] for s in ("web","worker","migrate")}=={"tdbooking-suite:local"}

def test_gunicorn_does_not_write_to_the_read_only_container():
    assert "--no-control-socket" in (ROOT/"Dockerfile").read_text()

@pytest.mark.django_db
def test_pending_migrations_are_named(monkeypatch):
    from django.db import connection
    from core import rls
    assert rls.pending_migrations(connection)==[]
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.conf import settings
    monkeypatch.setattr(settings,"DEV",False)
    monkeypatch.setattr("core.management.commands.check_runtime.pending_migrations",lambda c:["core.0099_futura"])
    if connection.vendor!="postgresql":pytest.skip("richiede PostgreSQL")
    with pytest.raises(CommandError,match="core.0099_futura.*docker compose --profile tools run --rm migrate"):call_command("check_runtime")

@pytest.mark.django_db
def test_pages_survive_a_missing_settings_table(monkeypatch):
    from django.db import DatabaseError
    from django.test import Client
    from core.models import SiteSettings
    def broken(*a,**kw):raise DatabaseError('relation "core_sitesettings" does not exist')
    monkeypatch.setattr(SiteSettings.objects,"get_or_create",broken)
    r=Client().get("/login/")
    assert r.status_code==200 and "Booking Suite" in r.content.decode()

# 2.4.0 — switching edition: Private refuses to start on a database with more than one business
@pytest.mark.django_db
def test_private_edition_refuses_a_database_with_many_businesses(settings):
    from core.models import Tenant
    from core.management.commands.check_runtime import private_problem
    from django.core.management.base import CommandError
    Tenant.objects.create(slug="uno",name="Uno")
    settings.EDITION="private";private_problem()
    Tenant.objects.create(slug="due",name="Due")
    with pytest.raises(CommandError,match="ne ha 2.*TDBS_EDITION=cloud"):private_problem()
    settings.EDITION="cloud";private_problem()
