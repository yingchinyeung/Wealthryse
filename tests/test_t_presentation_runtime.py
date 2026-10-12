import os,shutil,subprocess,unittest
from pathlib import Path

class TPresentationRuntimeTests(unittest.TestCase):
    def test_desktop_and_installed_module_resolution(self):
        root=Path(__file__).resolve().parents[1]
        helper=root/'scripts/alpha_t_presentation_runtime.mjs'
        if not helper.exists():helper=Path('/app/scripts/alpha_t_presentation_runtime.mjs')
        node=os.environ.get('NODE_BINARY') or shutil.which('node')
        self.assertTrue(node,'Node is required for the report runtime test')
        code="""import assert from 'node:assert/strict';
import {artifactToolUrl,bindTemplateObjectIds} from %s;
let calls=0;
const resolve=x=>{assert.equal(x,'@oai/artifact-tool');calls++;return 'file:///app/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs';};
assert.equal(artifactToolUrl({},resolve),'file:///app/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs');
assert.equal(calls,1);
const local=artifactToolUrl({RUNTIME_NODE_MODULES:'/private/runtime/node_modules'},()=>{throw Error('Unexpected installed lookup');});
assert.ok(local.endsWith('/private/runtime/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs'));
assert.throws(()=>artifactToolUrl({},()=>{throw Error('Package missing');}),/Package missing/);
const owners={'tb/known':2,'ch/known':5};
assert.deepEqual(bindTemplateObjectIds([{id:'tb/local',kind:'table',slideIndex:2},{id:'ch/local',kind:'chart',slideIndex:5}],owners),[['tb/known','tb/local'],['ch/known','ch/local']]);
assert.deepEqual(bindTemplateObjectIds([{id:'tb/cloud',kind:'table',slide:3},{id:'ch/cloud',kind:'chart',slide:6}],owners),[['tb/known','tb/cloud'],['ch/known','ch/cloud']]);
assert.throws(()=>bindTemplateObjectIds([{id:'tb/a',kind:'table',slide:3},{id:'tb/b',kind:'table',slide:3}],{'tb/x':2}),/Ambiguous/);
assert.throws(()=>bindTemplateObjectIds([{id:'tb/a',kind:'table',slide:3,slideIndex:3}],{'tb/x':2}),/Conflicting/);
assert.throws(()=>bindTemplateObjectIds([],{ 'tb/x':2}),/Ambiguous/);
""" % __import__('json').dumps(helper.as_uri())
        run=subprocess.run([node,'--input-type=module','-e',code],capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr)

if __name__=='__main__':unittest.main()
