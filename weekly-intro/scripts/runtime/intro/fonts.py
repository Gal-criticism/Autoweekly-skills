"""Check active text PostScript names against installed font files, without installing fonts."""
import os
from pathlib import Path


def installed_font_files():
    roots=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts',
           Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/Windows/Fonts',
           Path(os.environ.get('CommonProgramFiles','C:/Program Files/Common Files'))/'Adobe/Fonts']
    paths=set()
    for root in roots:
        if root.is_dir():
            paths.update(p for p in root.iterdir() if p.suffix.lower() in ('.ttf','.otf','.ttc','.otc'))
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        value=winreg.EnumValue(key,i)[1]
                        if isinstance(value,str):
                            path=Path(os.path.expandvars(value))
                            paths.add(path if path.is_absolute() else roots[0]/path)
            except OSError:
                pass
    return sorted(paths)


def require_fonts(required, paths=None):
    if not required:
        return {'status':'not_required','required':[]}
    try:
        from fontTools.ttLib import TTFont, TTCollection
    except ImportError as error:
        raise ValueError('Active text requires fontTools for PostScript font validation') from error
    found={}; errors=[]
    for path in installed_font_files() if paths is None else paths:
        path=Path(path)
        try:
            collection=None
            if path.suffix.lower() in ('.ttc','.otc'):
                collection=TTCollection(path,lazy=True); fonts=collection.fonts
            else:
                fonts=[TTFont(path,lazy=True)]
            try:
                for font in fonts:
                    for name in font['name'].names:
                        if name.nameID==6:
                            value=name.toUnicode()
                            if value in required: found[value]=str(path.resolve())
            finally:
                if collection: collection.close()
                else:
                    for font in fonts: font.close()
        except Exception as error:
            errors.append(str(path)+': '+str(error))
    missing=sorted(set(required)-set(found))
    if missing:
        raise ValueError('Missing active PostScript fonts: '+', '.join(missing))
    return {'status':'checked','required':list(required),'found':found,'unreadable_files':errors}
