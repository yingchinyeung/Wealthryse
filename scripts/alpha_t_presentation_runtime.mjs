// Resolve the same artifact runtime in a desktop review or the pinned container.
import path from 'node:path';
import {pathToFileURL} from 'node:url';

// A data-URL module has no relative import base. Resolve only these reviewed
// shared helpers against the adapter itself, never against a caller's cwd.
export function bindTReportImports(source, adapterUrl) {
  let code=source;
  for (const name of ['report_content_labels.mjs','report_summary_label.mjs']) {
    const needle=`from './${name}'`;
    if (code.split(needle).length!==2) throw Error('Expected one shared report helper import: '+name);
    const resolved=new URL('./'+name,adapterUrl);
    if(resolved.protocol!=='file:')throw Error('Local report helper required');
    code=code.replace(needle,'from '+JSON.stringify(resolved.href));
  }
  if (/from\s+['"]\.\.?\//.test(code))throw Error('Unresolved relative import in T renderer');
  return code;
}

export function artifactToolUrl(env=process.env, resolveInstalled=specifier=>import.meta.resolve(specifier)) {
  return env.RUNTIME_NODE_MODULES
    ? pathToFileURL(path.join(env.RUNTIME_NODE_MODULES,'@oai/artifact-tool/dist/artifact_tool.mjs')).href
    : resolveInstalled('@oai/artifact-tool');
}

export function bindTemplateObjectIds(rows, owners) {
  const result=[];
  const used=new Set();
  for (const [old,index] of Object.entries(owners)) {
    const kind=old.startsWith('tb/')?'table':'chart';
    const found=rows.filter(row=>{
      if(row.kind!==kind)return false;
      const zero=Number.isInteger(row.slideIndex)?row.slideIndex:null;
      const one=Number.isInteger(row.slide)?row.slide-1:null;
      if(zero!==null && one!==null && zero!==one)throw Error('Conflicting template slide numbering');
      return (zero ?? one)===index;
    });
    if(found.length!==1 || !String(found[0].id).startsWith(old.slice(0,3)) || used.has(found[0].id))
      throw Error('Ambiguous T template object '+old);
    used.add(found[0].id);result.push([old,found[0].id]);
  }
  return result;
}
