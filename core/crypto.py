"""Authenticated encryption. No plaintext fallback; tenant and field bind ciphertext."""
import base64,hashlib,hmac,json,secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from django.conf import settings
def b64(b): return base64.urlsafe_b64encode(b).decode()
def unb64(s): return base64.urlsafe_b64decode(s)
def master(version=None): return unb64(settings.KEYS[version or settings.ACTIVE_KEY])
def seal(data,key,aad):
    nonce=secrets.token_bytes(12)
    return b64(nonce+AESGCM(key).encrypt(nonce,json.dumps(data,ensure_ascii=False,separators=(",",":")).encode(),aad.encode()))
def open_seal(value,key,aad):
    raw=unb64(value)
    return json.loads(AESGCM(key).decrypt(raw[:12],raw[12:],aad.encode()))
def protect(data,label):
    version=settings.ACTIVE_KEY
    return version+"."+seal(data,master(version),label)
def reveal(value,label):
    version,blob=value.split(".",1)
    return open_seal(blob,master(version),label)
def tenant_key(tenant): return unb64(reveal(tenant.wrapped_key,"tenant-key:"+str(tenant.pk)))
def encrypt(tenant,data,label): return seal(data,tenant_key(tenant),str(tenant.pk)+":"+label)
def decrypt(tenant,data,label): return open_seal(data,tenant_key(tenant),str(tenant.pk)+":"+label)
def index(value,key,purpose):
    k=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=purpose.encode()).derive(key)
    return hmac.new(k,value.encode(),hashlib.sha256).hexdigest()
def login_index(email):
    # Dedicated stable index key; retained independently when encryption keys rotate.
    return index(email.strip().casefold(),unb64(settings.KEYS["index"]),"login")
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
