#!/usr/bin/env python3
"""Codice nuovo di TD Booking Suite dalle release pubbliche di GitHub, per scripts/aggiorna-tutto.sh.

  controlla RELEASE.json VERSIONE    → se la release è più nuova: versione, zip dei sorgenti, zipball, note (una per riga)
  prepara ZIP CARTELLA VERSIONE      → estrae lo zip e stampa la cartella del codice (td-booking-suite/ con quel VERSION)
  scambia NUOVA INSTALLAZIONE PRIMA  → mette il codice nuovo al posto di quello di adesso, che va in PRIMA
  annulla INSTALLAZIONE PRIMA        → rimette il codice di PRIMA
Configurazione e dati (.env, logs/, backups/, file generati dall'aggiornamento) non si spostano mai.
"""
import json, re, shutil, sys, zipfile
from pathlib import Path

LOCALI = {".env", ".git", ".venv", "logs", "backups", "static-collected", "compose.override.yaml", "requirements.auto.lock"}
locale = lambda n: n in LOCALI or n.startswith(".agg-") or ".sqlite3" in n

def versione(tag):   # solo X.Y.Z (con o senza v): una 2.15.0-rc1 non è un rilascio
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (tag or "").strip())
    return tuple(map(int, m.groups())) if m else None

def controlla(release, attuale):
    nuova, ora = versione(release.get("tag_name")), versione(attuale)
    if release.get("draft") or release.get("prerelease") or not nuova or not ora or nuova <= ora:
        return None   # mai indietro, mai prove: e se non si sa la versione di adesso, niente
    ver = "%d.%d.%d" % nuova
    zip_ = next((a.get("browser_download_url", "") for a in release.get("assets") or [] if a.get("name") == f"TD-Booking-Suite-{ver}-sorgenti.zip"), "")
    return [ver, zip_, release.get("zipball_url") or "", release.get("html_url") or ""]

def prepara(zip_path, dest, ver):
    dest = Path(dest); dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)   # extractall toglie già i percorsi assoluti e i ..; i link diventano file normali
        for i in z.infolist():   # i permessi (gli script .sh eseguibili), che extractall non rimette
            if i.external_attr >> 16 & 0o777 and not i.is_dir():
                (dest / i.filename).chmod(i.external_attr >> 16 & 0o777)
    top = list(dest.iterdir())
    # lo zip dei sorgenti ha td-booking-suite/, lo zipball di GitHub <utente>-td-booking-suite-<commit>/
    if len(top) != 1 or not top[0].is_dir() or not re.fullmatch(r"td-booking-suite|[\w.-]+-td-booking-suite-[0-9a-f]+", top[0].name, re.I):
        raise SystemExit("lo zip non contiene la sola cartella td-booking-suite/")
    v = (top[0] / "VERSION").read_text().strip() if (top[0] / "VERSION").is_file() else ""
    if v != ver:
        raise SystemExit(f"nello zip c'è la versione {v or '?'}, non la {ver}")
    return top[0]

def scambia(nuova, inst, prima):
    nuova, inst, prima = Path(nuova), Path(inst), Path(prima)
    shutil.rmtree(prima, ignore_errors=True); prima.mkdir()
    nomi = sorted(p.name for p in nuova.iterdir() if not locale(p.name))
    (prima / ".nuovi").write_text("\n".join(nomi) + "\n")   # prima di spostare: annulla funziona anche a metà
    for n in nomi:
        if (inst / n).exists() or (inst / n).is_symlink():
            (inst / n).rename(prima / n)
        (nuova / n).rename(inst / n)

def annulla(inst, prima):
    inst, prima = Path(inst), Path(prima)
    for n in (prima / ".nuovi").read_text().split():
        p = inst / n
        if p.is_dir() and not p.is_symlink(): shutil.rmtree(p)
        elif p.exists() or p.is_symlink(): p.unlink()
        if (prima / n).exists() or (prima / n).is_symlink():
            (prima / n).rename(p)
    shutil.rmtree(prima)

if __name__ == "__main__":
    cmd, *a = sys.argv[1:] or [""]
    if cmd == "controlla":
        print("\n".join(controlla(json.load(open(a[0], encoding="utf-8")), a[1]) or []))
    elif cmd == "prepara": print(prepara(*a))
    elif cmd == "scambia": scambia(*a)
    elif cmd == "annulla": annulla(*a)
    else: raise SystemExit(__doc__)
