import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH=Path(__file__).resolve().parents[1]/'weekly-outro/scripts/runtime/outro/profile.py'

def module():
    spec=importlib.util.spec_from_file_location('outro_profile_test',PATH)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def fixture():
    def layer(i,source,name,**kw):
        return dict(id=f'c2:l{i}',index=i,name=name,enabled=True,source_id=source,
                    source_name=name,source_file=None,effects=[],in_point=0,out_point=10,
                    start=0,stretch=100,essential=[],layout={'position':[1000,400+i*100],'scale':[100,100]},**kw)
    member=layer(2,4,'old')
    member['essential']=[{'path':[1],'text':'old','font':'font','font_size':48,'keys':0,'expression':True,'expression_text':'thisLayer.name;'}]
    title=layer(1,3,'后期')
    main={'id':1,'name':'export','width':1920,'height':1080,'fps':30,'duration':10,'layers':[layer(1,2,'STAFF2'),layer(2,5,'image')]}
    image=layer(1,8,'photo.png');image.update(source_file='E:/photo.png',width=800,height=600)
    bridge=layer(2,9,'春季片尾.png');bridge.update(source_file='E:/春季片尾.png',width=1280,height=720)
    raw={'comps':[main,{'id':2,'name':'STAFF2','width':1920,'height':1080,'layers':[title,member]},
           {'id':3,'name':'后期','layers':[]},{'id':4,'name':'staff','layers':[]},
           {'id':5,'name':'图片1调整','layers':[image,bridge]},
           {'id':6,'name':'STAFF2','layers':[layer(1,3,'unreachable')]}],
         'queues':[{'index':7,'comp_id':1,'done':True,'start':0,'duration':10,'settings':{'Quality':'Best'},
          'modules':[{'file':'E:/out.mp4','settings':{'Format':'Voukoder','Output Audio':'Auto'}}]}], 'missing':[]}
    return raw

class ProfileTest(unittest.TestCase):
    def test_actual_baseline_and_reachable_duplicate_names(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'t.aep';p.write_bytes(b't')
            out=module().compile_profile(fixture(),p,{})
            self.assertEqual(out['expected']['frames'],300)
            self.assertEqual(out['groups'][0]['role'],'后期')
            self.assertEqual(out['groups'][0]['slots'][0]['comp_id'],2)
            self.assertEqual(len(out['slots']),1)
            self.assertNotIn(6,out['reachable_comp_ids'])

    def test_missing_active_effect_or_unknown_expression_stops(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'t.aep';p.write_bytes(b't')
            raw=fixture();raw['comps'][1]['layers'][1]['essential'][0]['expression_text']='random()'
            with self.assertRaisesRegex(ValueError,'expression'):module().compile_profile(raw,p,{})
            raw=fixture();raw['comps'][1]['layers'][0]['effects']=[{'enabled':True,'available':False,'match':'missing'}]
            with self.assertRaisesRegex(ValueError,'effect'):module().compile_profile(raw,p,{})

    def test_empty_role_and_order_and_fixed_design(self):
        m=module()
        groups=[{'role':'后期','fixed':False,'slots':[{'id':'one'},{'id':'two'}]},
                {'role':'设计','fixed':True,'slots':[]}]
        with self.assertRaisesRegex(ValueError,'后期'):m.plan_text(groups,{'后期':[]})
        plan=m.plan_text(groups,{'后期':['A','B','C']})
        self.assertEqual([x['text'] for x in plan],['A\rB','C'])
        self.assertEqual([x['enabled'] for x in m.plan_text(groups,{'后期':['A']})],[True,False])

    def test_reading_order_uses_projected_glyph_origins_not_layer_indices(self):
        # Reproduce the template's anchor-offset trap: layer13 is below14
        # on screen although its outer Position has the smaller y value.
        m=module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'t.aep';p.write_bytes(b't')
            raw=fixture();group=raw['comps'][1];base=group['layers'][1]
            group['layers']=[group['layers'][0]]
            for index,y in [(12,735),(13,854),(14,796),(15,928),(16,992)]:
                layer=dict(base,id=f'c2:l{index}',index=index,name=f'old{index}',
                           layout={'position':[1495,y,0],'scale':[100,100,100]})
                group['layers'].append(layer)
            profile=m.compile_profile(raw,p,{})
            planned=m.plan_text(profile['groups'],{'后期':['弦叶','石竹','夜雨','Capercaillie']})
            self.assertEqual([(x['layer_index'],x['text']) for x in planned if x['enabled']],
                             [(12,'弦叶'),(14,'石竹'),(13,'夜雨'),(15,'Capercaillie')])

if __name__=='__main__':unittest.main()
