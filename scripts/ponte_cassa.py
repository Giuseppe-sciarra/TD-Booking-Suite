#!/usr/bin/env python3
"""Ponte cassa di TD Booking Suite (2.16.0): gira su un computer del negozio, nella stessa rete del registratore
telematico o con il registratore collegato via USB. Chiede al programma gli scontrini da stampare, li passa alla cassa e
restituisce la risposta. Solo libreria standard di Python 3.9+; per la cassa via USB serve anche pyserial
(pip install pyserial).

    python ponte_cassa.py https://indirizzo/book/attivita/api/cassa/ tdcassa_IL-CODICE
    python ponte_cassa.py --porte          (elenca le porte COM, per la cassa via USB)
    python ponte_cassa.py --cerca          (cerca le casse RCH ed Epson nella rete del negozio e sulle porte USB)

Il programma usa le stesse funzioni (deliver) quando chiama la cassa da sé, sull'edizione Windows."""
import ipaddress,json,socket,sys,time,urllib.error,urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

TIMEOUT=20
STX,ETX,ACK,NAK=b"\x02",b"\x03",b"\x06",b"\x15"

def lan(url):
    """Only the shop's own network: the server cannot send this PC anywhere else."""
    try:return ipaddress.IPv4Address(urlsplit(url).hostname or "").is_private and urlsplit(url).scheme=="http"
    except ValueError:return False

OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the register is on the shop's network: never through a proxy
def http_send(url,body):
    """{"status","body"}; {"error":"unreachable"} when nothing reached the register; {"error":"unknown"} when it got the
    request and did not answer: it may have printed."""
    if not lan(url):return {"error":"refused","detail":"Indirizzo della cassa fuori dalla rete del negozio."}
    request=urllib.request.Request(url,data=body.encode(),method="POST",headers={"Content-Type":"text/xml; charset=utf-8"})
    try:
        with OPENER.open(request,timeout=TIMEOUT) as r:return {"status":r.status,"body":r.read(1<<20).decode("utf-8","replace")}
    except urllib.error.HTTPError as ex:return {"status":ex.code,"body":ex.read(1<<20).decode("utf-8","replace")}
    except urllib.error.URLError:return {"error":"unreachable"}  # urllib wraps only the errors of connecting and sending
    except OSError:return {"error":"unknown"}

# ---------- 2.20.3: where the register is
EPSON_STATUS='<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><printerCommand><queryPrinterStatus operator="1" statusType="1" /></printerCommand></s:Body></s:Envelope>'
def networks():
    """The /24 of each private IPv4 address of this PC. shortcut: a /24 each, the shop networks seen so far; ask for the address by hand on a bigger one"""
    found=set()
    try:found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:pass
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as u:u.connect(("10.255.255.255",1));found.add(u.getsockname()[0])  # no packet goes out
    except OSError:pass
    return sorted({ipaddress.ip_network(a+"/24",strict=False) for a in found if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback},key=str)
def identify(host,port=80,wait=0.4):
    """What answers at host:port: "rch" (its web service answers an empty request with its status, nothing printed or
    cleared), "epson" (the status of fpmate) or None."""
    try:
        with socket.create_connection((host,port),timeout=wait):pass
    except OSError:return None
    def post(path,body):
        try:
            with OPENER.open(urllib.request.Request(f"http://{host}:{port}{path}",data=body.encode(),method="POST",headers={"Content-Type":"text/xml; charset=utf-8"}),timeout=3) as r:return r.read(1<<16).decode("utf-8","replace")
        except urllib.error.HTTPError as ex:return ex.read(1<<16).decode("utf-8","replace")
        except OSError:return ""
    answer=post("/service.cgi",'<?xml version="1.0" encoding="UTF-8"?><Service></Service>')
    if "<Service" in answer and "<errorCode" in answer:return "rch"
    if 'success="' in post("/cgi-bin/fpmate.cgi?devid=local_printer&timeout=3000",EPSON_STATUS):return "epson"
    return None
def discover(hosts=None,port=80):
    """[{"device","address"}] of the registers answering on the shop's network, and [{"port","description"}] of the COM ports."""
    hosts=hosts if hosts is not None else [str(h) for n in networks() for h in n.hosts()]
    with ThreadPoolExecutor(64) as pool:kinds=list(pool.map(lambda h:identify(h,port),hosts))
    found=[{"device":k,"address":h if port==80 else f"{h}:{port}"} for h,k in zip(hosts,kinds) if k]
    ports=[]
    try:
        from serial.tools.list_ports import comports
        ports=[{"port":p.device,"description":p.description or ""} for p in comports()]
    except ImportError:pass
    return found,ports

