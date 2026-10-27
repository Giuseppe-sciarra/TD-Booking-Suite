"""Create unique local secrets; never ship the generated .env."""
import argparse,base64,json,secrets,os
from pathlib import Path
# What compose.yaml needs from .env; DATABASE_URL is absent only in development.
REQUIRED=("TDBS_EDITION","PUBLIC_ORIGIN","DJANGO_SECRET_KEY","TDBS_KEYS","POSTGRES_PASSWORD","APP_DB_PASSWORD","DATABASE_URL")

def existing(dest):
    """Never touch an existing .env (it may hold the keys of live data): explain what is in it instead."""
    found={}
    for line in dest.read_text(encoding="utf-8",errors="replace").splitlines():
        k,sep,v=line.strip().partition("=")
        if sep and not k.startswith("#") and v.strip():found[k.strip()]=v
    missing=[k for k in REQUIRED if k not in found and not (k=="DATABASE_URL" and found.get("TDBS_DEV")=="1")]
    if not missing:raise SystemExit(".env esiste già ed è completo: nessuna chiave sovrascritta.")
    if not found:
        raise SystemExit(".env esiste già ma è vuoto: non contiene chiavi.\n"
                         "Se lo hai creato per errore, cancellalo (rm .env) e rilancia questo comando.")
    raise SystemExit(".env esiste già ma è incompleto. Mancano: "+", ".join(missing)+".\n"
                     "Non lo modifico: se contiene chiavi di un'installazione con dati, perderle renderebbe i dati illeggibili.\n"
                     "Se è un file di prova o di un altro progetto, spostalo (mv .env .env.vecchio) e rilancia questo comando.")

p=argparse.ArgumentParser();p.add_argument("--edition",choices=["private","cloud"],default="cloud");p.add_argument("--origin",default="https://prenotazioni.example.com");p.add_argument("--development",action="store_true")
a=p.parse_args();root=Path(__file__).resolve().parent.parent;dest=root/".env"
if dest.exists():existing(dest)
origin="http://localhost:8000" if a.development else a.origin.rstrip("/")
if not a.development and (not origin.startswith("https://") or ".example." in origin):raise SystemExit("Indica --origin https://il-tuo-dominio.it")
key=lambda:base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
dbpass=secrets.token_hex(24);rootpass=secrets.token_hex(24)
env={"TDBS_EDITION":a.edition,"PUBLIC_ORIGIN":origin,"DJANGO_SECRET_KEY":secrets.token_urlsafe(64),"TDBS_KEYS":json.dumps({"v1":key(),"index":key()},separators=(",",":")),"TDBS_ACTIVE_KEY":"v1","TDBS_DEV":"1" if a.development else "0","TRUST_PROXY":"0" if a.development else "1","POSTGRES_PASSWORD":rootpass,"APP_DB_PASSWORD":dbpass}
if not a.development:env["DATABASE_URL"]="postgresql://tdbooking_app:"+dbpass+"@db:5432/tdbooking"
with os.fdopen(os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"w",encoding="utf-8") as handle:
    handle.write("\n".join(k+"="+v for k,v in env.items())+"\n")
print("Configurazione creata. Conserva una copia cifrata di .env separata dal backup del database.")
