"""End-to-end check on a real WordPress with the TD Booking Suite connector.

Not part of pytest: it needs a running WordPress (WP_URL, MySQL access through `mysql`) with the connector
active and TDBS_ALLOW_HTTP defined in wp-config.php (the test portal is plain http), and a running core
(CORE_URL, development mode) on the database configured in the environment, holding the tenant TENANT.
Usage:  python tests/e2e/wordpress_e2e.py
Every step prints "ok" or stops with the first failure.
"""
import os,re,subprocess,sys
from pathlib import Path
import requests

WP=os.environ.get("WP_URL","http://127.0.0.1:8088")
CORE=os.environ.get("CORE_URL","http://127.0.0.1:8000")
WP_USER,WP_PASS=os.environ.get("WP_USER","admin"),os.environ.get("WP_PASS","Admin-test-password-1")
MYSQL=os.environ.get("MYSQL","mysql -uroot wp").split()
TENANT=os.environ.get("TENANT","salone-e2e")
PORTAL=os.environ.get("PORTAL_ORIGIN","https://portale.example.it")  # a real portal address, for the manual settings
ROOT=Path(__file__).resolve().parents[2]

def ok(cond,msg):
    if not cond:print("FALLITO:",msg);sys.exit(1)
    print("ok ",msg)
def sql(q):return subprocess.run(MYSQL+["-N","-e",q],capture_output=True,text=True,check=True).stdout.strip()
def shell(code):return subprocess.run([sys.executable,"manage.py","shell","-c",code],cwd=ROOT,capture_output=True,text=True).stdout.strip().splitlines()[-1]
def page_with(content,name):
    sql(f"DELETE FROM wp_posts WHERE post_name='{name}';")
    return sql(f"INSERT INTO wp_posts (post_author,post_date,post_date_gmt,post_content,post_title,post_excerpt,post_status,post_name,to_ping,pinged,post_modified,post_modified_gmt,post_content_filtered,post_type) VALUES (1,NOW(),UTC_TIMESTAMP(),'{content}','{name}','','publish','{name}','','',NOW(),UTC_TIMESTAMP(),'','page');SELECT LAST_INSERT_ID();")

sql("DELETE FROM wp_options WHERE option_name='tdbs_settings';DELETE FROM wp_posts WHERE post_name IN ('prenota','prenota-test','sede-test') AND post_type='page';")
s=requests.Session()
s.post(f"{WP}/wp-login.php",data={"log":WP_USER,"pwd":WP_PASS,"wp-submit":"Log In","testcookie":"1"},cookies={"wordpress_test_cookie":"WP Cookie check"},timeout=20)

# ---------- 1. a pizzeria with two locations, not connected yet
shell(f"from core.models import Tenant;from core.profile import apply_profile;print(apply_profile(Tenant.objects.get(slug='{TENANT}'),'ristorante',['Centro','Mare']))")
post=page_with("[td_booking_suite]","prenota-test")
html=requests.get(f"{WP}/?page_id={post}",timeout=20).text
ok("Prenotazioni temporaneamente non disponibili" in html and "tdbs-frame" not in html,"shortcode senza collegamento: messaggio, nessun riquadro")
page=s.get(f"{WP}/wp-admin/options-general.php?page=tdbooking",timeout=20).text
ok("Collegati al portale" in page and "Impostazioni manuali" in page,"connettore: pagina impostazioni con Collegati al portale e impostazioni manuali")
ok("Migrazione da TD Booking" not in page and "Passaggio completato" not in page,"connettore: nessuna traccia del passaggio da TD Booking")

# ---------- 2. manual settings, through the real settings form (sanitize included)
form_nonce=re.search(r"""name=['"]option_page['"] value=['"]tdbooking['"].*?name=['"]_wpnonce['"] value=['"]([0-9a-f]+)['"]""",page,re.S)
ok(form_nonce,"connettore: modulo delle impostazioni manuali")
def save(origin,tenant=TENANT):
    return s.post(f"{WP}/wp-admin/options.php",data={"option_page":"tdbooking","action":"update","_wpnonce":form_nonce.group(1),"_wp_http_referer":"/wp-admin/options-general.php?page=tdbooking",
        "tdbs_settings[origin]":origin,"tdbs_settings[tenant]":tenant},allow_redirects=False,timeout=20)
ok(save(PORTAL).status_code==302,"impostazioni manuali salvate")
html=requests.get(f"{WP}/?page_id={post}",timeout=20).text
ok(f'src="{PORTAL}/book/{TENANT}/?theme=auto"' in html and f'data-origin="{PORTAL}"' in html,"shortcode: riquadro del portale con il tema automatico")
save("https://portale.example.it/percorso")
ok(PORTAL+'"' in sql("SELECT option_value FROM wp_options WHERE option_name='tdbs_settings'") or PORTAL in sql("SELECT option_value FROM wp_options WHERE option_name='tdbs_settings'"),"indirizzo con percorso rifiutato, resta quello di prima")
ok("percorso" not in sql("SELECT option_value FROM wp_options WHERE option_name='tdbs_settings'"),"nessun percorso salvato")