def frame(command,framing):
    data=command.encode("ascii","replace")
    if framing=="cr":return data+b"\r"
    # shortcut: STX, address "01", length on 3 digits, "N", the command, checksum (sum of the bytes from the address on,
    # mod 256, 2 hex digits), ETX; the printer answers ACK or NAK. From RCH's serial protocol as used by the desk programs;
    # its manual (support.rch.it, Struttura del protocollo) is behind a login: check it on the first Print!F via USB
    body=b"01"+f"{len(data):03d}".encode()+b"N"+data
    return STX+body+f"{sum(body)%256:02X}".encode()+ETX
def serial_send(conf,commands,opener=None):
    """RCH via USB (a virtual COM port): one frame a command, ACK before the next, NAK sent again up to 3 times.
    {"serial":"ok"}; {"serial":"nak","at":i}; {"serial":"sent"} without handshake (framing cr: nothing confirms it);
    {"error":...} as http_send."""
    framing=conf.get("framing") or "stx"
    if opener is None:
        try:import serial
        except ImportError:return {"error":"refused","detail":"Per la cassa via USB serve pyserial: pip install pyserial"}
        opener=serial.Serial
    try:port=opener(conf["port"],int(conf.get("baud") or 9600),timeout=3,write_timeout=3,xonxoff=framing=="cr")
    except Exception:return {"error":"unreachable"}
    with port:
        for i,command in enumerate(commands):
            for _ in range(3):
                try:port.write(frame(command,framing))
                except Exception:return {"error":"unreachable" if i==0 else "unknown"}
                if framing=="cr":break
                answer=port.read(1)
                if answer==ACK:break
                if answer!=NAK:return {"error":"unreachable" if i==0 else "unknown"}  # silence: the last ones may have printed
            else:return {"serial":"nak","at":i}
    return {"serial":"sent" if framing=="cr" else "ok"}

def deliver(job,opener=None):
    """A request of the program to the register, network or USB."""
    if job.get("serial"):return serial_send(job["serial"],job["commands"],opener)
    return http_send(job["url"],job["body"])

def call(server,token,data=None):
    request=urllib.request.Request(server,data=json.dumps(data).encode() if data is not None else None,method="POST" if data is not None else "GET",
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","User-Agent":"TD-Ponte-Cassa"})
    with urllib.request.urlopen(request,timeout=30) as r:return json.loads(r.read().decode())
def main(argv):
    if argv[1:]==["--porte"]:
        try:from serial.tools.list_ports import comports
        except ImportError:print("Serve pyserial: pip install pyserial");return 1
        for p in comports():print(p.device,"-",p.description)
        return 0
    if argv[1:]==["--cerca"]:
        print("Cerco nella rete",", ".join(map(str,networks())) or "(nessuna rete trovata)","…")
        found,ports=discover()
        for f in found:print("Cassa",{"rch":"RCH Print!F","epson":"Epson"}[f["device"]],"in rete:",f["address"])
        if not found:print("Nessuna cassa in rete: è accesa, con il cavo, nella stessa rete e con il web server acceso?")
        for p in ports:print("Porta USB/seriale:",p["port"],"-",p["description"])
        return 0
    if len(argv)!=3 or not argv[2].startswith("tdcassa_"):print(__doc__);return 2
    server,token=argv[1],argv[2]
    parts=urlsplit(server)
    if parts.scheme!="https" and parts.hostname not in ("localhost","127.0.0.1"):print("L'indirizzo del programma deve iniziare con https://");return 2
    print("Ponte cassa avviato. Lascia aperta questa finestra; Ctrl+C per fermarlo.")
    while True:
        try:
            job=call(server,token)["job"]
            if not job:time.sleep(2);continue
            result=deliver(job)
            print(time.strftime("%H:%M:%S"),"richiesta",job["id"][:8],"->",result.get("error") or result.get("serial") or result.get("status"))
            call(server,token,{"id":job["id"],"attempt":job["attempt"],**result})
        except urllib.error.HTTPError as ex:
            print(time.strftime("%H:%M:%S"),"il programma risponde",ex.code,ex.read(300).decode("utf-8","replace"));time.sleep(30 if ex.code in (401,403) else 5)
        except (urllib.error.URLError,OSError,ValueError) as ex:print(time.strftime("%H:%M:%S"),"programma non raggiungibile:",ex);time.sleep(10)
        except KeyboardInterrupt:return 0

if __name__=="__main__":sys.exit(main(sys.argv))
