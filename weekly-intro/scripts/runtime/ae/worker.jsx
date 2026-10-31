(function () {
    // A command accidentally routed to a pre-existing AE has a different environment.
    // Do not open, save, configure or quit that instance.
    if ($.getenv('WEEKLY_INTRO_RUN_TOKEN') !== JOB.token) { app.exitCode = 71; return; }
    function encode(v) {
        var i, k, p = [];
        if (v === null || typeof v === 'undefined') return 'null';
        if (typeof v === 'string') return '"' + v.replace(/[\\"\x00-\x1f]/g, function(c) {
            if (c === '\\') return '\\\\'; if (c === '"') return '\\"';
            return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
        }) + '"';
        if (typeof v === 'number') return isFinite(v) ? String(v) : 'null';
        if (typeof v === 'boolean') return String(v);
        if (v instanceof Array) { for (i=0;i<v.length;i++) p.push(encode(v[i])); return '['+p.join(',')+']'; }
        for (k in v) if (v.hasOwnProperty(k)) p.push(encode(k)+':'+encode(v[k]));
        return '{'+p.join(',')+'}';
    }
    function write(value) {
        var f = new File(JOB.result_path); f.encoding = 'UTF-8';
        if (!f.open('w')) throw new Error('Cannot write worker receipt: '+f.error);
        f.write(encode(value)); f.close();
    }
    function filePath() { return app.project && app.project.file ? app.project.file.fsName : null; }
    function props(group) {
        var result=[],i,j,p,x;
        if (!group) return result;
        for(i=1;i<=group.numProperties;i++) {
            p=group.property(i); x={name:p.name,match:p.matchName};
            if(p.propertyType===PropertyType.PROPERTY) {
                try { x.expression=p.expression; x.expressionEnabled=p.expressionEnabled; } catch(ignore) {}
                try { x.value=p.value; } catch(ignore2) {}
                x.keys=[];
                for(j=1;j<=p.numKeys;j++) x.keys.push({time:p.keyTime(j),value:p.keyValue(j),inType:String(p.keyInInterpolationType(j)),outType:String(p.keyOutInterpolationType(j))});
            } else x.children=props(p);
            result.push(x);
        }
        return result;
    }
    function layerState(l) {
        return {enabled:l.enabled,threeD:l.threeDLayer,start:l.startTime,inPoint:l.inPoint,outPoint:l.outPoint,
            stretch:l.stretch,parent:l.parent?l.parent.index:null,transform:props(l.property('ADBE Transform Group')),
            masks:props(l.property('ADBE Mask Parade')),effects:props(l.property('ADBE Effect Parade'))};
    }
    function byId(id) { for(var i=1;i<=app.project.numItems;i++) if(app.project.item(i).id===id) return app.project.item(i); throw new Error('Item id missing: '+id); }
    function settings(obj) { try { return obj.getSettings(GetSettingsFormat.STRING); } catch(e) { return {read_error:String(e)}; } }
    function queueData(q,index) {
        var om=[],i;
        for(i=1;i<=q.numOutputModules;i++) om.push({index:i,file:q.outputModule(i).file?q.outputModule(i).file.fsName:null,settings:settings(q.outputModule(i))});
        return {index:index,comp_id:q.comp.id,comp_name:q.comp.name,done:q.status===RQItemStatus.DONE,status:String(q.status),
            start:q.timeSpanStart,duration:q.timeSpanDuration,settings:settings(q),modules:om};
    }
    var result={token:JOB.token,operation:JOB.operation,status:'failed',version:app.version,project_path:null};
    try {
        // Token match proves this worker inherited this invocation's environment.
        app.exitAfterLaunchAndEval=true;
        app.setSavePreferencesOnQuit(false);
        app.saveProjectOnCrash=false;
        app.beginSuppressDialogs();
        if (app.project && (app.project.file || app.project.numItems)) throw new Error('Fresh worker is not empty; refusing to replace a project');
        if(JOB.operation==='ping') { result.status='ok'; write(result); return; }
        if(!new File(JOB.project).exists) throw new Error('Project copy not found');
        app.open(new File(JOB.project));
        result.project_path=filePath();
        if(new File(result.project_path).fsName!==new File(JOB.project).fsName) throw new Error('Project copy identity mismatch');
        if(JOB.operation==='inspect') {
            var comps=[],footage=[],queues=[],missing=[],known={},i,j,item,l,source,effects,fx,k,ld;
            for(i=0;i<app.effects.length;i++) known[app.effects[i].matchName]=true;
            for(i=1;i<=app.project.numItems;i++) {
                item=app.project.item(i);
                if(item instanceof CompItem) {
                    var c={id:item.id,name:item.name,parent_id:item.parentFolder.id,width:item.width,height:item.height,
                        pixel_aspect:item.pixelAspect,duration:item.duration,fps:item.frameRate,layers:[]};
                    for(j=1;j<=item.numLayers;j++) {
                        l=item.layer(j); source=l instanceof AVLayer ? l.source : null;
                        ld={id:'c'+item.id+':l'+j,index:j,name:l.name,enabled:l.enabled,in_point:l.inPoint,out_point:l.outPoint,
                            source_id:source?source.id:null,source_name:source?source.name:null,source_file:source instanceof FootageItem&&source.file?source.file.fsName:null,
                            width:source?source.width:null,height:source?source.height:null,has_audio:source?source.hasAudio:false,effects:[]};
                        fx=l.property('ADBE Effect Parade');
                        if(fx) for(k=1;k<=fx.numProperties;k++) { effects=fx.property(k); ld.effects.push({name:effects.name,match:effects.matchName,enabled:effects.enabled,available:!!known[effects.matchName]}); }
                        if(l instanceof TextLayer) { var td=l.property('ADBE Text Properties').property('ADBE Text Document').value; ld.text=td.text; ld.font=td.font; }
                        c.layers.push(ld);
                    }
                    comps.push(c);
                } else if(item instanceof FootageItem) {
                    var ft={id:item.id,name:item.name,file:item.file?item.file.fsName:null,width:item.width,height:item.height,missing:item.footageMissing};
                    footage.push(ft); if(ft.missing) missing.push(ft);
                }
            }
            for(i=1;i<=app.project.renderQueue.numItems;i++) queues.push(queueData(app.project.renderQueue.item(i),i));
            result.comps=comps;result.footage=footage;result.queues=queues;result.missing=missing;
        } else if(JOB.operation==='apply') {
            var changes=[],r,comp,layer,before,after,imported,i;
            for(i=0;i<JOB.replacements.length;i++) {
                r=JOB.replacements[i]; comp=byId(r.comp_id); layer=comp.layer(r.layer_index);
                if(layer.name!==r.layer_name || layer.source.id!==r.source_id) throw new Error('Stale slot binding: '+r.slot_id);
                before=layerState(layer);
                imported=app.project.importFile(new ImportOptions(new File(r.path)));
                if(imported.width!==r.width || imported.height!==r.height) throw new Error('Replacement geometry mismatch: '+r.slot_id);
                layer.replaceSource(imported,false); after=layerState(layer);
                if(encode(before)!==encode(after)) throw new Error('Animation or effects changed: '+r.slot_id);
                changes.push({slot_id:r.slot_id,state_preserved:true});
            }
            var n=app.project.renderQueue.numItems;
            var reference=app.project.renderQueue.item(JOB.reference_index);
            if(reference.comp.id!==JOB.comp_id || reference.numOutputModules!==1) throw new Error('Reference queue mismatch');
            var duplicate=reference.duplicate();
            if(app.project.renderQueue.numItems!==n+1) throw new Error('Queue duplication count mismatch');
            duplicate.outputModule(1).file=new File(JOB.output_video);
            var newIndex=0,pathMatches=0,targetPath=new File(JOB.output_video).fsName;
            for(i=1;i<=n+1;i++) {
                var candidate=app.project.renderQueue.item(i);
                if(candidate.numOutputModules===1 && candidate.outputModule(1).file && candidate.outputModule(1).file.fsName===targetPath) {
                    newIndex=i;pathMatches++;
                }
            }
            if(pathMatches!==1) throw new Error('Cannot uniquely locate duplicated queue by output path');
            for(i=1;i<=n+1;i++) {
                // DONE/STOPPED items reject writes to .render in AE 22.
                if(i!==newIndex && app.project.renderQueue.item(i).status===RQItemStatus.QUEUED) app.project.renderQueue.item(i).render=false;
            }
            duplicate.render=true;
            var queued=0;
            for(i=1;i<=n+1;i++) if(app.project.renderQueue.item(i).status===RQItemStatus.QUEUED) queued++;
            if(queued!==1) throw new Error('More than one active queue item');
            if(app.project.renderQueue.item(newIndex).comp.id!==JOB.comp_id) throw new Error('New queue comp mismatch');
            app.project.save(new File(JOB.output_aep));
            result.project_path=filePath();result.queue_index=newIndex;result.reference=queueData(reference,JOB.reference_index);
            result.output_queue=queueData(duplicate,newIndex);result.changes=changes;
        } else throw new Error('Unsupported operation: '+JOB.operation);
        result.status='ok';
    } catch(error) {
        result.error=String(error)+' (line '+error.line+')';
        result.project_path=filePath(); app.exitCode=72;
    }
    write(result);
})();
