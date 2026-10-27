"""2.6.0: sending through Google (Gmail API, OAuth) and Microsoft 365 / Exchange (Graph), besides SMTP.

Both send the same MIME message the SMTP path builds, so Message-ID, Reply-To and List-Unsubscribe
stay identical. No SDK: plain HTTPS to fixed Google and Microsoft endpoints.

Google: the agency's own OAuth client (Client ID and secret from Google Cloud). "Collega con Google"
asks for the gmail.send permission and the account email; the refresh token stays encrypted with
the rest of the configuration.
Microsoft: an app registered in Entra ID with the Mail.Send application permission (client
credentials, like TD Forms). It sends from the mailbox in the configuration.
"""
import base64,hashlib,json,smtplib,urllib.error,urllib.parse,urllib.request
from django.core.cache import cache

GOOGLE_AUTH="https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN="https://oauth2.googleapis.com/token"
GOOGLE_USERINFO="https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SEND="https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GOOGLE_REVOKE="https://oauth2.googleapis.com/revoke"
GOOGLE_SCOPES="openid email https://www.googleapis.com/auth/gmail.send"
MS_TOKEN="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
MS_SEND="https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail"

class ApiError(ValueError):
    """A clear message for the page; status is the HTTP status, 0 when the service was not reached."""
    def __init__(self,message,status=0):super().__init__(message);self.status=status

