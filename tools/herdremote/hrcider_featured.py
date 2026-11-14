"""Byte-for-byte rules of apps-ipod/database/db_featured_parse.c."""

def featured_guests(text, known=()):
    data = text.encode('utf-8')
    lower = data.lower()
    library = {s.encode('utf-8').strip(b' \t').lower() for s in known}
    word = lambda c: 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122 or c >= 128
    markers = ((b'duet with',True),(b'featuring',True),(b'feat',True),
               (b'ft',True),(b'w/',False),(b'f/',False))
    start = None
    for i in range(len(data)):
        if i and word(data[i-1]):continue
        for marker, separator in markers:
            if not lower.startswith(marker,i):continue
            end = i + len(marker)
            tail = data[end:end+1]
            if not separator:
                if lower[end:end+1] == b'o' and (end+1 == len(data) or not word(data[end+1])):continue
                start = end
            elif tail in (b'.',b':'):start = end + 1
            elif tail in (b' ',b'\t'):start = end
            if start is not None:break
        if start is not None:break
    if start is None:return []
    stack=[]
    for c in data[:start]:
        if c in (40,91):stack.append(c)
        elif c in (41,93) and stack:stack.pop()
    end = len(data)
    if stack and len(stack) <= 4:
        close = b']' if stack[-1] == 91 else b')'
        found = data.find(close,start)
        if found >= 0:end = found
    for i in range(start,end):
        if data[i] in (40,91):end=i;break
    out=[];rejected=0
    def add(raw):
        value=raw.strip(b' \t')
        if 2 <= len(value) <= 63:out.append(value.decode('utf-8'));return True
        return False
    while len(out)<8 and rejected<8:
        value=data[start:end].strip(b' \t')
        if value.lower() in library:break
        cuts=[]
        for i in range(start,end):
            if data[i] in b'&,;+/':cuts.append((i,1))
            elif data[i] in b' \t':
                for sep in (b'and',b'x'):
                    j=i+1+len(sep)
                    if lower.startswith(sep,i+1) and j<end and data[j] in b' \t':cuts.append((i+1,len(sep)))
        cuts=[(i,n) for i,n in cuts if data[start:i].strip(b' \t') and data[i+n:end].strip(b' \t')]
        if not cuts:break
        longest=[c for c in cuts if data[start:c[0]].strip(b' \t').lower() in library]
        i,n=longest[-1] if longest else cuts[0]
        if not add(data[start:i]):rejected+=1
        start=i+n
    if len(out)<8 and rejected<8:add(data[start:end])
    return out
