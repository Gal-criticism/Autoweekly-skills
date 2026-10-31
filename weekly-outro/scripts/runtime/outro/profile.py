"""Bind a reachable outro hierarchy and its existing essential text instances."""
import hashlib
import math
from pathlib import Path
import re

ROLE_ALIASES={'后期':'后期','时轴/目录':'时轴','轴校':'轴校','文案':'文案',
              '素材汇总':'素材汇总','新闻采集':'新闻采集','配音':'配音',
              '音频后期':'修音','制作辅助':'制作辅助','设计':'设计'}

def compile_profile(raw,template,config=None):
    config=config or {}
    template=Path(template).resolve(strict=True)
    comps={c['id']:c for c in raw['comps']}
    choices=[q for q in raw['queues'] if q['done'] and len(q['modules'])==1
             and q['modules'][0]['settings'].get('Format')=='Voukoder'
             and (q['modules'][0].get('file') or '').lower().endswith('.mp4')]
    if config.get('reference_queue_index'):
        choices=[q for q in choices if q['index']==config['reference_queue_index']]
    if not choices:raise ValueError('No completed Voukoder MP4 reference queue')
    if len({q['comp_id'] for q in choices})!=1:raise ValueError('Ambiguous output compositions; configure reference_queue_index')
    ref=max(choices,key=lambda q:q['index']);main=comps[ref['comp_id']]
    if abs(ref['start'])>1e-6 or abs(ref['duration']-main['duration'])>1e-6:
        raise ValueError('Reference queue must cover the complete outro')
    reached=[]
    def visit(cid):
        if cid in reached:return
        reached.append(cid)
        for l in comps[cid]['layers']:
            if l.get('enabled',True) and l.get('source_id') in comps:visit(l['source_id'])
    visit(main['id'])
    slots=[];bridges=[];groups=[];fonts=set();audio=False
    for cid in reached:
        c=comps[cid];group=None
        for l in c['layers']:
            if not l.get('enabled',True):continue
            for e in l.get('effects',[]):
                if e.get('enabled') and not e.get('available',True):raise ValueError('Missing active effect: '+str(e))
            if l.get('font'):fonts.add(l['font'])
            if l.get('has_audio') and l.get('source_id') not in comps:audio=True
            source=comps.get(l.get('source_id'))
            if source and source['name'] in ROLE_ALIASES and c['name'].startswith('STAFF'):
                group={'role':ROLE_ALIASES[source['name']],'comp_id':cid,'comp_name':c['name'],
                       'title_layer':l['id'],'fixed':ROLE_ALIASES[source['name']] in config.get('fixed_roles',[]),'slots':[]}
                groups.append(group)
            elif source and re.fullmatch(r'staff(?:\s*-?up|-left)?',source['name'],re.I) and c['name'].startswith('STAFF'):
                if group is None:raise ValueError('Name instance has no role title: '+l['id'])
                texts=[e for e in l.get('essential',[]) if 'text' in e]
                if len(texts)!=1:raise ValueError('Ambiguous essential text: '+l['id'])
                e=texts[0]
                if e.get('keys'):raise ValueError('Keyframed essential text unsupported: '+l['id'])
                if e.get('expression') and re.sub(r'\s','',e.get('expression_text','')).rstrip(';')!='thisLayer.name':
                    raise ValueError('Unsupported essential text expression: '+l['id'])
                fonts.add(e['font'])
                bound={k:l[k] for k in ('id','index','name','source_id')}
                bound.update(comp_id=cid,layer_index=l['index'],layer_name=l['name'],essential_path=e['path'],
                             old_text=e['text'],font=e['font'],font_size=e['font_size'],
                             expression=e.get('expression_text',''),expression_enabled=e.get('expression',False),
                             position=l.get('layout',{}).get('position',[1080,540]),
                             scale=l.get('layout',{}).get('scale',[100,100]),
                             max_width=config.get('text_max_width',420),max_height=config.get('text_max_height',120))
                group['slots'].append(bound)
            file=l.get('source_file')
            if file and re.fullmatch(r'图片\d+调整',c['name']):
                slot={k:l[k] for k in ('id','source_id','width','height')}
                slot.update(comp_id=cid,comp_name=c['name'],layer_index=l['index'],layer_name=l['name'],source_file=file)
                (bridges if '片尾' in l['name'] else slots).append(slot)
    if not groups or any(not g['slots'] for g in groups):raise ValueError('Incomplete STAFF role groups')
    if len({g['role'] for g in groups})!=len(groups):raise ValueError('Duplicate active role groups')
    if len(bridges)!=1 or not slots:raise ValueError('Need image slots and exactly one seasonal outro image')
    windows={};mappings={}
    def timing(cid,base,scale,lo,hi,ancestors):
        if cid in ancestors:return
        windows.setdefault(cid,[]).append((lo,hi))
        mappings.setdefault(cid,[]).append((base,scale))
        for l in comps[cid]['layers']:
            if not l.get('enabled',True) or l.get('source_id') not in comps:continue
            if l.get('stretch',100)<=0:raise ValueError('Reverse-time nesting requires explicit sampling')
            a=max(lo,base+scale*l.get('in_point',0));b=min(hi,base+scale*l.get('out_point',main['duration']))
            if b>a:timing(l['source_id'],base+scale*l.get('start',0),scale*l.get('stretch',100)/100,a,b,ancestors+[cid])
    timing(main['id'],0,1,0,ref['duration'],[])
    for g in groups:
        g['visible_intervals']=windows.get(g['comp_id'],[])
        if not g['visible_intervals']:raise ValueError('Role has no visible output interval: '+g['role'])
        base,scale=mappings[g['comp_id']][0];a,b=g['visible_intervals'][0]
        g['readable_local_time']=((a+b)/2-base)/scale
        g['slots'].sort(key=lambda s:(round(s['position'][1]/20),s['position'][0]))
        for s in g['slots']:
            # Restrict to half the nearest row-neighbour distance, with margins.
            peers=[p for p in g['slots'] if p['id']!=s['id'] and abs(p['position'][1]-s['position'][1])<40
                   and abs(p['position'][0]-s['position'][0])>50]
            if peers:
                distance=min(abs(p['position'][0]-s['position'][0]) for p in peers)
                s['max_width']=min(s['max_width'],max(1,distance-32)/max(abs(s['scale'][0]/100),.01))
    groups.sort(key=lambda g:(g['visible_intervals'][0][0],g['slots'][0]['layer_index']))
    slots.sort(key=lambda s:int(re.search(r'\d+',s['comp_name'])[0]))
    audio_mode=ref['modules'][0]['settings'].get('Output Audio')
    audio_expected=audio if audio_mode=='Auto' else {'On':True,'Off':False}.get(audio_mode)
    return {'schema_version':1,'template':str(template),'template_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),
            'ae_version':raw.get('version'),'comp_id':main['id'],'main_comp_name':main['name'],
            'slots':slots,'bridge':bridges[0],'groups':groups,'fonts':sorted(fonts),'reference_queue':ref,
            'reachable_comp_ids':reached,'expected':{'width':main['width'],'height':main['height'],'fps':main['fps'],
             'duration':ref['duration'],'frames':round(ref['duration']*main['fps']),'audio_expected':audio_expected},
            'sample_times':sorted({round((a+b)/2,6) for g in groups for a,b in g['visible_intervals']}),
            'private_encoder_parameters':'inherited_not_individually_readable'}

def plan_text(groups,roles):
    result=[]
    for group in groups:
        if group['fixed']:continue
        members=roles.get(group['role'],[])
        if not members or any(not isinstance(x,str) or not x.strip() for x in members):
            raise ValueError('Missing or empty role: '+group['role'])
        if len(set(members))!=len(members):raise ValueError('Duplicate member within '+group['role'])
        capacity=len(group['slots']);per_slot=math.ceil(len(members)/capacity)
        for index,slot in enumerate(group['slots']):
            names=members[index*per_slot:(index+1)*per_slot]
            result.append(dict(slot,role=group['role'],text='\r'.join(names),members=names,enabled=bool(names)))
    for item in result:
        if not item['enabled'] or 'position' not in item:continue
        peers=[p for p in result if p['enabled'] and p['role']==item['role'] and p['id']!=item['id']
               and abs(p['position'][0]-item['position'][0])<50]
        if peers:
            gap=min(abs(p['position'][1]-item['position'][1]) for p in peers)
            # The template's rows are 70 px apart; CJK glyphs in its italic
            # font can occupy 65.5 px at the original 48 px size.
            item['max_height']=min(item['max_height'],max(1,gap-2)/max(abs(item['scale'][1]/100),.01))
    return result
