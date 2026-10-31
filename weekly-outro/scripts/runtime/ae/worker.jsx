(function () {
    // A command accidentally routed to a pre-existing AE has a different environment.
    // Do not open, save, configure or quit that instance.
    if ($.getenv('WEEKLY_OUTRO_RUN_TOKEN') !== JOB.token) { app.exitCode = 71; return; }
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
        var target = new File(JOB.result_path);
        var f = new File(JOB.result_path+'.tmp'); f.encoding = 'UTF-8';
        if (!f.open('w')) throw new Error('Cannot write worker receipt: '+f.error);
        f.write(encode(value)); f.close();
        if(!f.rename(target.name)) throw new Error('Cannot publish worker receipt');
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
    function overrides(l) {
        var a=[];
        function walk(g,path) {
            if(!g) return;
            for(var oi=1;oi<=g.numProperties;oi++) {
                var p=g.property(oi), indexes=path.concat([oi]);
                if(p.propertyType!==PropertyType.PROPERTY) {walk(p,indexes);continue;}
                if(p.propertyValueType===PropertyValueType.NO_VALUE) continue;
                var v=p.value, e={path:indexes,name:p.name,match:p.matchName,keys:p.numKeys};
                try {e.expression=p.expressionEnabled;e.expression_text=p.expression;} catch(ignore){}
                if(v && typeof v.text==='string') {
                    e.text=v.text;e.font=v.font;e.font_size=v.fontSize;
                } else if(typeof v==='string' || typeof v==='number' || v instanceof Array) e.value=v;
                a.push(e);
            }
        }
        try{walk(l.essentialProperty,[]);}catch(err){a.push({error:String(err)});}
        return a;
    }
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
        if(JOB.operation==='layout') {
            var layouts=[];
            for(var li=0;li<JOB.groups.length;li++) {
                var lg=JOB.groups[li],lc=byId(lg.comp_id);
                for(var lj=0;lj<lg.slots.length;lj++) {
                    var ls=lg.slots[lj],ll=lc.layer(ls.layer_index);
                    if(ll.name!==ls.layer_name)throw new Error('Layout binding drift');
                    var probe=ll.property('ADBE Effect Parade').addProperty('ADBE Point3D Control');
                    var projected;
                    try {
                        if(ll.timeRemapEnabled || !(ll.source.layer(1) instanceof TextLayer))
                            throw new Error('Unsupported name source timing or hierarchy');
                        var sourceTime=(lg.readable_local_time-ll.startTime)*100/ll.stretch;
                        // Project the inner text origin, not the precomp anchor:
                        // existing instances have different anchor offsets.
                        probe.property(1).expression='thisLayer.toComp(thisLayer.source.layer(1).toComp(thisLayer.source.layer(1).anchorPoint.valueAtTime('+sourceTime+'),'+sourceTime+'))';
                        projected=probe.property(1).valueAtTime(lg.readable_local_time,false);
                        if(probe.property(1).expressionError)throw new Error(probe.property(1).expressionError);
                    } finally {probe.remove();}
                    layouts.push({id:ls.id,comp_id:lg.comp_id,layer_index:ls.layer_index,
                        parent:ll.parent?ll.parent.index:null,time:lg.readable_local_time,position:projected,
                        scale:ll.property('ADBE Transform Group').property('ADBE Scale').valueAtTime(lg.readable_local_time,false)});
                }
            }
            result.layouts=layouts;
        } else if(JOB.operation==='measure') {
            var mc=app.project.items.addComp('__measure',2160,1080,1,1,60),ml=mc.layers.addText(''),md=[];
            for(var mi=0;mi<JOB.text_plan.length;mi++) {
                var mr=JOB.text_plan[mi];if(!mr.enabled)continue;
                var mp=byId(mr.comp_id).layer(mr.layer_index).essentialProperty;
                for(var mj=0;mj<mr.essential_path.length;mj++)mp=mp.property(mr.essential_path[mj]);
                var mv=mp.value;mv.text=mr.text;mv.fontSize=mr.font_size;
                ml.property('ADBE Text Properties').property('ADBE Text Document').setValue(mv);
                md.push({id:mr.id,text:mr.text,font:mv.font,font_size:mv.fontSize,leading:mv.leading,autoLeading:mv.autoLeading,
                    boxText:mv.boxText,rect:ml.sourceRectAtTime(0,false)});
            }
            mc.remove();result.measurements=md;
        } else if(JOB.operation==='inspect') {
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
                            width:source?source.width:null,height:source?source.height:null,has_audio:source?source.hasAudio:false,effects:[],
                            start:l.startTime,stretch:l.stretch,essential:overrides(l),layout:{
                                position:l.property('ADBE Transform Group').property('ADBE Position').value,
                                scale:l.property('ADBE Transform Group').property('ADBE Scale').value}};
                        fx=l.property('ADBE Effect Parade');
                        if(fx) for(k=1;k<=fx.numProperties;k++) { effects=fx.property(k); ld.effects.push({name:effects.name,match:effects.matchName,enabled:effects.enabled,available:!!known[effects.matchName]}); }
                        if(l instanceof TextLayer) { var td=l.property('ADBE Text Properties').property('ADBE Text Document').value; ld.text=td.text; ld.font=td.font;
                            var tp=l.property('ADBE Text Properties').property('ADBE Text Document');
                            ld.text_keys=tp.numKeys;ld.text_expression=tp.expressionEnabled;
                            ld.text_animators=l.property('ADBE Text Properties').property('ADBE Text Animators').numProperties;
                            ld.font_size=td.fontSize;ld.box_text=td.boxText;
                            ld.box_size=td.boxText?td.boxTextSize:null;
                            ld.bounds=l.sourceRectAtTime(0,false);
                            ld.text_style={font:td.font,fontSize:td.fontSize,fillColor:td.fillColor,
                                applyFill:td.applyFill,applyStroke:td.applyStroke,justification:String(td.justification),
                                tracking:td.tracking,leading:td.leading,autoLeading:td.autoLeading};
                            ld.state=layerState(l); }
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
            var textChanges=[],measureComp=null,measureLayer=null;
            if(!JOB.text_plan || !JOB.text_plan.length) throw new Error('Missing frozen roster text plan');
            measureComp=app.project.items.addComp('__outro_measure_'+JOB.token,2160,1080,1,1,60);
            measureLayer=measureComp.layers.addText('');
            function measured(td,text,size) {
                td.text=text;td.fontSize=size;
                measureLayer.property('ADBE Text Properties').property('ADBE Text Document').setValue(td);
                return measureLayer.sourceRectAtTime(0,false);
            }
            function wrapped(td,text,size,width) {
                var paragraphs=text.split('\r'),out=[],pi,ci,line,next;
                for(pi=0;pi<paragraphs.length;pi++) {
                    line='';
                    for(ci=0;ci<paragraphs[pi].length;ci++) {
                        next=line+paragraphs[pi].charAt(ci);
                        if(line && measured(td,next,size).width>width) {out.push(line);line=paragraphs[pi].charAt(ci);}
                        else line=next;
                    }
                    out.push(line);
                }
                return out.join('\r');
            }
            for(var ti=0;ti<JOB.text_plan.length;ti++) {
                var tr=JOB.text_plan[ti],tc=byId(tr.comp_id),tl=tc.layer(tr.layer_index);
                if(tl.name!==tr.layer_name || tl.source.id!==tr.source_id) throw new Error('Stale text instance: '+tr.id);
                var tb=layerState(tl),ep=tl.essentialProperty;
                for(var ei=0;ei<tr.essential_path.length;ei++)ep=ep.property(tr.essential_path[ei]);
                if(ep.numKeys || ep.expressionEnabled!==tr.expression_enabled || ep.expression!==tr.expression)
                    throw new Error('Text expression drift: '+tr.id);
                var original=ep.value;
                if(original.text!==tr.old_text || original.font!==tr.font || original.fontSize!==tr.font_size)
                    throw new Error('Text style drift: '+tr.id);
                if(!tr.enabled) {
                    tl.enabled=false;tb.enabled=false;
                    if(encode(tb)!==encode(layerState(tl)))throw new Error('Disabled name animation changed');
                    textChanges.push({id:tr.id,role:tr.role,enabled:false,state_preserved:true});continue;
                }
                var fit=null,display=null,chosenSize=null;
                for(var percent=100;percent>=90;percent--) {
                    var size=tr.font_size*percent/100;
                    display=wrapped(ep.value,tr.text,size,tr.max_width);
                    var rect=measured(ep.value,display,size);
                    if(rect.width<=tr.max_width+0.1 && rect.height<=tr.max_height+0.1) {
                        fit=rect;chosenSize=size;break;
                    }
                }
                if(!fit) {
                    result.overflow={id:tr.id,text:tr.text,display:display,rect:rect,
                        max_width:tr.max_width,max_height:tr.max_height,size:size};
                    throw new Error('Text overflow even at 90%: '+tr.role+'/'+tr.text+' '+encode(result.overflow));
                }
                if(chosenSize!==tr.font_size || !tr.expression_enabled) {
                    var td=ep.value;td.text=display;td.fontSize=chosenSize;ep.setValue(td);
                }
                if(tr.expression_enabled)tl.name=display;
                var readback=ep.value;
                if(readback.text!==display || readback.font!==tr.font || Math.abs(readback.fontSize-chosenSize)>0.01)
                    throw new Error('Text readback mismatch: '+tr.id);
                if(readback.text.replace(/\r/g,'')!==tr.text.replace(/\r/g,''))throw new Error('Names truncated');
                tb.enabled=true;tl.enabled=true;
                if(encode(tb)!==encode(layerState(tl)))throw new Error('Name animation changed: '+tr.id);
                textChanges.push({id:tr.id,role:tr.role,enabled:true,text:display,members:tr.members,
                    font:readback.font,font_size:readback.fontSize,
                    bounds:{top:fit.top,left:fit.left,width:fit.width,height:fit.height},state_preserved:true});
            }
            measureComp.remove();
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
            // Capture every value we need before saving. Accessing the duplicated
            // Voukoder queue item after save can re-open its output module and hang.
            var outputFile = duplicate.outputModule(1).file ? duplicate.outputModule(1).file.fsName : null;
            app.project.save(new File(JOB.output_aep));
            result.project_path=filePath();result.queue_index=newIndex;
            result.reference_index=JOB.reference_index;
            result.output_file=outputFile;
            result.changes_count=changes.length;result.text_changes_count=textChanges.length;
        } else throw new Error('Unsupported operation: '+JOB.operation);
        result.status='ok';
    } catch(error) {
        result.error=String(error)+' (line '+error.line+')';
        result.project_path=filePath(); app.exitCode=72;
    }
    write(result);
})();