def http(url,form=None,body=None,headers=None,timeout=15):
    """POST (form or raw body) or GET; returns (status, parsed JSON or {})."""
    data=None;h=dict(headers or {})
    if form is not None:data=urllib.parse.urlencode(form).encode();h["Content-Type"]="application/x-www-form-urlencoded"
    elif body is not None:data=body if isinstance(body,bytes) else body.encode()
    req=urllib.request.Request(url,data=data,headers=h,method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:raw=r.read();status=r.status
    except urllib.error.HTTPError as ex:raw=ex.read();status=ex.code
    # URLError: the request did not get through (DNS, refused, connect timeout). Any other error came while
    # waiting for the answer: the service may have acted on it (status -1, never retried for a send)
    except urllib.error.URLError as ex:raise ApiError("Servizio non raggiungibile: "+str(getattr(ex,"reason",ex))[:160])
    except OSError as ex:raise ApiError("Il servizio non ha risposto: "+str(ex)[:160],-1)
    try:parsed=json.loads(raw or b"{}")
    except ValueError:parsed={}
    return status,parsed if isinstance(parsed,dict) else {}

def detail(data):
    e=data.get("error")
    if isinstance(e,dict):return str(e.get("message") or e.get("code") or "")[:200]
    return str(data.get("error_description") or e or "")[:200]

# ---- Google
def google_auth_url(client_id,redirect_uri,state):
    return GOOGLE_AUTH+"?"+urllib.parse.urlencode({"response_type":"code","client_id":client_id,"redirect_uri":redirect_uri,"scope":GOOGLE_SCOPES,
        "access_type":"offline","prompt":"consent","include_granted_scopes":"false","state":state})

def google_exchange(client_id,secret,code,redirect_uri):
    """The code of the consent page -> refresh token and the account email."""
    status,data=http(GOOGLE_TOKEN,form={"code":code,"client_id":client_id,"client_secret":secret,"redirect_uri":redirect_uri,"grant_type":"authorization_code"})
    if status!=200:raise ApiError("Google ha rifiutato il collegamento: "+(detail(data) or str(status)),status)
    if "gmail.send" not in data.get("scope",""):raise ApiError("Durante il consenso non è stato autorizzato l'invio delle email: ricollega e spunta il permesso di Gmail.")
    if not data.get("refresh_token"):raise ApiError("Google non ha dato l'accesso permanente: togli l'app da myaccount.google.com, Sicurezza, Accessi di terze parti, e ricollega.")
    status,info=http(GOOGLE_USERINFO,headers={"Authorization":"Bearer "+data["access_token"]})
    email=info.get("email","") if status==200 else ""
    return {"refresh_token":data["refresh_token"],"account":email}

def google_revoke(token):
    try:http(GOOGLE_REVOKE,form={"token":token})
    except ApiError:pass

def cache_key(*parts):return "mail-token:"+hashlib.sha256("|".join(parts).encode()).hexdigest()

def google_token(config):
    key=cache_key("google",config["client_id"],config["refresh_token"])
    token=cache.get(key)
    if token:return token
    status,data=http(GOOGLE_TOKEN,form={"client_id":config["client_id"],"client_secret":config["client_secret"],"refresh_token":config["refresh_token"],"grant_type":"refresh_token"})
    if status!=200 or not data.get("access_token"):
        if data.get("error")=="invalid_grant":raise ApiError("Il collegamento con Google non è più valido: ricollega l'account.",status)
        raise ApiError("Google non ha dato l'accesso: "+(detail(data) or str(status)),status)
    cache.set(key,data["access_token"],max(60,int(data.get("expires_in",3600))-120))
    return data["access_token"]

# ---- Microsoft
def graph_token(config):
    key=cache_key("graph",config["tenant_id"],config["client_id"],config["client_secret"])
    token=cache.get(key)
    if token:return token
    status,data=http(MS_TOKEN.format(tenant=urllib.parse.quote(config["tenant_id"],safe="")),form={"client_id":config["client_id"],"client_secret":config["client_secret"],
        "scope":"https://graph.microsoft.com/.default","grant_type":"client_credentials"})
    if status!=200 or not data.get("access_token"):
        raise ApiError("Microsoft ha rifiutato le credenziali dell'app: "+(detail(data) or str(status)),status)
    cache.set(key,data["access_token"],max(60,int(data.get("expires_in",3600))-120))
    return data["access_token"]

# ---- sending
def raise_for(status,data,service):
    """HTTP result -> the SMTP exceptions the outbox already understands (4xx temporary, 5xx permanent)."""
    if 200<=status<300:return
    text=f"{service}: "+(detail(data) or str(status))
    if status in (401,403):raise ApiError(text,status)  # configuration: the worker marks it failed, no retry
    if status==429 or status>=500:raise smtplib.SMTPResponseException(451,text)
    raise smtplib.SMTPResponseException(554,text)

def send(config,message):
    """message: email.message.EmailMessage, already complete. A service that cannot be reached is a
    temporary failure (the outbox retries); refused credentials are a configuration error."""
    try:_send(config,message)
    except ApiError as ex:
        if ex.status==0:raise smtplib.SMTPResponseException(451,str(ex))
        # no answer after the message was handed over: it may have left, so the outbox marks it uncertain (no double send)
        if ex.status==-1:raise RuntimeError(str(ex))
        raise

def _send(config,message):
    mime=message.as_bytes()
    if config["method"]=="gmail":
        token=google_token(config)
        raw=base64.urlsafe_b64encode(mime).decode().rstrip("=")
        status,data=http(GOOGLE_SEND,body=json.dumps({"raw":raw}),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        if status==401:cache.delete(cache_key("google",config["client_id"],config["refresh_token"]))
        raise_for(status,data,"Gmail")
    elif config["method"]=="graph":
        token=graph_token(config)
        status,data=http(MS_SEND.format(mailbox=urllib.parse.quote(config["mailbox"],safe="@")),body=base64.b64encode(mime),
            headers={"Authorization":"Bearer "+token,"Content-Type":"text/plain"})
        if status==401:cache.delete(cache_key("graph",config["tenant_id"],config["client_id"],config["client_secret"]))
        if status==403:raise ApiError("Microsoft 365 non permette all'app di inviare da "+config["mailbox"]+": serve il permesso Mail.Send di tipo applicazione, con il consenso dell'amministratore.",403)
        raise_for(status,data,"Microsoft 365")
    else:raise ValueError("Metodo di invio sconosciuto.")
