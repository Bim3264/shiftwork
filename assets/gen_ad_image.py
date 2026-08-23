#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ShiftWork Facebook ad (1080x1080) — promo-led referral offer + product hook."""
import html, resvg_py, os

W = H = 1080
NAVY   = "#102A43"
TEAL   = "#14B8A6"
TEAL_D = "#0F9488"
CHAR   = "#1F2937"
OFF    = "#F8FAFC"
MUTE   = "#94A3B8"
WHITE  = "#FFFFFF"
TEALBG = "#E6FAF6"
LTEAL  = "#CDECE6"   # light teal text on navy
FONT   = "IBM Plex Sans Thai"

s = []
def esc(t):  # decompose THAI SARA AM so resvg positions marks correctly
    return html.escape(str(t).replace("ำ", "ํา"), quote=True)
def rect(x,y,w,h,fill,rx=0,opacity=1.0,extra=""):
    o = f' opacity="{opacity}"' if opacity!=1.0 else ""
    s.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
             f'fill="{fill}" rx="{rx}"{o} {extra}/>')
def circle(cx,cy,r,fill,opacity=1.0):
    s.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" opacity="{opacity}"/>')
def text(x,y,t,size,fill,weight=400,anchor="start",spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    s.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
             f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}>{esc(t)}</text>')
def tw(t,size,f=0.60):   # rough Thai/mixed text width estimate
    return len(str(t))*size*f
def check(cx,cy,color,r=15):
    circle(cx,cy,r,color)
    s.append(f'<path d="M {cx-7:.1f} {cy:.1f} l 5 5 l 9 -11" stroke="{WHITE}" '
             f'stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')

PAD = 64
HERO_H = 668

# ---- backgrounds ----
rect(0,0,W,H,OFF)
rect(0,0,W,HERO_H,NAVY)
# soft decorative circles
circle(W-70, 90, 240, TEAL, 0.16)
circle(W-40, 120, 130, TEAL, 0.22)
circle(W-150, HERO_H-70, 90, TEAL, 0.12)

# ---- logo ----
s.append(f'<text x="{PAD}" y="96" font-family="{FONT}" font-size="40" font-weight="700">'
         f'<tspan fill="{WHITE}">Shift</tspan><tspan fill="{TEAL}">Work</tspan></text>')

# ---- kicker pill ----
kick = "โปรโมชั่นชวนเพื่อน"
kw = tw(kick,24)+44
rect(PAD, 128, kw, 46, TEAL, rx=23)
text(PAD+kw/2, 159, kick, 24, WHITE, 700, anchor="middle")

# ---- hero headline ----
text(PAD, 288, "ชวน 1 วอร์ด", 90, WHITE, 700)
s.append(f'<text x="{PAD}" y="388" font-family="{FONT}" font-size="90" font-weight="700">'
         f'<tspan fill="{TEAL}">ฟรี 1 เดือน</tspan></text>')
# "ฟรีทั้งคู่" pill
fp = "ฟรีทั้งคู่ — ทั้งคุณและเพื่อน"
fpw = tw(fp,30)+56
rect(PAD, 420, fpw, 58, TEALBG, rx=29)
text(PAD+fpw/2, 459, fp, 30, TEAL_D, 700, anchor="middle")

# ---- conditions ----
conds = [
    "เพื่อนที่คุณชวน ได้ใช้ฟรีเดือนแรก",
    "เมื่อเพื่อนเริ่มจ่ายจริง คุณได้ฟรีอีก 1 เดือน",
    "ชวนได้ไม่จำกัด — ยิ่งชวนยิ่งฟรี",
]
cy = 536
for c in conds:
    check(PAD+16, cy-8, TEAL)
    text(PAD+48, cy, c, 27, WHITE, 400)
    cy += 46

# ---- product section ----
py = HERO_H
text(PAD, py+62, "แล้ว ShiftWork คืออะไร?", 32, NAVY, 700)
text(PAD, py+108, "ระบบจัดตารางเวรพยาบาลอัตโนมัติ เสร็จในไม่กี่นาที", 25, CHAR, 400)
text(PAD, py+142, "จัดเวรยุติธรรม สมดุลภาระงาน ทำงานน้อยลง", 25, CHAR, 400)

# feature chips
chips = ["เสร็จไวทันใจ", "จัดเวรยุติธรรม", "สมดุลภาระงาน"]
cxp = PAD
chy = py+176
for ch in chips:
    cwp = tw(ch,22)+58
    rect(cxp, chy, cwp, 48, TEALBG, rx=24)
    check(cxp+26, chy+24, TEAL_D, r=11)
    text(cxp+44, chy+31, ch, 22, TEAL_D, 600)
    cxp += cwp + 16

# ---- CTA button ----
cta = "ทักแชทเพจ ShiftWork เพื่อเริ่มใช้ฟรี"
bw = tw(cta,27)+96
bx = (W-bw)/2
by = py+272
rect(bx, by, bw, 78, TEAL, rx=39)
text(bx+bw/2-16, by+50, cta, 27, WHITE, 700, anchor="middle")
# arrow
ax = bx+bw-42
s.append(f'<path d="M {ax:.1f} {by+39-9:.1f} l 12 9 l -12 9" stroke="{WHITE}" '
         f'stroke-width="3.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
       f'viewBox="0 0 {W} {H}">' + "".join(s) + "</svg>")

with open("ad_th.svg","w",encoding="utf-8") as f:
    f.write(svg)

FONT_DIR = os.environ.get("SW_FONT_DIR","ttf2")
png = resvg_py.svg_to_bytes(svg_string=svg, font_dirs=[FONT_DIR],
                            sans_serif_family=FONT, skip_system_fonts=True)
with open("ShiftWork-Ad-Referral-TH.png","wb") as f:
    f.write(bytes(png))
print("done", len(bytes(png)), "bytes")
