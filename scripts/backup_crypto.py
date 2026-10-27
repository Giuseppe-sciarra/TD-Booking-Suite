"""Stream AES-GCM backup encryption; keep the backup key separate."""
import argparse,os,base64,tempfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
HEADER=b"TDBS-BACKUP-1\n"
LEGACY_HEADERS=(b"PRENODO-BACKUP-1\n",)  # prototype backups stay readable
def transform(mode,source,dest,key):
    source=Path(source);dest=Path(dest)
    if dest.exists():raise ValueError("Destinazione esistente: nessun file sovrascritto.")
    created=False;temporary=None
    try:
        fd,temporary=tempfile.mkstemp(prefix=".tdbs-backup-",dir=dest.parent)
        os.chmod(temporary,0o600)
        with source.open("rb") as src,os.fdopen(fd,"wb") as out:
            created=True
            if mode=="encrypt":
                nonce=os.urandom(12);out.write(HEADER+nonce)
                engine=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor()
                engine.authenticate_additional_data(HEADER)
                while chunk:=src.read(1024*1024):out.write(engine.update(chunk))
                out.write(engine.finalize());out.write(engine.tag)
            else:
                start=src.read(max(len(h) for h in (HEADER,)+LEGACY_HEADERS))
                header=next((h for h in (HEADER,)+LEGACY_HEADERS if start.startswith(h)),None)
                if header is None:raise ValueError("Formato backup non valido.")
                src.seek(len(header))
                nonce=src.read(12);src.seek(-16,2);tag=src.read(16)
                remaining=src.tell()-len(header)-12-16
                if remaining<0:raise ValueError("Backup troncato.")
                src.seek(len(header)+12)
                engine=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor()
                engine.authenticate_additional_data(header)
                while remaining:
                    chunk=src.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError("Backup troncato.")
                    out.write(engine.update(chunk));remaining-=len(chunk)
                out.write(engine.finalize())
            out.flush();os.fsync(out.fileno())
        # Publish only authenticated output, without replacing an existing path.
        os.link(temporary,dest)
        os.unlink(temporary);temporary=None
    except BaseException:
        if temporary and Path(temporary).exists():Path(temporary).unlink()
        raise
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("mode",choices=["keygen","encrypt","decrypt"]);p.add_argument("source");p.add_argument("destination",nargs="?");p.add_argument("--key-file")
 a=p.parse_args()
 if a.mode=="keygen":
  path=Path(a.source)
  with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"wb") as f:f.write(base64.urlsafe_b64encode(os.urandom(32)))
 elif not a.key_file or not a.destination:p.error("Servono destinazione e --key-file.")
 else:transform(a.mode,a.source,a.destination,base64.urlsafe_b64decode(Path(a.key_file).read_bytes()))
