/* Loaded by a generated driver; job is a serialized data object. */
function weeklyOverlayRender(job) {
    function json(v) {
        if (v === null) return "null";
        if (typeof v === "string") return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[\u0000-\u001f]/g, function (c) {
            return "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
        }) + '"';
        if (typeof v === "number" || typeof v === "boolean") return String(v);
        var parts = [], k;
        if (v instanceof Array) {
            for (k = 0; k < v.length; k++) parts.push(json(v[k]));
            return "[" + parts.join(",") + "]";
        }
        for (k in v) if (v.hasOwnProperty(k)) parts.push(json(k) + ":" + json(v[k]));
        return "{" + parts.join(",") + "}";
    }
    function write(path, data) {
        var f = File(path);
        f.encoding = "UTF8";
        if (!f.open("w")) throw Error("Cannot write " + path);
        f.write(json(data));
        f.close();
    }
    function walk(container, callback) {
        for (var i = 0; i < container.layers.length; i++) {
            var layer = container.layers[i];
            callback(layer);
            if (layer.typename === "LayerSet") walk(layer, callback);
        }
    }
    function one(doc, name) {
        var found = [];
        walk(doc, function (layer) { if (layer.name === name) found.push(layer); });
        if (found.length !== 1) throw Error("Expected one layer: " + name + "; found " + found.length);
        if (found[0].typename !== "ArtLayer" || found[0].kind !== LayerKind.TEXT) throw Error("Not editable text: " + name);
        return found[0];
    }
    function reveal(layer) {
        while (layer.typename !== "Document") {
            layer.visible = true;
            layer = layer.parent;
        }
    }
    function bounds(layer) {
        // DOM bounds include the template's large shadow. Check glyph layout
        // against boundsNoEffects so inherited styling does not look like overflow.
        var ref = new ActionReference();
        ref.putIdentifier(stringIDToTypeID("layer"), layer.id);
        var b = executeActionGet(ref).getObjectValue(stringIDToTypeID("boundsNoEffects"));
        return [b.getUnitDoubleValue(stringIDToTypeID("left")),
                b.getUnitDoubleValue(stringIDToTypeID("top")),
                b.getUnitDoubleValue(stringIDToTypeID("right")),
                b.getUnitDoubleValue(stringIDToTypeID("bottom"))];
    }
    function text(layer, value, font, slot) {
        layer.textItem.contents = value;
        layer.textItem.font = font;
        if (layer.textItem.font !== font) throw Error("Font substitution: " + font);
        var b = bounds(layer);
        if (b[0] < slot[0] || b[1] < slot[1] || b[2] > slot[2] || b[3] > slot[3] || b[2] <= b[0] || b[3] <= b[1]) {
            throw Error("Text exceeds reserved area: " + layer.name + " [" + b.join(",") + "]");
        }
        return {text: value, font: layer.textItem.font, bounds: b, opacity: layer.opacity};
    }
    var folder = Folder(job.output_dir), resultFile = folder + "/result.json";
    if (File(folder + "/started.json").exists || File(resultFile).exists) throw Error("Run already started; prepare a new run.");
    write(folder + "/started.json", {run_id: job.run_id, status: "running"});
    var result = {schema_version: 1, run_id: job.run_id, status: "failed", photoshop_version: app.version, outputs: []};
    var oldDialogs = app.displayDialogs, oldUnits = app.preferences.rulerUnits;
    var previous = app.documents.length ? app.activeDocument : null, doc = null;
    var categories = ["\u65b0\u4f5c\u60c5\u62a5", "\u6c49\u5316\u60c5\u62a5", "\u5468\u8fb9\u60c5\u62a5", "\u7b80\u8baf"];
    var fonts = ["MiSans-Semibold", "MiSans-Regular", "NotoSerifCJKsc-SemiBold"];
    try {
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        var available = {}, i, j;
        for (i = 0; i < app.fonts.length; i++) available[app.fonts[i].postScriptName] = true;
        for (i = 0; i < fonts.length; i++) if (!available[fonts[i]]) throw Error("Missing original font: " + fonts[i]);
        for (i = 0; i < app.documents.length; i++) {
            try { if (app.documents[i].fullName.fsName === File(job.template).fsName) throw Error("Staged template is already open."); }
            catch (e) { if (String(e).indexOf("Staged template") >= 0) throw e; }
        }
        for (i = 0; i < job.task.items.length; i++) {
            var item = job.task.items[i], categoryValid = false;
            for (j = 0; j < categories.length; j++) if (item.category === categories[j]) categoryValid = true;
            if (!categoryValid) throw Error("Unknown category");
            doc = app.open(File(job.template));
            if (doc.width.as("px") !== 2160 || doc.height.as("px") !== 1080) throw Error("Wrong canvas size");
            if (doc.mode !== DocumentMode.RGB || doc.bitsPerChannel !== BitsPerChannelType.EIGHT) throw Error("Template must be RGB 8-bit");
            var layers = [], c;
            for (j = 0; j < categories.length; j++) layers.push(one(doc, categories[j]));
            var date = one(doc, "\u3010\u65b0\u95fb\u65e5\u671f\u3011");
            var title = one(doc, "\u3010\u97f3\u4e50\u540d\u3011");
            var artist = one(doc, "\u3010\u97f3\u4e50\u4f5c\u8005\u3011");
            walk(doc, function (layer) { layer.visible = false; });
            for (j = 0; j < layers.length; j++) if (layers[j].name === item.category) c = layers[j];
            reveal(c); reveal(date); reveal(title); reveal(artist);
            var fields = {
                category: text(c, item.category, fonts[0], [60, 170, 2060, 234]),
                date: text(date, job.period, fonts[2], [60, 234, 2060, 285]),
                title: text(title, item.title, fonts[0], [60, 315, 2060, 388]),
                artist: text(artist, item.artist, fonts[1], [60, 388, 2060, 445])
            };
            var output = File(folder + "/" + job.filenames[i]);
            if (output.exists) throw Error("Output exists: " + output.name);
            var png = new PNGSaveOptions();
            png.interlaced = false;
            png.compression = 6;
            doc.saveAs(output, png, true, Extension.LOWERCASE);
            result.outputs.push({filename: job.filenames[i], category: item.category, title: item.title, artist: item.artist, fields: fields});
            doc.close(SaveOptions.DONOTSAVECHANGES); doc = null;
        }
        result.status = "rendered";
    } catch (error) {
        result.error = String(error);
        result.error_line = error.line || 0;
    } finally {
        if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (_) {} }
        app.displayDialogs = oldDialogs;
        app.preferences.rulerUnits = oldUnits;
        if (previous) { try { app.activeDocument = previous; } catch (_) {} }
        write(resultFile, result);
    }
}
