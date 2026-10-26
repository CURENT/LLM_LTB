from zipfile import ZipFile, ZIP_DEFLATED
from pathlib import Path
from xml.etree import ElementTree as ET
base=Path('.pptx_work/build/current_14_slides.pptx')
slide=Path('.pptx_work/build/classification_table.pptx')
out=Path('.pptx_work/output/LTB_monthly_Meeting_10_26_2026_revised_classification.pptx')
P='http://schemas.openxmlformats.org/presentationml/2006/main'; R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'; REL='http://schemas.openxmlformats.org/package/2006/relationships'
with ZipFile(base) as zin, ZipFile(slide) as zslide:
    parts={name:zin.read(name) for name in zin.namelist()}
    pres=ET.fromstring(parts['ppt/presentation.xml']); rels=ET.fromstring(parts['ppt/_rels/presentation.xml.rels'])
    targets={r.get('Id'):r.get('Target') for r in rels if r.get('Type','').endswith('/slide')}
    slide_id=pres.find(f'{{{P}}}sldIdLst')[3]
    rel_id=slide_id.get(f'{{{R}}}id'); target=targets[rel_id]
    part='ppt/'+target
    parts[part]=zslide.read('ppt/slides/slide1.xml')
    out.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(out,'w',ZIP_DEFLATED) as zout:
        for name,data in parts.items(): zout.writestr(name,data)
print(out,'replaced page 4 part',part,out.stat().st_size)
