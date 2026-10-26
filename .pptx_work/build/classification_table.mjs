import { Presentation, PresentationFile } from '@oai/artifact-tool';
const p=Presentation.create({slideSize:{width:960,height:540}});
const s=p.slides.add(); s.background.fill='#FFFFFF';
const C={green:'#008000',ink:'#252A2D',gray:'#576168',grid:'#D2DBD6',scenario:'#EEF4F6',scenarioEdge:'#AFC4CC',form:'#FFF3E8',formEdge:'#E5AE83',head:'#F1F6F1',white:'#FFFFFF'};
function shape(geom,x,y,w,h,fill='none',stroke='none',sw=0,name=''){
 return s.shapes.add({geometry:geom,name,position:{left:x,top:y,width:w,height:h},fill,line:{style:'solid',fill:stroke,width:sw}});
}
function txt(text,x,y,w,h,size=12,color=C.ink,bold=false,align='left'){
 const q=shape('textbox',x,y,w,h,'none','none',0); q.text=text;
 q.text.style={fontFamily:'Arial',fontSize:size,color,bold,verticalAlignment:'middle',alignment:align,wrap:true}; return q;
}
txt('Revise parameter classification',54,17,850,47,34,C.green,true);
shape('rect',48,66,864,2,C.green);
txt('Classify controls by their effect on data, constraints, and decision structure.',54,76,850,25,16,C.gray,false);
const x=[44,210,485,916], top=108, headerH=29;
const rowHeights=[57,53,50,55,54,63];
// Header row
shape('rect',x[0],top,x[3]-x[0],headerH,C.head,C.grid,1);
txt('TYPE',x[0]+9,top+3,x[1]-x[0]-18,headerH-6,12,C.green,true);
txt('CURRENT OPERATIONS',x[1]+9,top+3,x[2]-x[1]-18,headerH-6,12,C.green,true);
txt('EFFECT ON THE OPTIMIZATION MODEL',x[2]+9,top+3,x[3]-x[2]-18,headerH-6,12,C.green,true);
const rows=[
 {kind:'Input values',tag:'scenario',op:'scale_pq_load — PQ_1 +10%\nset_line_rate — Line_1 rate_a = 0.9',effect:'Updates demand or line limits. Existing variables, objective terms, and constraint definitions remain; changed limits may still alter the feasible region.',scenario:true},
 {kind:'Device / topology status',tag:'scenario',op:'set_line_status — open Line_1\nset_generator_status — disable unit',effect:'Changes device availability, network state, and solve inputs. Results can shift without adding a decision-variable class.',scenario:true},
 {kind:'Routine settings',tag:'scenario',op:'set_config_t — ED interval = 0.5 h',effect:'Changes the active routine’s time setting and rebuilds its model; adds no custom objective term or constraint.',scenario:true},
 {kind:'Existing constraint toggles',tag:'formulation',op:'set_constraint_status — disable plfub\nset_ramping_status — disable ED ramping',effect:'Changes which existing constraints participate in the solve. This is a formulation change; no new constraint is defined.',scenario:false},
 {kind:'Add objective / constraint',tag:'formulation',op:'add_dispatch_cost\nset_total_generation_cap',effect:'First use adds an objective term or generation-cap constraint. Repeated use updates its coefficient or upper limit.',scenario:false},
 {kind:'New variables + model rewrite',tag:'formulation',op:'replace_generator_with_ibr',effect:'DCOPF adds pibr / pcurt, availability limits, and cost terms, then rewrites nodal power balance (active-power scope).',scenario:false}
];
let y=top+headerH;
for(let i=0;i<rows.length;i++){
 const r=rows[i],h=rowHeights[i],tint=r.scenario?C.scenario:C.form,edge=r.scenario?C.scenarioEdge:C.formEdge;
 shape('rect',x[0],y,x[3]-x[0],h,C.white,C.grid,0.8,`Row ${i+1}`);
 shape('rect',x[0],y,x[1]-x[0],h,tint,'none',0);
 // vertical dividers
 shape('rect',x[1],y,1,h,C.grid); shape('rect',x[2],y,1,h,C.grid);
 txt(r.kind,x[0]+9,y+4,x[1]-x[0]-18,h-21,12,C.ink,true);
 txt(r.tag,x[0]+9,y+h-19,x[1]-x[0]-18,14,10.5,r.scenario?'#567986':'#B85B1C',true);
 txt(r.op,x[1]+9,y+3,x[2]-x[1]-18,h-6,11.5,C.ink,false);
 txt(r.effect,x[2]+9,y+3,x[3]-x[2]-18,h-6,11.8,C.ink,false);
 y+=h;
}
txt('Sources: formulation_controls.json:43–179 · parameter_controls.py:230–302 · formulation_edit.py:105–129',47,top+headerH+rowHeights.reduce((a,b)=>a+b,0)+5,850,18,9.5,C.gray,false);
shape('rect',48,502,864,1,'#D5DDD8');
txt('Monthly Meeting  ·  10/26/2026',600,507,280,22,12,'#647078',false,'right');
txt('4',886,507,24,22,12,'#647078',false,'right');
await (await PresentationFile.exportPptx(p)).save('.pptx_work/build/classification_table.pptx');
const b=await p.export({slide:s,format:'png',scale:1});
import fs from 'node:fs/promises'; await fs.writeFile('.pptx_work/build/classification_table.png',new Uint8Array(await b.arrayBuffer()));
