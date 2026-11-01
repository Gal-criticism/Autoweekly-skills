function renderCovers(job) {
    function json(v) {
        if(v===null||v===undefined)return "null";
        if(typeof v==="string")return '"'+v.replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/[\u0000-\u001f]/g,function(c){return "\\u"+("0000"+c.charCodeAt(0).toString(16)).slice(-4);})+'"';
        if(typeof v==="number"||typeof v==="boolean")return String(v);
        var a=[],k;
        if(v instanceof Array){for(k=0;k<v.length;k++)a.push(json(v[k]));return "["+a.join(",")+"]";}
        for(k in v)if(v.hasOwnProperty(k))a.push(json(k)+":"+json(v[k]));return "{"+a.join(",")+"}";
    }
    function write(report){var f=File(job.output_dir+"/covers_result.json");f.encoding="UTF8";if(!f.open("w"))throw Error("Cannot write manifest");f.write(json(report));f.close();}
    function find(container,id){for(var i=0;i<container.layers.length;i++){var l=container.layers[i];if(l.id===id)return l;if(l.typename==="LayerSet"){var found=find(l,id);if(found)return found;}}return null;}
    function one(doc,id){var l=find(doc,id);if(!l)throw Error("Missing layer "+id);return l;}
    function descriptor(layer){var ref=new ActionReference();ref.putIdentifier(stringIDToTypeID("layer"),layer.id);return executeActionGet(ref);}
    function box(layer){
        var d=descriptor(layer);
        if(d.hasKey(stringIDToTypeID("smartObjectMore"))){
            var t=d.getObjectValue(stringIDToTypeID("smartObjectMore")).getList(stringIDToTypeID("transform"));
            var xs=[],ys=[];for(var i=0;i<8;i+=2){xs.push(t.getDouble(i));ys.push(t.getDouble(i+1));}
            return [Math.min.apply(null,xs),Math.min.apply(null,ys),Math.max.apply(null,xs),Math.max.apply(null,ys)];
        }
        var b=layer.bounds;return [b[0].as("px"),b[1].as("px"),b[2].as("px"),b[3].as("px")];
    }
    function maskLinked(layer,value){var ref=new ActionReference();ref.putIdentifier(stringIDToTypeID("layer"),layer.id);var d=new ActionDescriptor(),to=new ActionDescriptor();d.putReference(charIDToTypeID("null"),ref);to.putBoolean(stringIDToTypeID("userMaskLinked"),value);d.putObject(charIDToTypeID("T   "),charIDToTypeID("Lyr "),to);executeAction(charIDToTypeID("setd"),d,DialogModes.NO);}
    function replace(doc,spec){
        var l=one(doc,spec.id);if(l.kind!==LayerKind.SMARTOBJECT)throw Error("Expected smart object "+spec.id);
        var old=box(l),info=descriptor(l),linked=null;
        if(info.hasKey(stringIDToTypeID("hasUserMask"))&&info.getBoolean(stringIDToTypeID("hasUserMask"))){linked=info.getBoolean(stringIDToTypeID("userMaskLinked"));if(linked)maskLinked(l,false);}
        doc.activeLayer=l;
        var d=new ActionDescriptor();d.putPath(charIDToTypeID("null"),File(spec.image));executeAction(stringIDToTypeID("placedLayerReplaceContents"),d,DialogModes.NO);
        var b=box(l),target=spec.box;
        var scale=Math.max((target[2]-target[0])/(b[2]-b[0]),(target[3]-target[1])/(b[3]-b[1]))*100;
        l.resize(scale,scale,AnchorPosition.MIDDLECENTER);b=box(l);
        l.translate(UnitValue((target[0]+target[2]-b[0]-b[2])/2,"px"),UnitValue((target[1]+target[3]-b[1]-b[3])/2,"px"));
        if(linked)maskLinked(l,true);
        l.name="CG "+spec.cg+" - "+spec.id;
        return {id:spec.id,image:spec.image,original_box:old,target_box:target,final_box:box(l),mask_linked:linked};
    }
    function fill(doc,id,rgb){
        doc.activeLayer=one(doc,id);var ref=new ActionReference();ref.putEnumerated(stringIDToTypeID("contentLayer"),charIDToTypeID("Ordn"),charIDToTypeID("Trgt"));
        var d=new ActionDescriptor(),to=new ActionDescriptor(),c=new ActionDescriptor();d.putReference(charIDToTypeID("null"),ref);
        c.putDouble(charIDToTypeID("Rd  "),rgb[0]);c.putDouble(charIDToTypeID("Grn "),rgb[1]);c.putDouble(charIDToTypeID("Bl  "),rgb[2]);
        to.putObject(charIDToTypeID("Clr "),charIDToTypeID("RGBC"),c);d.putObject(charIDToTypeID("T   "),stringIDToTypeID("solidColorLayer"),to);executeAction(charIDToTypeID("setd"),d,DialogModes.NO);
    }
    function fonts(container,report){
        for(var i=0;i<container.layers.length;i++){
            var l=container.layers[i];
            if(l.typename==="LayerSet")fonts(l,report);
            else if(l.kind===LayerKind.TEXT){
                app.activeDocument.activeLayer=l;
                var ref=new ActionReference(),d=new ActionDescriptor(),style=new ActionDescriptor();
                ref.putProperty(stringIDToTypeID("property"),stringIDToTypeID("textStyle"));
                ref.putEnumerated(stringIDToTypeID("textLayer"),stringIDToTypeID("ordinal"),stringIDToTypeID("targetEnum"));
                d.putReference(stringIDToTypeID("null"),ref);
                style.putString(stringIDToTypeID("fontPostScriptName"),"CormorantGaramond-Bold");
                style.putString(stringIDToTypeID("fontName"),"Cormorant Garamond");
                style.putString(stringIDToTypeID("fontStyleName"),"Bold");
                d.putObject(stringIDToTypeID("to"),stringIDToTypeID("textStyle"),style);
                executeAction(stringIDToTypeID("set"),d,DialogModes.NO);
                report.push({id:l.id,text:l.textItem.contents,font:l.textItem.font});
            }
        }
    }
    var report={run_id:job.run_id,status:"failed",photoshop:app.version,outputs:[]};
    var previous=app.documents.length?app.activeDocument:null,dialogs=app.displayDialogs,units=app.preferences.rulerUnits,doc=null;
    try {
        app.fonts.getByName("CormorantGaramond-Bold");
        // Refuse the whole batch before opening or modifying any document.
        for (var check=0;check<job.covers.length;check++) {
            var entry=job.covers[check], source=File(entry.template).fsName;
            for(var openIndex=0;openIndex<app.documents.length;openIndex++) {
                var opened=null;
                try { opened=app.documents[openIndex].fullName.fsName; } catch(ignore) {}
                if(opened===source) throw Error("Batch template is already open; preserve unsaved edits");
            }
            var outputDir=entry.output_dir || job.output_dir;
            for(var extIndex=0;extIndex<1;extIndex++) {
                var extension=["png"][extIndex];
                if(File(outputDir+"/"+entry.filename+"."+extension).exists)
                    throw Error("Output exists; not overwriting "+entry.filename+"."+extension);
            }
        }
        app.displayDialogs=DialogModes.NO;app.preferences.rulerUnits=Units.PIXELS;
        for(var i=0;i<job.covers.length;i++){
            var spec=job.covers[i];doc=app.open(File(spec.template));
            var item={index:spec.index,filename:spec.filename,width:doc.width.as("px"),height:doc.height.as("px"),replacements:[],fonts:[],issue:one(doc,939).textItem.contents};
            for(var h=0;h<spec.hide.length;h++)one(doc,spec.hide[h]).visible=false;
            for(var j=0;j<spec.replacements.length;j++)item.replacements.push(replace(doc,spec.replacements[j]));
            fill(doc,919,spec.tint);fonts(doc,item.fonts);
            if(spec.issue!==null && spec.issue!==undefined){
                var issueLayer=one(doc,939),oldIssue=issueLayer.textItem.contents;
                issueLayer.textItem.contents=/\d+/.test(oldIssue)?oldIssue.replace(/\d+/,String(spec.issue)):String(spec.issue);
                item.issue=issueLayer.textItem.contents;
            }
            if(spec.date){var date=one(doc,185),oldRight=box(date)[2];date.textItem.contents=spec.date;date.translate(UnitValue(oldRight-box(date)[2],"px"),UnitValue(0,"px"));item.date=spec.date;}
            // Keep only the layered T2 working document needed by optional refine.
            if(spec.date){
                var working=Folder(job.output_dir+"/working");if(!working.exists)working.create();
                var workingFile=File(working.fsName+"/"+spec.filename+".psd");
                if(workingFile.exists)throw Error("Working PSD already exists");
                var psd=new PhotoshopSaveOptions();psd.layers=true;psd.embedColorProfile=true;
                doc.saveAs(workingFile,psd,true,Extension.LOWERCASE);
            }
            var png=new PNGSaveOptions();png.compression=6;doc.saveAs(File(job.output_dir+"/"+spec.filename+".png"),png,true,Extension.LOWERCASE);
            doc.close(SaveOptions.DONOTSAVECHANGES);doc=null;
            report.outputs.push(item);write(report);
        }
        report.status="rendered";
    }catch(e){report.error=String(e);report.line=e.line;}
    finally{
        if(doc)try{doc.close(SaveOptions.DONOTSAVECHANGES);}catch(_){}
        app.displayDialogs=dialogs;app.preferences.rulerUnits=units;
        if(previous)try{app.activeDocument=previous;}catch(_){}
        write(report);
    }
}
