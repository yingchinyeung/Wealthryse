// A read-only, fail-closed table-ID adapter for private desktop qualification.
// It does not change table contents or geometry and is not part of the release.
const position = row => row.bbox ?? (row.position && [row.position.left,
  row.position.top, row.position.width, row.position.height]);
const slide = row => Number.isInteger(row.slideIndex) ? row.slideIndex + 1 : row.slide;
function samePosition(old, row) {
  const before=position(old),after=position(row);
  if(before?.length!==4 || after?.length!==4) return false;
  return after.every((value,index)=>Math.abs(value-before[index])<0.011 || (
    // Approved slide 5 OOXML: outer frame 810px, explicit column sum 757.500157px.
    // Current imports expose the column sum. No geometry is edited here.
    index===2 && old.id==='tb/gjutwb61' && slide(old)===5
    && before[2]===810 && Math.abs(value-757.500157480315)<0.011));
}
export function tableAliases(reference, current) {
  const oldTables = reference.filter(row=>row.kind==='table');
  const newTables = current.filter(row=>row.kind==='table');
  if(oldTables.length!==11 || newTables.length!==11) throw new Error('Expected exactly eleven template tables');
  const aliases = new Map();
  const used = new Set();
  for(const old of oldTables) {
    const oldPosition=position(old);
    const matches=newTables.filter(row=>row.rows===old.rows && row.cols===old.cols
      && slide(row)===slide(old) && row.preview===old.preview
      && samePosition(old,row));
    if(matches.length!==1 || used.has(matches[0].id)) throw new Error('Missing, ambiguous or changed template table: '+JSON.stringify({
      reference:{id:old.id,slide:slide(old),position:oldPosition,rows:old.rows,cols:old.cols},
      sameOwner:newTables.filter(row=>slide(row)===slide(old)).map(row=>({id:row.id,position:position(row),rows:row.rows,cols:row.cols,previewMatches:row.preview===old.preview}))}));
    aliases.set(old.id,matches[0].id); used.add(matches[0].id);
  }
  const currentOther=new Map(current.filter(row=>row.id && row.kind!=='table').map(row=>[row.id,row]));
  for(const row of reference.filter(row=>row.id && !['table','chart','layout'].includes(row.kind))) {
    const now=currentOther.get(row.id);
    if(!now || now.kind!==row.kind || slide(now)!==slide(row)) throw new Error('Non-table element identities changed');
  }
  return aliases;
}

export function chartAliases(reference,current) {
  const before=reference.filter(row=>row.kind==='chart');
  const after=current.filter(row=>row.kind==='chart');
  if(before.length!==6 || after.length!==6) throw new Error('Expected six template charts');
  const aliases=new Map(),used=new Set();
  for(const old of before) {
    const matches=after.filter(row=>slide(row)===slide(old) && row.title===old.title
      && row.chartType===old.chartType && row.name===old.name && samePosition(old,row));
    if(matches.length!==1 || used.has(matches[0].id)) throw new Error('Missing, ambiguous or changed template chart');
    aliases.set(old.id,matches[0].id);used.add(matches[0].id);
  }
  return aliases;
}

export function explicitLegacyPointLineStyle(options) {
  const result=structuredClone(options);
  for(const series of result.series??[]) for(const point of series.points??[]) {
    const line=point.line;
    if(line && line.style===undefined && line.type===undefined) {
      if(Object.keys(line).some(key=>!['fill','width'].includes(key)) || line.width!==1
          || !['#00884A','#54BFAF','#C94F52'].includes(line.fill)) throw new Error('Unexpected legacy point line');
      point.line={style:'solid',...line};
    }
  }
  return result;
}
