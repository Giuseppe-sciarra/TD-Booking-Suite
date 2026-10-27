import os,base64
os.environ["TDBS_TESTING"]="1"
os.environ["TDBS_DEV"]="1"
os.environ.setdefault("PUBLIC_ORIGIN","http://localhost:8000")
os.environ.setdefault("DJANGO_SECRET_KEY","test-only-"+("x"*64))
os.environ.setdefault("TDBS_KEYS",'{"v1":"'+base64.urlsafe_b64encode(b"x"*32).decode()+'","index":"'+base64.urlsafe_b64encode(b"i"*32).decode()+'"}')
