import fs from 'node:fs/promises';
import { Presentation, PresentationFile } from '@oai/artifact-tool';
const p = Presentation.create({slideSize:{width:960,height:540}});
const C={green:'#008000',dark:'#27343A',gray:'#5B6266',line:'#D5DDD8',pale:'#F1F7F1',blue:'#EAF2F6',blueLine:'#7B9AA7',orange:'#E86B16',orangePale:'#FFF4E8',white:'#FFFFFF',muted:'#647078',ink:'#252A2D'};
function shape(slide,geom,x,y,w,h,fill='none',stroke='none',sw=0,name=''){
 return slide.shapes.add({geometry:geom,name,position:{left:x,top:y,width:w,height:h},fill,line:{style:'solid',fill:stroke,width:sw}});
}
function txt(slide,text,x,y,w,h,size=18,color=C.ink,bold=false,opts={}){
 const s=shape(slide,'textbox',x,y,w,h,'none','none',0,opts.name||''); s.text=text;
 s.text.style={fontFamily:'Arial',fontSize:size,color,bold,verticalAlignment:opts.valign||'middle',alignment:opts.align||'left',wrap:true};
 return s;
}
function box(slide,x,y,w,h,fill,stroke=C.line,r=10){return shape(slide,'roundRect',x,y,w,h,fill,stroke,1.5);}
function base(title,num,titleSize=34){
 const s=p.slides.add(); s.background.fill=C.white;
 txt(s,title,54,17,850,47,titleSize,C.green,true,{name:'Slide title'});
 shape(s,'rect',48,66,864,2,C.green,'none',0,'Title rule');
 // visual footer consistent with the existing white/green title slides
 shape(s,'rect',48,502,864,1,C.line,'none',0,'Footer rule');
 txt(s,'Monthly Meeting  ·  10/26/2026',600,507,280,22,12,C.muted,false,{align:'right'});
 txt(s,String(num),886,507,24,22,12,C.muted,false,{align:'right'});
 return s;
}
// 1 — declarative configuration contract
{
 const s=base('Configuration file update',3);
 txt(s,'A reviewed catalog can describe model edits; the compiler applies only declared templates.',56,79,845,29,17,C.gray,false);
 // Left: schema field tree
 txt(s,'FORMULATION CONTROL SPEC',60,120,380,19,13,C.green,true);
 const fields=[
  ['control_id · operation_kind','scenario  |  formulation'],
  ['routines · target_scope','DCOPF / ED / UC / ACOPF'],
  ['variables · objective_terms','what the template adds or changes'],
  ['constraint_templates','named, versioned, reviewed blocks'],
  ['parameters · bounds · units','typed values with validation rules'],
  ['preconditions · conflicts · reset','safe composition and recovery']
 ];
 let y=148;
 for(let i=0;i<fields.length;i++){
   const fill=i===0?C.pale:C.white;
   box(s,58,y,386,45,fill,i===0?'#A8C9A8':C.line,5);
   txt(s,fields[i][0],72,y+4,355,18,14,C.ink,true);
   txt(s,fields[i][1],72,y+22,354,18,12,C.gray,false);
   y+=49;
 }
 // Right: runtime path
 txt(s,'DECLARATION → MODEL CHANGE',504,120,390,19,13,C.green,true);
 const nodes=[
  {x:500,w:112,head:'Config',body:'reviewed\ntemplate',fill:C.pale,stroke:'#85B785'},
  {x:632,w:116,head:'Validate',body:'routine · types\nbounds · conflicts',fill:C.blue,stroke:C.blueLine},
  {x:768,w:140,head:'Compile',body:'AMS model\noperations',fill:C.orangePale,stroke:'#E7A36E'}
 ];
 for(const n of nodes){box(s,n.x,190,n.w,100,n.fill,n.stroke,8);txt(s,n.head,n.x+8,203,n.w-16,26,18,C.dark,true,{align:'center'});txt(s,n.body,n.x+9,237,n.w-18,42,13,C.gray,false,{align:'center'});}
 txt(s,'›',612,223,20,35,26,C.green,true,{align:'center'});txt(s,'›',748,223,20,35,26,C.green,true,{align:'center'});
 box(s,532,325,344,82,'#F7F8F7',C.line,8);
 txt(s,'Live AMS model',552,335,300,24,16,C.dark,true);
 txt(s,'Only validated, routine-compatible edits are applied.',552,365,300,26,14,C.gray,false);
 txt(s,'Design direction',60,463,130,19,12,C.orange,true);
 txt(s,'Replace one-off executor branches with configuration-driven, validated operations.',190,459,700,27,15,C.dark,false);
}
// 2 — classification
{
 const s=base('Revise parameter classification',4);
 txt(s,'Route by what the request changes in the optimization model.',56,79,840,27,17,C.gray,false);
 // two-column contrast, with structural edits emphasized
 txt(s,'SCENARIO INPUT',62,123,365,23,15,C.blueLine,true);
 txt(s,'FORMULATION CHANGE',515,123,390,23,15,C.orange,true);
 box(s,58,153,390,222,'#F5F8F9','#B7C8CE',9);
 box(s,506,153,396,222,C.orangePale,'#E6A36C',9);
 txt(s,'Updates values or availability within an existing model template',80,169,350,45,18,C.dark,true);
 const left=['Load level / demand scaling','Line status or rating','Routine setting such as config.t'];
 let y=230; for(const a of left){txt(s,'•',82,y,18,22,17,C.blueLine,true);txt(s,a,104,y,320,22,15,C.ink,false);y+=34;}
 txt(s,'Model data changes; decision structure stays fixed.',80,340,350,24,13,C.gray,false);
 txt(s,'Changes variables, objective terms, or feasible set',530,169,350,45,18,C.dark,true);
 const right=['Add or revise an objective cost term','Add / remove a named constraint block','Replace PV with an IBR: variables + limits + cost'];
 y=230; for(const a of right){txt(s,'•',532,y,18,22,17,C.orange,true);txt(s,a,554,y,330,25,15,C.ink,false);y+=34;}
 txt(s,'Prioritize these structural edits in the formulation catalog.',530,340,350,24,13,C.orange,true);
 // decision rule band
 shape(s,'rect',58,396,844,1,C.line,'none',0);
 txt(s,'CLASSIFICATION RULE',62,408,180,18,12,C.green,true);
 txt(s,'Data / availability only  →  scenario     |     New variables, objective terms, or constraints  →  formulation',62,431,828,36,15,C.dark,true);
}
// 3 — execution correctness
{
 const s=base('How to ensure the LLM executes a prompt correctly?',5,27);
 txt(s,'Treat execution as a verifiable transaction, not a reasoning claim.',56,79,845,26,17,C.gray,false);
 const steps=[
  {x:56,w:126,k:'1  REQUEST',h:'“Set a = 0.9”',b:'Target + value'},
  {x:202,w:145,k:'2  PLAN',h:'a := 0.9',b:'Typed, scoped\noperation'},
  {x:367,w:145,k:'3  PRECHECK',h:'a exists · in range',b:'Routine · units ·\npreconditions'},
  {x:532,w:145,k:'4  APPLY',h:'Declared edit',b:'One controlled\nmodel operation'},
  {x:697,w:207,k:'5  READ BACK',h:'Actual value = 0.9',b:'Model diff + solve\nfeasibility check'}
 ];
 for(const st of steps){
  const fill=st.x===697?'#EAF5EA':C.white, edge=st.x===697?'#80B780':C.line;
  box(s,st.x,147,st.w,124,fill,edge,8);
  txt(s,st.k,st.x+10,158,st.w-20,18,11,st.x===697?C.green:C.muted,true,{align:'center'});
  txt(s,st.h,st.x+10,182,st.w-20,36,st.x===56?15:16,C.dark,true,{align:'center'});
  txt(s,st.b,st.x+9,222,st.w-18,38,12,C.gray,false,{align:'center'});
 }
 txt(s,'›',183,188,18,32,23,C.green,true,{align:'center'});txt(s,'›',348,188,18,32,23,C.green,true,{align:'center'});txt(s,'›',513,188,18,32,23,C.green,true,{align:'center'});txt(s,'›',678,188,18,32,23,C.green,true,{align:'center'});
 // verification record
 txt(s,'EXECUTION RECORD',60,305,220,19,13,C.green,true);
 const checks=[['Intent','target a · requested value 0.9'],['Applied state','read back from the live AMS object'],['Model effect','changed fields / objective / constraints'],['Outcome','solver status + feasibility checks']];
 let x=60;for(const [h,b] of checks){
   box(s,x,331,202,83,'#F7F8F7',C.line,6);
   txt(s,h,x+12,340,178,22,14,C.dark,true);
   txt(s,b,x+12,365,178,38,12,C.gray,false);
   x+=214;
 }
 txt(s,'Block and ask when target, units, bounds, or post-apply state do not match.',60,445,820,27,15,C.orange,true);
}
const out='.pptx_work/build/inserted_slides.pptx';
await (await PresentationFile.exportPptx(p)).save(out);
for(let i=0;i<p.slides.items.length;i++){
 const b=await p.export({slide:p.slides.items[i],format:'png',scale:1}); await fs.writeFile(`.pptx_work/build/new-${i+1}.png`,new Uint8Array(await b.arrayBuffer()));
}
console.log(out,p.slides.items.length);
