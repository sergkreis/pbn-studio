# SPDX-License-Identifier: GPL-3.0-only
"""Generate owned geometric artwork; optionally run the real PBN pipeline.
Run from repository root: python examples/generate_demo.py --convert
This simple fixture demonstrates mechanics, not complex-illustration quality.
"""
from pathlib import Path
import sys, argparse, json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image, ImageDraw, ImageFont

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--convert',action='store_true');args=parser.parse_args()
    source=ROOT/'examples/geometric-landscape.png'
    im=Image.new('RGB',(640,800),'#e5eced');d=ImageDraw.Draw(im)
    d.ellipse((415,70,535,190),fill='#e9b957')
    d.polygon([(0,450),(215,190),(420,450)],fill='#627c85')
    d.polygon([(240,470),(460,240),(640,460),(640,650),(240,650)],fill='#a8b8aa')
    d.rectangle((0,530,639,799),fill='#315d66')
    d.polygon([(0,610),(270,580),(640,670),(640,799),(0,799)],fill='#c78863')
    d.rectangle((170,460,350,650),fill='#f1d9b0')
    d.polygon([(140,460),(260,350),(380,460)],fill='#9f594b')
    d.rectangle((235,540,285,650),fill='#414d54');d.rectangle((190,490,220,525),fill='#627c85');d.rectangle((300,490,330,525),fill='#627c85')
    im.save(source)
    if not args.convert:return
    from pbnstudio.engine import run_image,EngineOptions
    from pbnstudio.document import Document
    from pbnstudio.exports import export_bundle
    from pbnstudio.geometry import ensure_app
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage,QPainter
    app=ensure_app();job=ROOT/'reports/public-demo-job';job.parent.mkdir(exist_ok=True)
    doc=Document.load(run_image(source,job,EngineOptions(12,0,0)))
    report=doc.validate_smooth_geometry(repair=True);doc.prepare_labels()
    if not doc.topology_certified or doc.issues:raise RuntimeError('Demo not ready for normal export: '+str(doc.quality_metrics()))
    export_bundle(doc,ROOT/'reports/public-demo-export',diagnostic=False)
    panels=[im]
    for mode in ['color','numbers']:
        renderer=QSvgRenderer(QByteArray(doc.svg(mode,diagnostic=False).encode()))
        img=QImage(640,800,QImage.Format.Format_ARGB32);img.fill(0xffffffff);painter=QPainter(img);renderer.render(painter);painter.end()
        dest=ROOT/'reports'/('demo-'+mode+'.png');img.save(str(dest));panels.append(Image.open(dest).convert('RGB'))
    sheet=Image.new('RGB',(1920,950),'#f5f4ef');draw=ImageDraw.Draw(sheet)
    try:font=ImageFont.truetype('arial.ttf',27);small=ImageFont.truetype('arial.ttf',20)
    except OSError:font=small=ImageFont.load_default()
    titles=['01 / SOURCE','02 / COLOR REGIONS','03 / NUMBERED']
    for n,panel in enumerate(panels):
        panel=panel.resize((600,750));sheet.paste(panel,(n*640+20,95));draw.text((n*640+20,35),titles[n],fill='#263c45',font=font)
    draw.text((20,885),'Real application output / synthetic geometric fixture / not a complex-art quality benchmark',fill='#435861',font=small)
    sheet.save(ROOT/'docs/images/pipeline.png')
    (ROOT/'reports/public-demo-receipt.json').write_text(json.dumps({'geometry':report,'quality':doc.quality_metrics(),'source':'examples/geometric-landscape.png','diagnostic':False},indent=2),encoding='utf-8')
    print('Generated real normal export and README comparison.')
if __name__=='__main__':main()