# ---------- 3. Collegati al portale
def nonce_for(action,html):
    m=re.search(r'name="action" value="'+action+r'">\s*<input type="hidden" id="_wpnonce" name="_wpnonce" value="([0-9a-f]+)"',html)
    return m and m.group(1)
code=shell(f"from core.models import Tenant;from core.wpconnect import new_code;print(new_code(Tenant.objects.get(slug='{TENANT}')))")
ok(re.fullmatch(r"[A-Z2-9]{4}(-[A-Z2-9]{4}){3}",code),"portale: codice di collegamento generato")
sql("DELETE FROM wp_posts WHERE post_name='prenota' AND post_type='page';")
page=s.get(f"{WP}/wp-admin/options-general.php?page=tdbooking",timeout=20).text
ok("Collegati al portale" in page or "Collega a un" in page,"connettore: sezione Collegati al portale")
def connect(c):
    html=s.get(f"{WP}/wp-admin/options-general.php?page=tdbooking",timeout=20).text
    return s.post(f"{WP}/wp-admin/admin-post.php",data={"action":"tdbs_connect","_wpnonce":nonce_for("tdbs_connect",html),"portal":CORE,"code":c.lower(),"create_page":"1"},allow_redirects=True,timeout=30)
r=connect(code)
ok("Collegato a" in r.text and "La pagina Prenota è pubblicata" in r.text,"connettore: collegato con il codice, pagina Prenota creata")
opt=sql("SELECT option_value FROM wp_options WHERE option_name='tdbs_settings'")
ok(f'"{TENANT}"' in opt and CORE in opt and "profile" in opt,"connettore: portale, attività e configurazione salvati")
origins=shell(f"from core.models import Tenant;print(Tenant.objects.get(slug='{TENANT}').embed_origins)")
ok(WP.rstrip("/") in origins,"portale: il sito WordPress è autorizzato da solo")
ok("Collegato a" in r.text and "[td_booking_suite service=" in r.text,"connettore: shortcode dei servizi elencati")
pid=sql("SELECT ID FROM wp_posts WHERE post_name='prenota' AND post_type='page' AND post_status='publish'")
html=requests.get(f"{WP}/?page_id={pid}",timeout=20).text
ok("tdbs-frame" in html and f"{CORE}/book/{TENANT}/" in html,"pagina Prenota: modulo del portale nell'iframe")
again=connect(code)
ok("Codice non valido o scaduto" in again.text,"connettore: il codice vale una volta sola")
html=s.get(f"{WP}/wp-admin/options-general.php?page=tdbooking",timeout=20).text
r=s.post(f"{WP}/wp-admin/admin-post.php",data={"action":"tdbs_refresh","_wpnonce":nonce_for("tdbs_refresh",html)},timeout=30)
ok("Configurazione aggiornata dal portale" in r.text,"connettore: configurazione aggiornata dal portale")
loc=shell(f"from core.models import Tenant,Location;from core.tenancy import scope;t=Tenant.objects.get(slug='{TENANT}')\nwith scope(t):print(Location.objects.filter(active=True).first().pk)")
post2=page_with('[td_booking_suite location=\\"'+loc+'\\" theme=\\"dark\\"]',"sede-test")
html=requests.get(f"{WP}/?page_id={post2}",timeout=20).text
ok(f"location={loc}" in html and "theme=dark" in html,"shortcode con la sede e il tema scuro: riquadro filtrato sulla sede")
sql("DELETE FROM wp_posts WHERE post_name='sede-test';")
book=requests.get(f"{CORE}/book/{TENANT}/?location={loc}",timeout=20)
ok(book.status_code==200 and WP.rstrip("/") in book.headers.get("Content-Security-Policy",""),"portale: il sito collegato può mostrare il modulo (frame-ancestors)")

# ---------- 4. disconnect
html=s.get(f"{WP}/wp-admin/options-general.php?page=tdbooking",timeout=20).text
r=s.post(f"{WP}/wp-admin/admin-post.php",data={"action":"tdbs_disconnect","_wpnonce":nonce_for("tdbs_disconnect",html)},timeout=30)
ok("Sito scollegato" in r.text,"connettore: sito scollegato")
html=requests.get(f"{WP}/?page_id={post}",timeout=20).text
ok("Prenotazioni temporaneamente non disponibili" in html,"dopo lo scollegamento il riquadro non compare")
sql("DELETE FROM wp_posts WHERE post_name IN ('prenota-test','sede-test');")

debug=Path(os.environ.get("WP_DEBUG_LOG","/tmp/wp-debug.log"))
errors=[l for l in (debug.read_text().splitlines() if debug.exists() else []) if "td-booking-suite" in l and ("Fatal" in l or "Warning" in l or "Notice" in l or "Deprecated" in l)]
ok(not errors,"nessun errore, avviso o deprecazione del connettore nel log di WordPress")
print("\nCollaudo WordPress completato: tutti i passaggi superati")
