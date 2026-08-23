#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the ShiftWork Thai pricing comparison image (1080x1080) as PNG."""
import html, resvg_py, os

W = H = 1080

# ---- Brand palette ----
NAVY   = "#102A43"
TEAL   = "#14B8A6"
TEAL_D = "#0F9488"
CHAR   = "#1F2937"
GRAY   = "#E5E7EB"
OFF    = "#F8FAFC"
MUTE   = "#94A3B8"
WHITE  = "#FFFFFF"
TEALBG = "#E6FAF6"   # soft teal tint for popular column
FONT   = "IBM Plex Sans Thai"

# ---- Layout ----
PAD = 36
LBL_X = PAD
LBL_W = 360
COL_X0 = LBL_X + LBL_W                    # 396
N = 5
COL_W = (W - PAD - COL_X0) / N            # tier column width
HEAD_TOP = 196
HEAD_H = 182
ROW_TOP = HEAD_TOP + HEAD_H
BOTTOM_MARGIN = 30
TABLE_BOTTOM = H - BOTTOM_MARGIN
ROWS = 13
ROW_H = (TABLE_BOTTOM - ROW_TOP) / ROWS

POP = 2  # index of Ward+ (0-based) -> popular column

# (name, monthly price, nurse cap, yearly price)   yearly = monthly x 10
tiers = [
    ("Free",       "฿0",      "ไม่เกิน 8 คน",  "ฟรีตลอด"),
    ("Ward",       "฿390",    "ไม่เกิน 15 คน", "ปีละ ฿3,900"),
    ("Ward+",      "฿690",    "ไม่เกิน 25 คน", "ปีละ ฿6,900"),
    ("Ward Pro",   "฿1,290",  "ไม่เกิน 40 คน", "ปีละ ฿12,900"),
    ("Department", "เริ่ม ฿2,900", "หลายวอร์ด", "ปีละ ฿29,000"),
]

# each row: (label, [c0..c4]) ; cell = "Y","N" or text string
rows = [
    ("สร้างตารางเวรอัตโนมัติ",              ["Y","Y","Y","Y","Y"]),
    ("กฎจัดเวรพื้นฐาน (อาวุโส/สลับเวร/สมดุล)", ["Y","Y","Y","Y","Y"]),
    ("จำนวนตารางต่อเดือน",                  ["1","ไม่จำกัด","ไม่จำกัด","ไม่จำกัด","ไม่จำกัด"]),
    ("คำขอเวร & วันหยุดของพยาบาล",          ["จำกัด","Y","Y","Y","Y"]),
    ("รองรับวันประชุม",                    ["N","Y","Y","Y","Y"]),
    ("ส่งออกไม่มีลายน้ำ",                   ["ลายน้ำ","Y","Y","Y","Y"]),
    ("ประวัติ & เวอร์ชันตาราง",             ["N","N","Y","Y","Y"]),
    ("รายงานความยุติธรรม / ภาระงาน",        ["N","N","Y","Y","Y"]),
    ("จัดเวรใหม่เมื่อลาป่วย (เร็วๆ นี้)",     ["N","N","Y","Y","Y"]),
    ("วางแผนหลายเดือน",                    ["N","N","N","Y","Y"]),
    ("กำหนดกฎเอง (custom)",                ["N","N","N","Y","Y"]),
    ("หลายวอร์ด + แดชบอร์ดผู้บริหาร",       ["N","N","N","N","Y"]),
    ("การซัพพอร์ต",                        ["ชุมชน","อีเมล","อีเมล","ลำดับสำคัญ","สำคัญ+ตั้งค่า"]),
]

s = []
# resvg mis-shapes precomposed THAI SARA AM (U+0E33); decompose to
# NIKHAHIT (U+0E4D) + SARA AA (U+0E32) so marks position correctly.
def esc(t): return html.escape(str(t).replace("ำ", "ํา"), quote=True)
def rect(x,y,w,h,fill,rx=0,extra=""):
    s.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" rx="{rx}" {extra}/>')
def text(x,y,t,size,fill,weight=400,anchor="start",spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    s.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
             f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}>{esc(t)}</text>')

