"""Compile an explicit whitelist for the supported weekly intro template."""
import hashlib
from pathlib import Path
import re

SCENES = ('第一镜头', '第二镜', '第三镜头', '第四镜头')


def compile_profile(raw, template):
    template = Path(template).resolve(strict=True)
    if raw.get('missing'):
        raise ValueError('Template has missing media: ' + str(raw['missing']))
    comps = raw['comps']
    main = [c for c in comps if c['name'] == '总合成']
    if len(main) != 1 or any(sum(c['name'] == name for c in comps) != 1 for name in SCENES):
        raise ValueError('Unsupported or ambiguous template composition hierarchy')
    main = main[0]
    if (main['width'], main['height'], main['fps'], main['duration']) != (2160, 1080, 60, 12):
        raise ValueError('Template differs from the approved 2160x1080 / 60fps / 12s baseline')
    slots, bridges, fonts = [], [], set()
    for comp in comps:
        for layer in comp['layers']:
            if layer.get('font') and layer.get('enabled', True):
                fonts.add(layer['font'])
            if layer.get('enabled', True) and any(e.get('enabled') and not e.get('available', True) for e in layer.get('effects', [])):
                raise ValueError('Missing effect on ' + comp['name'] + '/' + layer.get('name', '?'))
            source = layer.get('source_file')
            if comp['name'] not in SCENES or not source or not layer.get('enabled',True):
                continue
            parts = source.replace('\\', '/').lower().split('/')
            if 'logo' in parts or 'logo' in layer['name'].lower() or Path(source).suffix.lower() in ('.psd', '.ai'):
                continue
            slot = {'id': layer['id'], 'comp_id': comp['id'], 'comp_name': comp['name'],
                    'layer_index': layer['index'], 'layer_name': layer['name'], 'source_id': layer['source_id'],
                    'source_file': source, 'width': layer['width'], 'height': layer['height']}
            if re.search(r'视频_底图_.*a\.', layer['name']):
                bridges.append(slot)
            else:
                slots.append(slot)
    if len(bridges) != 1 or not slots:
        raise ValueError('Need exactly one seasonal bridge and at least one image slot')
    slots.sort(key=lambda s: (SCENES.index(s['comp_name']), s['layer_index']))
    choices = [q for q in raw['queues'] if q['comp_id'] == main['id'] and q['done'] and len(q['modules']) == 1
               and q['modules'][0]['settings'].get('Format') == 'Voukoder'
               and (q['modules'][0]['file'] or '').lower().endswith('.mp4')]
    if not choices:
        raise ValueError('No completed Voukoder MP4 reference queue found')
    reference = max(choices, key=lambda q: q['index'])
    if reference['start'] != 0 or reference['duration'] != 12:
        raise ValueError('Reference queue is not the full 12-second intro')
    by_id = {c['id']: c for c in comps}
    visited = set()
    def has_audio(comp):
        if comp['id'] in visited:
            return False
        visited.add(comp['id'])
        return any(has_audio(by_id[l['source_id']]) if l.get('source_id') in by_id else bool(l.get('has_audio')) for l in comp['layers'])
    audio_mode = reference['modules'][0]['settings'].get('Output Audio', 'Unknown')
    audio = has_audio(main) if audio_mode == 'Auto' else {'On': True, 'Off': False}.get(audio_mode)
    return {'schema_version': 1, 'template': str(template), 'template_sha256': hashlib.sha256(template.read_bytes()).hexdigest(),
            'ae_version': raw.get('version'), 'comp_id': main['id'], 'slots': slots, 'bridge': bridges[0],
            'reference_queue': reference, 'fonts': sorted(fonts), 'expected': {'width':2160,'height':1080,'fps':60,
            'duration':12,'frames':720,'audio_expected':audio}, 'private_encoder_parameters': 'inherited_not_individually_readable'}
