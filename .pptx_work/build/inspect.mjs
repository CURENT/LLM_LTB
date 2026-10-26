import fs from 'node:fs/promises';
import { installNode } from '@oai/granola/node';
import { Presentation } from '@oai/artifact-tool';
installNode();
const p = await Presentation.load('/Users/jyin/Library/CloudStorage/OneDrive-UniversityofTennessee/0 Project/LTB/meeting/monthly/LTB monthly Meeting 10_26_2026 copy.pptx');
console.log('slides',p.slides.items.length);
const x=await p.inspect({kind:'slide,textbox,shape',maxChars:50000});
console.log(JSON.stringify(x,null,2));
for (const i of [1,2,5]) { if(p.slides.items[i]) { const b=await p.export({slide:p.slides.items[i],format:'png',scale:1}); await fs.writeFile(`.pptx_work/build/slide${i+1}.png`,new Uint8Array(await b.arrayBuffer())); }}
