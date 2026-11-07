"""2.22.2: a PDF of text, written here by hand: A4 pages, Helvetica, the letters of the receipts (Latin-1 and €).
Enough for the Cassa's summaries; a table is lines with columns at fixed positions. No library: nothing to install
on Windows, nothing that can hang, a few KB per page."""
A4=(595.28,841.89);MARGIN=48;LINE=14
# Helvetica widths (per 1000 em) of the characters 32..126; other letters count as 556
_W=[278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278]+[556]*10+[278,278,584,584,584,556,1015,
    667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611,
    278,278,278,469,556,333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500,334,260,334,584]
def width(s,size):return sum(_W[ord(c)-32] if 32<=ord(c)<127 else 556 for c in s)*size/1000
def _esc(s):
    b=s.encode("cp1252","replace")  # WinAnsiEncoding: accents and € as the fonts have them
    return b.replace(b"\\",b"\\\\").replace(b"(",b"\\(").replace(b")",b"\\)")
class Doc:
    def __init__(self,title):self.title=title;self.pages=[];self.y=0
    def page(self):self.pages.append([]);self.y=A4[1]-MARGIN
    def put(self,x,y,s,size=10,bold=False,right=False):
        """A piece of text at x,y (points from the bottom left); right=True: ending at x."""
        if right:x-=width(s,size)
        self.pages[-1].append(b"BT /F%d %d Tf %.1f %.1f Td (" % (2 if bold else 1,size,x,y)+_esc(s)+b") Tj ET")
    def room(self,n=1):
        """A new page unless n more lines fit on this one."""
        if not self.pages or self.y-n*LINE<MARGIN+LINE:self.page()
    def line(self,cells,size=10,bold=False,gap=LINE):
        """One row: cells as (x,text) or (x,text,right); a new page when the one in hand is full."""
        self.room();self.y-=gap
        for c in cells:self.put(c[0],self.y,str(c[1]),size,bold,len(c)>2 and c[2])
    def rule(self):self.room();self.y-=6;self.pages[-1].append(b"0.7 w %.1f %.1f m %.1f %.1f l S" % (MARGIN,self.y,A4[0]-MARGIN,self.y));self.y-=2
    def space(self,n=1):self.y-=n*LINE
    def render(self):
        n=len(self.pages);objs=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (5+2*i) for i in range(n)),n),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        for i,ops in enumerate(self.pages):
            foot=b"BT /F1 8 Tf %.1f %.1f Td (" % (A4[0]-MARGIN-width(f"Pagina {i+1} di {n}",8),MARGIN/2)+_esc(f"Pagina {i+1} di {n}")+b") Tj ET"
            body=b"\n".join(ops+[foot])
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % (A4[0],A4[1],6+2*i))
            objs.append(b"<< /Length %d >>\nstream\n" % len(body)+body+b"\nendstream")
        objs.append(b"<< /Title ("+_esc(self.title)+b") /Producer (TD Booking Suite) >>")
        out=bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n");at=[]
        for i,o in enumerate(objs):at.append(len(out));out+=b"%d 0 obj\n" % (i+1)+o+b"\nendobj\n"
        x=len(out);out+=b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs)+1)+b"".join(b"%010d 00000 n \n" % a for a in at)
        out+=b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs)+1,len(objs),x)
        return bytes(out)
