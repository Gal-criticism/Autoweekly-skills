function refineCoverBackgroundBatch(job) {
    var previous=app.documents.length?app.activeDocument:null,doc=null,dialogs=app.displayDialogs;
    var results=[];
    try {
        // Refuse the whole batch before opening or modifying any document.
        for (var check=0;check<job.items.length;check++) {
            var entry=job.items[check], source=File(entry.template).fsName;
            for(var openIndex=0;openIndex<app.documents.length;openIndex++) {
                var opened=null;
                try { opened=app.documents[openIndex].fullName.fsName; } catch(ignore) {}
                if(opened===source) throw Error("Batch template is already open; preserve unsaved edits");
            }
            var outputDir=entry.output_dir || job.output_dir;
            for(var extIndex=0;extIndex<3;extIndex++) {
                var extension=["psd","png","jpg"][extIndex];
                if(File(outputDir+"/"+entry.filename+"."+extension).exists)
                    throw Error("Output exists; not overwriting "+entry.filename+"."+extension);
            }
        }
        app.displayDialogs=DialogModes.NO;
        for(var n=0;n<job.items.length;n++){
            var spec=job.items[n];
            doc=app.open(File(spec.template));
            function find(container,id){for(var i=0;i<container.layers.length;i++){var l=container.layers[i];if(l.id===id)return l;if(l.typename==="LayerSet"){var v=find(l,id);if(v)return v;}}return null;}
            for(var i=0;i<spec.layers.length;i++){
                var layerSpec=spec.layers[i],layer=find(doc,layerSpec.id);if(!layer)throw Error("Missing background layer "+layerSpec.id);
                doc.activeLayer=layer;
                var desc=new ActionDescriptor(),list=new ActionList(),adjust=new ActionDescriptor();
                desc.putBoolean(charIDToTypeID("Clrz"),false);
                adjust.putInteger(charIDToTypeID("H   "),layerSpec.hue);
                adjust.putInteger(charIDToTypeID("Strt"),layerSpec.saturation);
                adjust.putInteger(charIDToTypeID("Lght"),layerSpec.lightness);
                list.putObject(charIDToTypeID("Hst2"),adjust);desc.putList(charIDToTypeID("Adjs"),list);
                executeAction(charIDToTypeID("HStr"),desc,DialogModes.NO);
            }
            for(var f=0;f<spec.fills.length;f++){
                var fill=spec.fills[f];doc.activeLayer=find(doc,fill.id);
                var ref=new ActionReference();ref.putEnumerated(stringIDToTypeID("contentLayer"),charIDToTypeID("Ordn"),charIDToTypeID("Trgt"));
                var set=new ActionDescriptor(),to=new ActionDescriptor(),rgb=new ActionDescriptor();set.putReference(charIDToTypeID("null"),ref);
                rgb.putDouble(charIDToTypeID("Rd  "),fill.rgb[0]);rgb.putDouble(charIDToTypeID("Grn "),fill.rgb[1]);rgb.putDouble(charIDToTypeID("Bl  "),fill.rgb[2]);
                to.putObject(charIDToTypeID("Clr "),charIDToTypeID("RGBC"),rgb);set.putObject(charIDToTypeID("T   "),stringIDToTypeID("solidColorLayer"),to);executeAction(charIDToTypeID("setd"),set,DialogModes.NO);
            }
            var psd=new PhotoshopSaveOptions();psd.layers=true;psd.embedColorProfile=true;
            doc.saveAs(File(spec.output_dir+"/"+spec.filename+".psd"),psd,true,Extension.LOWERCASE);
            var png=new PNGSaveOptions();png.compression=6;doc.saveAs(File(spec.output_dir+"/"+spec.filename+".png"),png,true,Extension.LOWERCASE);
            var jpg=new JPEGSaveOptions();jpg.quality=11;doc.saveAs(File(spec.output_dir+"/"+spec.filename+".jpg"),jpg,true,Extension.LOWERCASE);
            doc.close(SaveOptions.DONOTSAVECHANGES);doc=null;
            results.push({filename:spec.filename,status:"rendered"});
        }
    }catch(e){results.push({status:"failed",error:String(e).replace(/"/g,"'"),line:e.line});}
    finally{
        if(doc)try{doc.close(SaveOptions.DONOTSAVECHANGES);}catch(_){}
        app.displayDialogs=dialogs;if(previous)try{app.activeDocument=previous;}catch(_){}
        var out=File(job.output_dir+"/refine_batch_result.json");out.encoding="UTF8";out.open("w");
        var parts=[];for(var r=0;r<results.length;r++){var x=results[r];var s='{"filename":"'+(x.filename||"")+'","status":"'+x.status+'"';if(x.error)s+=',"error":"'+x.error+'","line":'+x.line;s+='}';parts.push(s);}
        out.write("["+parts.join(",")+"]");out.close();
    }
}