def check(cx,cy,color):
    # tick mark
    s.append(f'<path d="M {cx-9:.1f} {cy:.1f} l 6 6 l 12 -14" stroke="{color}" '
             f'stroke-width="3.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
def dash(cx,cy):
    s.append(f'<line x1="{cx-8:.1f}" y1="{cy:.1f}" x2="{cx+8:.1f}" y2="{cy:.1f}" '
             f'stroke="{MUTE}" stroke-width="3" stroke-linecap="round"/>')

# ---- background ----
rect(0,0,W,H,OFF)

# ---- header ----
text(PAD, 74, "Shift", 46, NAVY, 700)
# measure-free: place "Work" after "Shift" (approx width). Use tspan-in-one instead:
s.pop()  # remove the split; render as single with two colored tspans
s.append(f'<text x="{PAD}" y="74" font-family="{FONT}" font-size="46" font-weight="700">'
         f'<tspan fill="{NAVY}">Shift</tspan><tspan fill="{TEAL}">Work</tspan></text>')
text(PAD, 128, "แพ็กเกจราคา", 40, NAVY, 700)
text(PAD, 166, "จัดเวรที่ยุติธรรมใน 5 นาที — ทำงานน้อยลง", 22, CHAR, 400)
# billing note pill (right aligned)
note = "ราคาต่อวอร์ด / เดือน · รายปีประหยัด ~17%"
rect(W-PAD-430, 96, 430, 44, TEALBG, rx=22)
text(W-PAD-430+215, 125, note, 20, TEAL_D, 600, anchor="middle")

# ---- popular column highlight (behind header+rows) ----
px = COL_X0 + POP*COL_W
rect(px, HEAD_TOP-10, COL_W, TABLE_BOTTOM-(HEAD_TOP-10)+6, TEALBG, rx=16)

# ---- header tier cells ----
for i,(name,price,cap,yearly) in enumerate(tiers):
    cx = COL_X0 + i*COL_W + COL_W/2
    top = HEAD_TOP
    if i==POP:
        rect(COL_X0+i*COL_W+6, top-10, COL_W-12, 34, TEAL, rx=14)
        text(cx, top+13, "ยอดนิยม", 17, WHITE, 700, anchor="middle")
        name_y = top+50
    else:
        name_y = top+28
    nm_size = 25 if len(name)<=6 else 20
    text(cx, name_y, name, nm_size, NAVY, 700, anchor="middle")
    pr_size = 34 if len(price)<=6 else 24
    text(cx, name_y+40, price, pr_size, TEAL_D if i>0 else NAVY, 700, anchor="middle")
    text(cx, name_y+60, "/เดือน", 14, MUTE, 400, anchor="middle")
    text(cx, name_y+84, cap, 16, MUTE, 400, anchor="middle")
    # yearly price chip (white on the popular teal column so it stays visible)
    yw = 8.6*len(yearly) + 20
    chip_bg = WHITE if i==POP else TEALBG
    rect(cx-yw/2, name_y+98, yw, 26, chip_bg, rx=13)
    text(cx, name_y+116, yearly, 14, TEAL_D, 600, anchor="middle")

# divider under header
s.append(f'<line x1="{PAD}" y1="{ROW_TOP}" x2="{W-PAD}" y2="{ROW_TOP}" stroke="{GRAY}" stroke-width="2"/>')

# ---- feature rows ----
for r,(label,cells) in enumerate(rows):
    ry = ROW_TOP + r*ROW_H
    if r % 2 == 1:
        rect(PAD, ry, W-2*PAD, ROW_H, WHITE, rx=0)
    midy = ry + ROW_H/2
    # label (auto shrink if long)
    lsize = 20
    if len(label) > 26: lsize = 17
    elif len(label) > 20: lsize = 18
    text(LBL_X, midy+lsize*0.34, label, lsize, CHAR, 500)
    for i,val in enumerate(cells):
        cx = COL_X0 + i*COL_W + COL_W/2
        if val == "Y":
            check(cx, midy, TEAL_D if i!=POP else TEAL_D)
        elif val == "N":
            dash(cx, midy)
        else:
            vsize = 17
            if len(val) > 7: vsize = 14
            elif len(val) > 5: vsize = 15
            col = NAVY if val=="ไม่จำกัด" else CHAR
            wt = 600 if val in ("ไม่จำกัด","1") else 500
            text(cx, midy+vsize*0.34, val, vsize, col, wt, anchor="middle")

# row separators (light)
for r in range(1, ROWS):
    ry = ROW_TOP + r*ROW_H
    s.append(f'<line x1="{PAD}" y1="{ry:.1f}" x2="{W-PAD}" y2="{ry:.1f}" stroke="{GRAY}" stroke-width="1" opacity="0.55"/>')

svg =(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
       f'viewBox="0 0 {W} {H}">' + "".join(s) + "</svg>")

with open("pricing_th.svg","w",encoding="utf-8") as f:
    f.write(svg)

FONT_DIR = os.environ.get("SW_FONT_DIR", "ttf2")
png = resvg_py.svg_to_bytes(
    svg_string=svg,
    font_dirs=[FONT_DIR],
    sans_serif_family=FONT,
    skip_system_fonts=True,
)
with open("ShiftWork-Pricing-TH.png","wb") as f:
    f.write(bytes(png))
print("done", len(bytes(png)), "bytes")
