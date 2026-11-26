"""Rectangle-based visibility estimate. No screenshots or pixel inspection."""
from dataclasses import dataclass

def area(rect):
    return max(0, rect[2]-rect[0]) * max(0, rect[3]-rect[1])

def intersect(a,b):
    result=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])
    return result if area(result)>0 else None

def subtract(rect,cover):
    overlap=intersect(rect,cover)
    if overlap is None:
        return [rect]
    l,t,r,b=rect;x,y,z,w=overlap
    return [p for p in ((l,t,r,y),(l,w,r,b),(l,y,x,w),(z,y,r,w)) if area(p)>0]

@dataclass
class WindowGeometry:
    hwnd:int
    pid:int
    app:str|None
    rect:tuple[int,int,int,int]|None
    minimized:bool=False
    occludes:bool=True

def visible_windows(windows,monitors,minimum_ratio=.05,minimum_pixels=4096):
    """Input is top-to-bottom Z order; skip tiny exposed borders by default."""
    covers=[]
    result={}
    for window in windows:
        if window.minimized or window.rect is None:
            result[window.hwnd]=False
            continue
        pieces=[p for monitor in monitors if (p:=intersect(window.rect,monitor)) is not None]
        total=sum(map(area,pieces))
        for cover in covers:
            pieces=[part for piece in pieces for part in subtract(piece,cover)]
            if len(pieces)>1024:
                # Conservative cap for pathological layouts; never inflate visibility.
                pieces=[]
                break
            if not pieces:
                break
        exposed=sum(map(area,pieces))
        result[window.hwnd]=total>0 and exposed>=min(minimum_pixels,total) and exposed>=total*minimum_ratio
        if window.occludes:
            covers.append(window.rect)
    return result
