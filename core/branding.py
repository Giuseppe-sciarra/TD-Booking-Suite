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
def clean_photo(upload):
    """2.11.0: the photo of an operator or a room: PNG, JPEG or WebP up to 5 MB, kept as a JPEG of at most 800 px,
    turned upright as the phone took it."""
    from PIL import ImageOps
    data=upload.read(5*1024*1024+1)
    if len(data)>5*1024*1024:raise ValidationError("Foto massimo 5 MB.")
    try:
        im=Image.open(io.BytesIO(data))
        if im.format not in ("PNG","JPEG","WEBP") or im.width*im.height>40000000:raise ValueError()
        im.load();im=ImageOps.exif_transpose(im);im.thumbnail((800,800))
        if im.mode!="RGB":
            rgba=im.convert("RGBA");flat=Image.new("RGB",rgba.size,(255,255,255));flat.paste(rgba,mask=rgba.getchannel("A"));im=flat
        out=io.BytesIO();im.save(out,format="JPEG",quality=85,optimize=True);return out.getvalue()
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):raise ValidationError("Usa una foto PNG, JPEG o WebP valida, massimo 40 megapixel.")
def resource_photo(request,slug,resource_id):
    """The photo of an operator: public like the booking page that shows it; the address changes with the photo."""
    from .models import ResourcePhoto
    t=get_object_or_404(Tenant,slug=slug)
    photo=ResourcePhoto.raw.filter(tenant=t,resource_id=resource_id).first()
    if not photo:return HttpResponse(status=404)
    response=HttpResponse(bytes(photo.data),content_type="image/jpeg")
    response["X-Content-Type-Options"]="nosniff";response["Cache-Control"]="public, max-age=31536000, immutable"
    return response
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
