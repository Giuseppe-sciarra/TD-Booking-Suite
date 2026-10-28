import io
from PIL import Image,UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from .models import Tenant
def clean_logo(upload):
    data=upload.read(512*1024+1)
    if len(data)>512*1024:raise ValidationError("Logo massimo 512 KB.")
    try:
        im=Image.open(io.BytesIO(data))
        if im.format not in ("PNG","JPEG","WEBP") or im.width*im.height>4000000:raise ValueError()
        im.load();im.thumbnail((256,256));im=im.convert("RGBA")
        out=io.BytesIO();im.save(out,format="PNG");return out.getvalue()
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):raise ValidationError("Usa un'immagine PNG, JPEG o WebP valida, massimo 4 megapixel.")
def luminance(rgb):
    values=[v/255 for v in rgb]
    return sum(w*(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4) for c,w in zip(values,(.2126,.7152,.0722)))
def mix(color,other,amount):
    """color moved toward other by amount, 0 to 1: the light and dark tints of the accent."""
    a=[int(color[i:i+2],16) for i in (1,3,5)];b=[int(other[i:i+2],16) for i in (1,3,5)]
    return "#"+"".join(f"{round(x+(y-x)*amount):02x}" for x,y in zip(a,b))
def contrast_color(color,dark=False):
    rgb=[int(color[i:i+2],16) for i in (1,3,5)]
    bg=.05 if dark else 1
    for _ in range(60):
        lum=luminance(rgb);ratio=(max(lum,bg)+.05)/(min(lum,bg)+.05)
        if ratio>=4.5:break
        rgb=[round(v*.94+255*.06) if dark else round(v*.94) for v in rgb]
    return "#"+"".join(f"{v:02x}" for v in rgb)
def css(request,slug):
    t=get_object_or_404(Tenant,slug=slug)
    light=contrast_color(t.brand_color);dark=contrast_color(t.brand_color,True)
    body=f":root,:root[data-theme=light]{{--brand:{light};--brand-ink:#fff}}:root[data-theme=dark]{{--brand:{dark};--brand-ink:#241a30}}@media(prefers-color-scheme:dark){{:root[data-theme=auto]{{--brand:{dark};--brand-ink:#241a30}}}}"
    return HttpResponse(body,content_type="text/css")
def logo(request,slug):
    t=get_object_or_404(Tenant,slug=slug)
    if not t.logo:return HttpResponse(status=404)
    response=HttpResponse(bytes(t.logo),content_type="image/png")
    response["X-Content-Type-Options"]="nosniff";return response
