const {test}=require('node:test');
const assert=require('node:assert/strict');
const {boxesForPage}=require('../public/assets/ocr_highlights.js');
const snippet={pages:[{width:4542,height:6368}],regions:[{pageIdx:0}],highlights:[[{ulx:3328,uly:3007,lrx:3513,lry:3055,parentRegionIdx:0}]]};
test('absolute word boxes retain image coordinates and deduplicate repeated snippets',()=>{
 assert.deepEqual(boxesForPage([snippet,snippet],4542,6368),[{x:3328,y:3007,width:185,height:48}]);
});
test('mismatched dimensions and invalid coordinates do not draw on another image',()=>{
 assert.deepEqual(boxesForPage([snippet],100,200),[]);
 for(const edit of [{ulx:-1},{lrx:99999},{lrx:3328},{uly:NaN},{parentRegionIdx:9}]){
 const copy=structuredClone(snippet);Object.assign(copy.highlights[0][0],edit);
 assert.deepEqual(boxesForPage([copy],4542,6368),[]);
 }
 assert.deepEqual(boxesForPage([],4542,6368),[]);
});
test('opening adds all overlays without zoom; navigation alone focuses a word',()=>{
 const vm=require('node:vm'),fs=require('node:fs');
 let onOpen, fits=0, overlays=0;
 const status={textContent:''}, buttons={};
 for(const name of ['next','previous','fit']) buttons[name]={};
 const controls={querySelector:s=>s==='[role="status"]'?status:buttons[s.match(/data-ocr-(\w+)/)[1]],querySelectorAll:()=>Object.values(buttons)};
 const root={dataset:{ocrHighlights:JSON.stringify({snippets:[snippet]})},querySelector:()=>controls};
 const document={currentScript:{src:'http://localhost/assets/ocr_highlights.js'},head:{appendChild(){}},createElement:()=>({classList:{toggle(){}},setAttribute(){}}),addEventListener:(name,fn)=>{onOpen=fn;}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../public/assets/ocr_highlights.js'),'utf8'),{document});
 const image={getContentSize:()=>({x:4542,y:6368}),imageToViewportRectangle:()=>({})};
 const viewer={world:{getItemAt:()=>image},addOverlay:()=>overlays++,viewport:{fitBounds:()=>fits++,goHome(){}}};
 onOpen({target:{closest:()=>root},detail:{viewer}});
 assert.equal(overlays,1);
 assert.equal(fits,0,'initial page view must not zoom to a word');
 assert.match(status.textContent,/1 highlighted word/);
 buttons.next.onclick();
 assert.equal(fits,1);
 assert.match(status.textContent,/Match 1 of 1/);
});
