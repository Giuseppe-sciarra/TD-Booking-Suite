"""The PDF of the cloud (Docker) installation guide, from INSTALLAZIONE.md, styled like the Windows manuals:

    python docs/manuali/build.py   ->  docs/Istruzioni-TD-Booking-Suite-Cloud.pdf

Needs the markdown package and Playwright for Python with Chromium."""
import html,re
from datetime import date
from pathlib import Path
import markdown

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
VERSION=(ROOT/"VERSION").read_text().strip()
MONTHS=["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"]
TITLE="TD Booking Suite Cloud · Istruzioni"

def page():
    md=(ROOT/"INSTALLAZIONE.md").read_text("utf-8")
    md=md.split("\n",1)[1].lstrip()  # the # title becomes the cover
    intro,md=md.split("\n## ",1)  # the opening lines go under the index
    body=markdown.markdown("## "+md,extensions=["fenced_code","tables"])
    toc=[]
    def h2(m):
        n=len(toc)+1;text=m.group(1);num=re.match(r"(\d+)\.\s*(.*)",text)
        label,title=(num.group(1),num.group(2)) if num else ("",text)
        toc.append((n,label,title))
        return f'<h2 id="c{n}"><span class="num">{label}</span>{title}</h2>'
    body=re.sub(r"<h2>(.*?)</h2>",h2,body)
    items="".join(f'<li><span>{l}</span><a href="#c{n}">{t}</a></li>' for n,l,t in toc)
    d=date.today()
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><title>{TITLE}</title><link rel="stylesheet" href="manuale.css"><style>h2{{break-before:auto;break-after:avoid;margin-top:9mm}}.toc+h2{{margin-top:0}}</style></head><body>
<section class="cover"><div class="mark">TD</div><div class="kicker">Istruzioni · versione {VERSION}</div><h1>TD Booking Suite Cloud</h1>
<p class="sub">Installare TD Booking Suite con Docker su un server Linux dietro NPMplus, collegare i siti WordPress, fare i backup, aggiornare e collegare il registratore di cassa.</p>
<div class="foot"><div><strong>Tastiere Digitali</strong>tastieredigitali.tech</div><div>{d.day} {MONTHS[d.month-1]} {d.year}</div></div></section>
<section class="toc"><h2>Indice</h2>{markdown.markdown(intro)}<ol>{items}</ol></section>
{body}</body></html>'''

if __name__=="__main__":
    from playwright.sync_api import sync_playwright
    src=HERE/"_cloud.html";src.write_text(page(),"utf-8");target=ROOT/"docs"/"Istruzioni-TD-Booking-Suite-Cloud.pdf"
    footer=('<div style="width:100%;font:8px Helvetica,Arial,sans-serif;color:#6b6079;padding:0 17mm;display:flex;justify-content:space-between">'
            f'<span>{html.escape(TITLE)} · versione {VERSION}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
    try:
        with sync_playwright() as p:
            b=p.chromium.launch();pg=b.new_page();pg.goto(src.as_uri(),wait_until="networkidle");pg.evaluate("document.fonts.ready")
            pg.pdf(path=str(target),format="A4",print_background=True,prefer_css_page_size=True,display_header_footer=True,header_template="<span></span>",footer_template=footer)
            b.close()
    finally:src.unlink(missing_ok=True)
    print(target)
