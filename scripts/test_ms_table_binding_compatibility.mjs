import test from 'node:test';
import assert from 'node:assert/strict';
import {tableAliases,chartAliases,explicitLegacyPointLineStyle} from './ms_table_binding_compatibility.mjs';
const rows=()=>Array.from({length:11},(_,i)=>({kind:'table',id:`tb/old${i}`,
  slide:i+1,rows:2,cols:2,bbox:[1,2,3,4],preview:`Header ${i}`}));
const current=source=>source.map((row,i)=>({...row,id:`tb/new${i}`,slideIndex:row.slide-1}));
test('eleven changed IDs bind by exact owner, dimensions, content and geometry',()=>{
  const old=rows(),now=current(old); assert.equal(tableAliases(old,now).get('tb/old0'),'tb/new0');
});
test('old runtime keeps identity mappings',()=>{
  const old=rows(); assert.equal(tableAliases(old,old).get('tb/old0'),'tb/old0');
});
test('missing table is rejected',()=>assert.throws(()=>tableAliases(rows(),current(rows()).slice(1)),/eleven/));
test('changed dimensions, caption or geometry are rejected',()=>{
  for(const [key,value] of [['rows',3],['preview','Other'],['bbox',[2,2,3,4]]]) {
    const old=rows(),now=current(old); now[0][key]=value;
    assert.throws(()=>tableAliases(old,now),/changed template/);
  }
});
test('changed owner is rejected',()=>{
  const old=rows(),now=current(old); now[0].slideIndex=15;
  assert.throws(()=>tableAliases(old,now),/changed template/);
});
test('non-table identities cannot be silently adapted',()=>{
  const old=rows(),now=current(old); old.push({kind:'textbox',id:'sh/old',slide:1});
  now.push({kind:'textbox',id:'sh/new',slideIndex:0});
  assert.throws(()=>tableAliases(old,now),/Non-table/);
});
test('only the pinned slide-five frame versus column-sum width is accepted',()=>{
  const old=rows(); old[4].id='tb/gjutwb61'; old[4].bbox[2]=810;
  const now=current(old); now[4].bbox=[1,2,757.5,4];
  assert.equal(tableAliases(old,now).size,11);
  now[4].bbox[2]=756;
  assert.throws(()=>tableAliases(old,now),/changed template/);
});
test('six chart IDs bind without changing owner, type, title or frame',()=>{
  const old=Array.from({length:6},(_,i)=>({kind:'chart',id:`ch/old${i}`,slide:i+6,
    chartType:'bar',title:`Chart ${i}`,name:'Chart',bbox:[1,2,3,4]}));
  const now=old.map((row,i)=>({...row,id:`ch/new${i}`,slideIndex:row.slide-1}));
  assert.equal(chartAliases(old,now).size,6);
  for(const [key,value] of [['title','Other'],['chartType','line'],['bbox',[1,2,4,4]]]) {
    const changed=now.map(row=>({...row}));changed[0][key]=value;
    assert.throws(()=>chartAliases(old,changed),/changed template chart/);
  }
  assert.throws(()=>chartAliases(old,now.slice(1)),/six/);
});
test('legacy solid point borders preserve values, signs, colours and all other options',()=>{
  const original={title:'Signed',series:[{values:[-0.2,0.1],points:[{idx:0,fill:'#C94F52',line:{fill:'#C94F52',width:1}}]}]};
  const changed=explicitLegacyPointLineStyle(original);
  assert.equal(changed.series[0].points[0].line.style,'solid');
  delete changed.series[0].points[0].line.style;
  assert.deepEqual(changed,original);
  assert.equal(original.series[0].points[0].line.style,undefined);
});
test('unknown missing-style point-border configurations are rejected',()=>{
  assert.throws(()=>explicitLegacyPointLineStyle({series:[{points:[{line:{fill:'#FFFFFF',width:1}}]}]}),/Unexpected/);
});
