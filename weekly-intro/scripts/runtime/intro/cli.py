"""JSON command-line orchestration for the weekly-intro skill."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from PIL import Image, ImageOps
from .candidates import scan_roots, build_contact_sheets, build_replacements
from .profile import compile_profile
from .fonts import require_fonts
from .transport import AEError, run_script, run_render, save_json, sha256, claim_dispatch
from .verification import doctor, verify_video


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def safe_week_id(value):
    value = str(value)
    if not value or len(value)>60 or value in ('.','..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value[-1:] in (' ','.'):
        raise ValueError('week_id must be a safe filename component, without path separators')
    return value


def copy_project(template, work):
    template = Path(template).resolve(strict=True)
    work = Path(work); work.mkdir(parents=True, exist_ok=False)
    target = work/template.name
    shutil.copy2(template, target)
    assets = template.parent/'(素材)'
    if assets.is_dir():
        shutil.copytree(assets, work/assets.name)
    return target


def inspect_template(config, output):
    root = Path(output).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError('Inspection output must be new/empty')
    root.mkdir(parents=True, exist_ok=True)
    template = Path(config['template']).resolve(strict=True)
    original_hash = sha256(template)
    project = copy_project(template, root/'work')
    raw = run_script(root, 'inspect', {'project':str(project)}, config, float(config.get('script_timeout',300)))
    if sha256(template) != original_hash:
        raise AEError('Master changed during inspection')
    profile = compile_profile(raw, template)
    profile['font_check'] = require_fonts(profile['fonts'])
    save_json(root/'profile.json', profile)
    return {'status':'inspected','profile':str(root/'profile.json'),'slots':len(profile['slots']),
            'reference_queue_index':profile['reference_queue']['index'],'expected':profile['expected']}


def prepare_task(task_path, output):
    task = read_json(task_path)
    week = safe_week_id(task['week_id'])
    config = read_json(task['config'])
    profile = read_json(task['profile'])
    template = Path(config['template']).resolve(strict=True)
    if str(template) != str(Path(profile['template']).resolve()) or sha256(template) != profile['template_sha256']:
        raise ValueError('Template changed; run inspect again before prepare')
    roots = [str(Path(p).resolve(strict=True)) for p in task['allowed_roots']]
    candidates = scan_roots(roots)
    if len(candidates) < len(profile['slots']):
        raise ValueError(f'Not enough distinct images: {len(candidates)} available, {len(profile["slots"])} slots')
    bridge = Path(task.get('bridge_image') or config.get('bridge_image','')).resolve()
    if not bridge.is_file():
        raise ValueError('Missing current seasonal bridge_image; configure or provide its path')
    config['bridge_image'] = str(bridge)
    root = Path(output).resolve()/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    project = copy_project(template, root/'work')
    (root/'output').mkdir()
    sheets = build_contact_sheets(candidates, root/'candidates')
    save_json(root/'candidates.json', candidates)
    save_json(root/'profile.json', profile)
    result = {'schema_version':1,'status':'prepared','week_id':week,'run_dir':str(root),'config':config,
              'allowed_roots':roots,'expected':profile['expected'],'project_copy':str(project),
              'template_sha256':profile['template_sha256'],'bridge_sha256':sha256(bridge),
              'video_path':str(root/'output'/(week+'片头.mp4')),'aep_path':str(root/'work'/(week+'片头.aep')),
              'contact_sheets':sheets,'slot_count':len(profile['slots']),'visual_review':{'status':'not_reviewed'}}
    save_json(root/'run.json', result)
    return result


def _normalise_bridge(run, profile, folder):
    source = Path(run['config']['bridge_image']).resolve(strict=True)
    if sha256(source) != run['bridge_sha256']:
        raise ValueError('Seasonal bridge changed after prepare')
    slot = profile['bridge']; size=(slot['width'],slot['height'])
    with Image.open(source) as im:
        oriented=ImageOps.exif_transpose(im)
        # Seasonal artwork must have exactly the same framing as the main video.
        if abs(oriented.width/oriented.height-size[0]/size[1])>0.001:
            raise ValueError('Seasonal bridge aspect does not match the template; supply the matching 底图a')
        image=oriented.convert('RGBA').resize(size,Image.Resampling.LANCZOS)
        target=folder/'season-bridge.png'; image.save(target)
    return dict(slot,slot_id=slot['id'],path=str(target))


def _without_output_paths(settings):
    return {k:v for k,v in settings.items() if k != 'Output File Info'}


def validate_run_paths(folder, run):
    folder=Path(folder).resolve(strict=True)
    if run.get('run_dir') and Path(run['run_dir']).resolve()!=folder:
        raise ValueError('run.json belongs to another batch')
    for key in ('project_copy','aep_path','video_path'):
        path=Path(run[key]).resolve()
        if path==folder or not path.is_relative_to(folder):
            raise ValueError(key+' escapes the current batch')


def render_run(folder):
    folder=Path(folder).resolve(strict=True); run=read_json(folder/'run.json'); profile=read_json(folder/'profile.json')
    validate_run_paths(folder,run)
    if run['status'] != 'prepared':
        raise ValueError('Run is not prepared; inspect status instead of resubmitting')
    if sha256(profile['template']) != profile['template_sha256']:
        raise ValueError('Master changed since prepare')
    if Path(run['project_copy']).resolve()==Path(profile['template']).resolve() or sha256(run['project_copy'])!=profile['template_sha256']:
        raise ValueError('Working AEP must be an unchanged copy of the inspected master')
    require_fonts(profile.get('fonts',[]))
    if Path(run['video_path']).exists() or Path(run['aep_path']).exists():
        raise FileExistsError('This batch already has an output; it will not be overwritten')
    readiness=doctor(run['config'])
    if readiness.get('status')!='ready' or not readiness.get('prerequisites_found') or readiness.get('setup_required'):
        raise ValueError('Preflight failed: '+json.dumps(readiness,ensure_ascii=False))
    pipeline={'pid':os.getpid(),'submitted_at':datetime.now().isoformat()}
    pipeline_receipt=claim_dispatch(folder,'pipeline',pipeline)
    try:
        selected=read_json(folder/'selection.json')
        replacements=build_replacements(selected,read_json(folder/'candidates.json'),profile['slots'],folder/'replacements',run['allowed_roots'])
        lookup={s['id']:s for s in profile['slots']}
        for r in replacements:
            r.update({k:lookup[r['slot_id']][k] for k in ('comp_id','layer_index','layer_name','source_id')})
        replacements.append(_normalise_bridge(run,profile,folder/'replacements'))
        save_json(folder/'replacements.json',replacements)
        run['status']='applying'; save_json(folder/'run.json',run)
        payload={'project':run['project_copy'],'output_aep':run['aep_path'],'output_video':run['video_path'],
                 'replacements':replacements,'reference_index':profile['reference_queue']['index'],'comp_id':profile['comp_id']}
        result=run_script(folder,'apply',payload,run['config'],float(run['config'].get('script_timeout',300)))
        reference=result['reference']; inherited=result['output_queue']
        old=profile['reference_queue']
        if reference['settings'] != old['settings'] or _without_output_paths(reference['modules'][0]['settings']) != _without_output_paths(old['modules'][0]['settings']):
            raise AEError('Reference queue settings drifted from inspection')
        if inherited['settings'] != reference['settings'] or _without_output_paths(inherited['modules'][0]['settings']) != _without_output_paths(reference['modules'][0]['settings']):
            raise AEError('Duplicated queue settings were not preserved')
        run.update(status='rendering',queue_index=result['queue_index']); save_json(folder/'run.json',run)
        run_render(folder,run['aep_path'],run['queue_index'],run['config'],float(run['config'].get('render_timeout',1800)))
        if not Path(run['video_path']).is_file() or Path(run['video_path']).stat().st_size == 0:
            raise AEError('aerender did not create the target MP4')
        if sha256(profile['template']) != profile['template_sha256']:
            raise AEError('Master hash changed')
        run['status']='rendered'; save_json(folder/'run.json',run)
        save_json(pipeline_receipt,dict(pipeline,status='rendered'))
        return run
    except Exception as error:
        run.update(status='failed',error=str(error)); save_json(folder/'run.json',run)
        save_json(pipeline_receipt,dict(pipeline,status='failed',error=str(error)))
        raise


def verify_run(folder):
    folder=Path(folder).resolve(strict=True); run=read_json(folder/'run.json')
    validate_run_paths(folder,run)
    if run['status']=='verified':
        raise ValueError('Already verified; use status. Existing verification is preserved')
    if run['status']!='rendered':
        raise ValueError('No successful render to verify')
    if (folder/'verification.json').exists():
        raise ValueError('Verification receipt already exists; use status. Existing receipt is preserved')
    dispatch={'pid':os.getpid(),'submitted_at':datetime.now().isoformat()}
    receipt=claim_dispatch(folder,'verify',dispatch)
    try:
        result=verify_video(folder,run['config'])
        run['status']='verified' if result['status']=='verified' else 'verification_failed'
        save_json(folder/'run.json',run)
        save_json(receipt,dict(dispatch,status=run['status']))
        return result
    except Exception as error:
        save_json(receipt,dict(dispatch,status='failed',error=str(error)))
        raise


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    d=commands.add_parser('doctor'); d.add_argument('--config')
    i=commands.add_parser('inspect'); i.add_argument('--config',required=True); i.add_argument('--output',required=True)
    p=commands.add_parser('prepare'); p.add_argument('--task',required=True); p.add_argument('--output',required=True)
    for name in ('render','verify','status'):
        command=commands.add_parser(name); command.add_argument('run_dir')
    args=parser.parse_args(argv)
    try:
        if args.command=='doctor': result=doctor(read_json(args.config) if args.config else {})
        elif args.command=='inspect': result=inspect_template(read_json(args.config),args.output)
        elif args.command=='prepare': result=prepare_task(args.task,args.output)
        elif args.command=='render': result=render_run(args.run_dir)
        elif args.command=='verify': result=verify_run(args.run_dir)
        else:
            result=read_json(Path(args.run_dir)/'run.json')
            result['dispatches']={p.name:read_json(p) for p in Path(args.run_dir).glob('*-dispatch.json')}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result.get('status') not in ('failed','setup_required','verification_failed') else 1
    except Exception as error:
        print(json.dumps({'status':'failed','stage':args.command,'error':str(error)},ensure_ascii=False,indent=2))
        return 1
