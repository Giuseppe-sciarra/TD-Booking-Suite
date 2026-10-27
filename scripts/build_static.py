import os,secrets,base64,json
os.environ["TDBS_DEV"]="1"
os.environ["PUBLIC_ORIGIN"]="http://localhost:8000"
os.environ["DJANGO_SECRET_KEY"]=secrets.token_urlsafe(64)
os.environ["TDBS_KEYS"]=json.dumps({k:base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() for k in ("v1","index")})
os.environ["DJANGO_SETTINGS_MODULE"]="tdbooking.settings"
import django;django.setup()
from django.core.management import call_command
call_command("collectstatic",interactive=False)
