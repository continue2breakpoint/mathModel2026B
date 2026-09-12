// DOM/Plotly integration smoke test with the real matrix HTTP endpoints.
// Start q2_dashboard.py, then: node script/q2_matrix_headless_test.js URL
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const BASE=process.argv[2]||'http://127.0.0.1:8055';
const html=fs.readFileSync(__dirname+'/q2_matrix_dashboard.html','utf8');
const elements=new Map();
function element(){return {value:'',textContent:'',children:[],disabled:false,handlers:{},append(x){this.children.push(x);},replaceChildren(){this.children=[];},setAttribute(){},addEventListener(k,f){this.handlers[k]=f;},removeAllListeners(k){delete this.handlers[k];},on(k,f){this.handlers[k]=f;},click(){if(this.onclick)return this.onclick();}};}
function get(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);}
for(const tag of html.matchAll(/<(input|select)\b[^>]*id="([^"]+)"[^>]*>/g)){
 const value=tag[0].match(/value="([^"]+)"/);get(tag[2]).value=value?value[1]:'';
}
Object.entries({rho_model:'uniform',metric:'Rmin',stat:'mean',condition:'all',resolution:'3',order:'3',sides:'96',angle_bins:'48'}).forEach(([k,v])=>get(k).value=v);
let plotted=null,downloaded=null;
const context=vm.createContext({document:{getElementById:get,createElement:element},console,Blob,setTimeout:fn=>fn(),URL:{createObjectURL:b=>(downloaded=b,'blob:dummy'),revokeObjectURL(){}},fetch:(url,opts)=>fetch(BASE+url,opts),Plotly:{react:async(id,traces,layout)=>{assert(traces[0].z.length===3);assert(traces[0].z.flat().some(Number.isFinite));plotted={traces,layout};}}});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
(async()=>{
 assert.equal(get('matrix').children.length,6);
 await get('compute').click();assert(plotted,get('status').textContent);
 await get('plot').handlers.plotly_click({points:[{x:850,y:520}]});assert(get('probe').textContent.includes('p_det='));
 get('addProbe').click();assert.equal(get('matrix').children[0].children.length,3);
 get('export').click();const json=JSON.parse(await downloaded.text());assert.equal(json.columns[1].status,'not_measure');
 get('exportMd').click();get('importText').value=await downloaded.text();await get('import').click();assert.equal(get('matrix').children[0].children.length,3);
 get('example').click();await get('compute').click();assert(!get('status').textContent.includes('失败'),get('status').textContent);
 get('zoom').click();assert(Number(get('xmax').value)-Number(get('xmin').value)<300);
 get('importText').value='{"columns":[{"point":[0,0],"status":"find","bearing_deg":0},{"point":[0,0],"status":"not_find"}]}';await get('import').click();assert(get('status').textContent.includes('导入失败'));
 console.log('PASS matrix editor, heatmap, probe, append point, JSON/Markdown export/import, example, zoom, invalid import');
})().catch(e=>{console.error(e);process.exitCode=1;});
