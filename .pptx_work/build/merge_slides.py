from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET
from pathlib import Path
src=Path('/Users/jyin/Library/CloudStorage/OneDrive-UniversityofTennessee/0 Project/LTB/meeting/monthly/LTB monthly Meeting 10_26_2026 copy.pptx')
added=Path('.pptx_work/build/inserted_slides.pptx')
out=Path('.pptx_work/output/LTB_monthly_Meeting_10_26_2026_updated.pptx')
P='http://schemas.openxmlformats.org/presentationml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
ET.register_namespace('p',P); ET.register_namespace('r',R); ET.register_namespace('',REL)
with ZipFile(src) as zin, ZipFile(added) as znew:
    original={n:zin.read(n) for n in zin.namelist()}
    pres=ET.fromstring(original['ppt/presentation.xml'])
    rels=ET.fromstring(original['ppt/_rels/presentation.xml.rels'])
    sldlist=pres.find(f'{{{P}}}sldIdLst')
    # Add unique relationship and slide ids after the existing summary (slide 2).
    next_rid=25; next_sid=423
    rel_type=R+'/'+'slide'
    layout_type=R+'/'+'slideLayout'
    layout_target='../slideLayouts/slideLayout2.xml'
    add_overrides=[]
    for i in range(1,4):
        slide_name=f'slide{11+i}.xml'
        rel_id=f'rId{next_rid}'; sid=str(next_sid)
        next_rid+=1; next_sid+=1
        rel=ET.SubElement(rels,f'{{{REL}}}Relationship',{'Id':rel_id,'Type':rel_type,'Target':f'slides/{slide_name}'})
        sld=ET.Element(f'{{{P}}}sldId',{'id':sid,f'{{{R}}}id':rel_id})
        sldlist.insert(2+i-1,sld)
        # Import generated editable slide XML, keeping its native shapes and text.
        original[f'ppt/slides/{slide_name}']=znew.read(f'ppt/slides/slide{i}.xml')
        # Point the slide at the existing deck's matching content layout. No notes relationship is needed.
        root=ET.Element(f'{{{REL}}}Relationships')
        ET.SubElement(root,f'{{{REL}}}Relationship',{'Id':'rId1','Type':layout_type,'Target':layout_target})
        original[f'ppt/slides/_rels/{slide_name}.rels']=ET.tostring(root,encoding='utf-8',xml_declaration=True)
        add_overrides.append((f'/ppt/slides/{slide_name}','application/vnd.openxmlformats-officedocument.presentationml.slide+xml'))
    original['ppt/presentation.xml']=ET.tostring(pres,encoding='utf-8',xml_declaration=True)
    original['ppt/_rels/presentation.xml.rels']=ET.tostring(rels,encoding='utf-8',xml_declaration=True)
    types=ET.fromstring(original['[Content_Types].xml'])
    for part,ctype in add_overrides:
        ET.SubElement(types,f'{{{CT}}}Override',{'PartName':part,'ContentType':ctype})
    ET.register_namespace('',CT)
    original['[Content_Types].xml']=ET.tostring(types,encoding='utf-8',xml_declaration=True)
    out.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(out,'w',ZIP_DEFLATED) as zout:
        for name,data in original.items(): zout.writestr(name,data)
print(out, out.stat().st_size)
