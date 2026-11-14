"""Paged native Music tree over the independent Cider library index.

Routes contain bounded, escaped filter values, never arbitrary Apple API paths.
Only this worker calls the read-only Apple endpoints. Playback stays explicit.
"""
import collections
import json
import random
import re
import time
import urllib.parse
from hrcider_cache import art_id
from hrcider_featured import featured_guests

LIST_BEFORE, LIST_AFTER = 5, 20  # queue window around a song of a plain list

ROOT = {'Search':'search', 'Album':'albums', 'Random Album':'random',
        'Album Artist':'albumartists', 'Artist':'artists', 'Genre':'genres',
        'Year':'years', 'Composer':'composers', 'Featured Artists':'featured',
        'First Letter':'letters', 'Tracks by':'tracks', 'Shuffle Songs':'shuffle',
        'Search by...':'searchby', 'User Rating':'ratings',
        'Recently Added':'recent', 'Playback History':'history',
        'Same as currently played track':'same', 'Custom menu':'empty',
        'Audiobooks':'books', 'Podcasts':'podcasts', 'Quiz':'empty'}


def route(path, **filters):
    return path + ('?' + urllib.parse.urlencode(filters) if filters else '')


def row(title, path, kind='menu', **extra):
    return dict(id=extra.pop('id', path), title=title, subtitle='', art='-',
                duration=0, count=0, kind=kind, route=path, bg='181818', year=0, **extra)


def item_row(item, duration=False):
    attrs = item.get('attributes', {})
    kind = item.get('type', 'library-songs')
    name = attrs.get('name', '')
    ms = int(attrs.get('durationInMillis', 0) or 0)
    if duration:
        name += f' - {ms//60000:02}:{ms//1000%60:02}'
    key = str(item['id'])
    if 'albums' in kind:
        catalog=re.match(r'^/v1/catalog/([a-zA-Z-]+)/albums/',item.get('href',''))
        prefix='catalog-'+catalog[1]+'/' if catalog else ''
        path, view = 'album/'+prefix+urllib.parse.quote(key, safe=''), 'album'
    elif 'playlists' in kind:
        catalog=re.match(r'^/v1/catalog/([a-zA-Z-]+)/playlists/',item.get('href',''))
        prefix='catalog-'+catalog[1]+'/' if catalog else ''
        path, view = 'playlist/'+prefix+urllib.parse.quote(key, safe=''), 'playlist'
    elif 'artists' in kind:
        path, view = route('albums', artist=attrs.get('name', '')), 'artist'
    else:
        path, view = 'play/'+kind+'/'+urllib.parse.quote(key, safe=''), 'song'
    return dict(id=key, title=name, subtitle=attrs.get('artistName', ''),
                art=art_id(attrs.get('artwork', {}).get('url', '')),
                duration=ms, count=attrs.get('trackCount', 0), kind=view,
                route=path, bg=attrs.get('artwork', {}).get('bgColor', '181818'),
                year=int((attrs.get('releaseDate') or '0')[:4]))


def cached_row(v,duration=False):
    attrs={'name':v['title'],'artistName':v.get('subtitle',''),'durationInMillis':v.get('duration',0),'trackCount':v.get('count',0),'releaseDate':v.get('releaseDate',''),'artwork':{'url':v.get('artwork',''),'bgColor':v.get('bg','181818')}}
    return item_row(dict(id=v['id'],type=v.get('type') or 'library-'+v['kind'],attributes=attrs),duration)


class Library:
    def __init__(self, cache, client):
        self.cache, self.client = cache, client
        self.remote = collections.OrderedDict()
        self.state = {}
        self.context = None

    def _items(self, kind):
        with self.cache.lock:
            rows = self.cache.db.execute('SELECT value FROM items WHERE kind=?', (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _read(self, path, offset, limit):
        key = (path, offset, limit)
        cached = self.remote.get(key)
        if cached and time.monotonic()-cached[0] < 60:
            return cached[1]
        sep = '&' if '?' in path else '?'
        try:
            reply = self.client.library_path(path+sep+f'offset={offset}&limit={limit}')
            if isinstance(reply.get('data'), dict):
                reply = reply['data']
            if isinstance(reply.get('results'),dict):
                reply=dict(data=[item for kind in ('library-songs','library-albums','library-artists') for item in reply['results'].get(kind,{}).get('data',[])])
            if not isinstance(reply.get('data'), list):
                raise ValueError('Invalid library relationship')
            self.remote[key] = (time.monotonic(), reply)
            self.remote.move_to_end(key)
            while len(self.remote) > 32:
                self.remote.popitem(last=False)
            return reply
        except (OSError, ValueError, TimeoutError):
            if cached:
                return dict(cached[1], stale=True)
            return dict(data=[], stale=True, meta={'total':0})

    def page(self, query, offset, limit, stale=False):
        if len(query) > 380 or offset < 0 or not 1 <= limit <= 50:
            raise ValueError('Invalid native library query')
        parsed = urllib.parse.urlsplit(query)
        name = parsed.path
        args = dict(urllib.parse.parse_qsl(parsed.query))
        items, remote = [], None
        if name.startswith('root/'):
            name = ROOT.get(urllib.parse.unquote(name[5:]), 'empty')
        if name in ('albums','songs','playlists') and (not args or set(args)=={'format'}):
            with self.cache.lock:
                total=self.cache.db.execute('SELECT count(*) FROM items WHERE kind=?',(name,)).fetchone()[0]
                values=self.cache.db.execute("SELECT value FROM items WHERE kind=? ORDER BY json_extract(value,'$.title') COLLATE NOCASE,id LIMIT ? OFFSET ?",(name,limit,offset)).fetchall()
                generation=self.cache.generation
            return dict(generation=generation,offset=offset,total=total,stale=bool(stale),items=[cached_row(json.loads(v[0]),args.get('format')=='duration') for v in values])
        if name=='random':
            albums=self._items('albums')
            if albums:
                value=cached_row(random.choice(albums))
                value['route']='play/library-albums/'+urllib.parse.quote(value['id'],safe='')
                value['kind']='song';items=[value]
            return dict(generation=self.cache.generation,offset=offset,total=len(items),stale=bool(stale),items=items[offset:offset+limit])
        if name=='shuffle':
            with self.cache.lock:
                have=self.cache.db.execute("SELECT 1 FROM items WHERE kind='songs' LIMIT 1").fetchone()
            items=[row('Shuffle Songs','shuffle','song')] if have else []
            return dict(generation=self.cache.generation,offset=offset,total=len(items),stale=bool(stale),items=items[offset:offset+limit])
        menus = {
            'letters':[('Albums','letters/albums'),('Album Artists','letters/albumartists'),('Artists','letters/artists'),('Tracks','letters/songs')],
            'tracks':[('Filename','songs'),('Title','songs'),('Title (with track duration)','songs?format=duration')],
            'searchby':[(x,route('input',field=y)) for x,y in [('Album','album'),('Artist','artist'),('Album Artist','albumartist'),('Title','title'),('Albums by Year','year'),('Albums between Years','range'),('Artists between Years','artistrange'),('User Rating','rating')]],
            'history':[(x,'history/'+y) for x,y in [('Recently played tracks','tracks'),('Recently Played Albums','albums'),('Recently Played Artists','artists')]],
            'same':[(x,route('same/'+y)) for x,y in [('Title','title'),('Artist','artist'),('Album','album'),('Composer','composer')]],
            'ratings':[('0','songs?rating=0'),('1','songs?rating=1'),('2','songs?rating=-1')],
        }
        if name in menus:
            items=[row(t,p) for t,p in menus[name]]
        elif name.startswith('letters/'):
            target=name[8:]
            if target not in ('albums','albumartists','artists','songs'):
                raise ValueError('Invalid letter target')
            items=[row('Numeric',route(target,letter='numeric'))]+[row(c,route(target,letter=c)) for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ']+[row('Special Character',route(target,letter='special'))]
        elif name.startswith(('album/','playlist/')):
            parts=name.split('/')
            kind=parts[0];key=parts[-1]
            if not re.fullmatch(r'[\w.:-]{1,128}',urllib.parse.unquote(key)):
                raise ValueError('Invalid library id')
            if len(parts)==3 and re.fullmatch(r'catalog-[A-Za-z-]{2,16}',parts[1]):
                path='/v1/catalog/'+parts[1][8:]+'/'+kind+'s/'+key+'/tracks'
            elif len(parts)==2:path='/v1/me/library/'+kind+'s/'+key+'/tracks'
            else:raise ValueError('Invalid collection route')
            remote=self._read(path,offset,limit)
        elif name=='recent':
            remote=self._read('/v1/me/library/recently-added',offset,limit)
        elif name.startswith('history/'):
            target=name.split('/',1)[1]
            endpoint={'tracks':'/v1/me/recent/played/tracks','albums':'/v1/me/recent/played','artists':'/v1/me/recent/played/tracks'}.get(target)
            if endpoint:
                remote=self._read(endpoint,offset,limit)
                if target=='artists':
                    names=list(dict.fromkeys(i.get('attributes',{}).get('artistName','') for i in remote['data']))
                    remote=dict(remote,data=[dict(id=n,type='artists',attributes={'name':n}) for n in names if n])
                else:remote=dict(remote,data=[i for i in remote['data'] if target in i.get('type','')])
        elif name=='search' and args.get('q'):
            reply=self._read('/v1/me/library/search?term='+urllib.parse.quote(args['q'])+'&types=library-songs,library-albums,library-artists',0,50)
            data=reply['data']
            remote=dict(reply,data=data[offset:offset+limit],meta={'total':len(data)})
        elif name.startswith('same/'):
            field=name[5:]
            value=self.state.get(field,'')
            return self.page(route('songs' if field in ('title','composer','album') else 'albums',**{field:value or '\x00'}),offset,limit,stale)
        elif name in ('albums','songs','playlists','artists','albumartists','genres','years','composers','featured','books','podcasts'):
            songs=self._items('songs')
            if name=='featured' or 'guest' in args:
                known={s.get('albumartist') or s.get('subtitle','') for s in songs}
                known.update(a['title'] for a in self._items('artists'))
                songs=[dict(s,guests=featured_guests(s['title'],known)+featured_guests(s.get('artist',''),known)) for s in songs]
            def match(s):
                for raw_field,val in args.items():
                    field=raw_field.removesuffix('~')
                    contains=raw_field.endswith('~')
                    if field in ('format','letter'):continue
                    if field=='genre' and val not in s.get('genres',[]):return False
                    if field=='year' and not s.get('releaseDate','').startswith(val):return False
                    if field=='range':
                        bounds=val.split('-',1)
                        if len(bounds)!=2 or not bounds[0]<=s.get('releaseDate','')[:4]<=bounds[1]:return False
                    elif field=='guest' and (val not in s.get('guests',[]) or val.strip().lower()==s.get('albumartist','').strip().lower()):return False
                    elif field not in ('genre','year','guest','rating'):
                        actual=str(s.get(field,'')).casefold()
                        if (val.casefold() not in actual) if contains else (val.casefold()!=actual):return False
                    if field=='rating' and str(s.get('rating',0))!=('-1' if val=='2' else val):return False
                return True
            songs=[s for s in songs if match(s)]
            if name in ('books','podcasts'):
                term='audiobook' if name=='books' else 'podcast'
                songs=[s for s in songs if any(term in g.casefold() for g in s.get('genres',[]))]
                name='albums'
            if name in ('genres','years','composers','albumartists','artists','featured'):
                field={'genres':'genre','years':'year','composers':'composer','albumartists':'albumartist','artists':'artist','featured':'guest'}[name]
                values=set()
                for s in songs:
                    if field=='genre':values.update(s.get('genres',[]))
                    elif field=='year':values.add(s.get('releaseDate','')[:4])
                    elif field=='guest':values.update(g for g in s.get('guests',[]) if g.strip().lower()!=s.get('albumartist','').strip().lower())
                    else:values.add(s.get(field,'') or (s.get('subtitle','') if field in ('artist','albumartist') else ''))
                target='albumartists' if field in ('genre','year') else 'albums'
                items=[row(v,route(target,**(args|{field:v})), 'artist' if field in ('artist','albumartist','guest') else 'menu') for v in sorted(values,key=str.casefold) if v]
            else:
                values=songs if name=='songs' else self._items(name)
                if name=='songs':values=songs
                elif args and name=='albums':
                    album_ids={key for s in songs for key in s.get('albums',[])}
                    albums={s.get('album','') for s in songs if not s.get('albums')}
                    values=[s for s in values if s['id'] in album_ids or s['title'] in albums]
                for v in values:
                    kind=v.get('type') or 'library-'+name
                    attrs={'name':v['title'],'artistName':v.get('subtitle',''),'durationInMillis':v.get('duration',0),'trackCount':v.get('count',0),'releaseDate':v.get('releaseDate',''),'artwork':{'url':v.get('artwork',''),'bgColor':v.get('bg','181818')}}
                    items.append(item_row(dict(id=v['id'],type=kind,attributes=attrs),args.get('format')=='duration'))
                if name=='songs' and args.get('album'):
                    rank={v['id']:(v.get('disc',0),v.get('track',0)) for v in values}
                    items.sort(key=lambda r:rank[r['id']])
                else:items.sort(key=lambda r:r['title'].casefold())
            if args.get('letter'):
                c=args['letter'];items=[v for v in items if (v['title'][:1].isdigit() if c=='numeric' else v['title'][:1].upper() not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' if c=='special' else v['title'][:1].upper()==c)]
        if remote is not None:
            items=[item_row(i) for i in remote['data']]
            # Relationship artwork is registered only as whitelisted metadata.
            self.cache.register_templates(remote['data'])
            total=remote.get('meta',{}).get('total',offset+len(items)+(1 if remote.get('next') else 0))
            return dict(generation=self.cache.generation,offset=offset,total=total,stale=stale or remote.get('stale',False),items=items)
        return dict(generation=self.cache.generation,offset=offset,total=len(items),stale=bool(stale),items=items[offset:offset+limit])

    def play(self, path):
        if path=='random':
            albums=self._items('albums')
            if not albums:raise ValueError('No albums')
            chosen=random.choice(albums)
            return self.client.control('play-item','library-albums:'+chosen['id'])
        if path=='shuffle':
            return self.client.shuffle_library()
        parts=path.split('/')
        if len(parts)!=3 or parts[0]!='play':raise ValueError('Invalid play route')
        kind,key=parts[1],urllib.parse.unquote(parts[2])
        context,self.context=self.context,None
        if context and kind in ('songs','library-songs') and time.monotonic()-context[2]<15:
            try:
                result=self._play_in_list(context[0],context[1],kind,key)
                if result is not None:return result
            except (OSError,ValueError,TimeoutError):
                pass  # the song still plays on its own
        return self.client.control('play-item',kind+':'+key)

    def set_context(self, value):
        """native-context: 'index|list-route', for the native-play that follows."""
        index,_,route=str(value).partition('|')
        self.context=None
        if index.isdigit() and 0<len(route)<=380 and int(index)<100000:
            self.context=(int(index),route,time.monotonic())

    def _play_in_list(self, index, route, kind, key):
        """Play song `key` of the list `route` (row `index`) so that Cider's queue is
        that list: a whole album or playlist, or a window around the row."""
        aliases=self.client.song_aliases(kind,key)
        match=re.fullmatch(r'(album|playlist)/(?:catalog-([a-zA-Z-]+)/)?([^/?]+)',route)
        if match:
            ctype=('' if match[2] else 'library-')+('albums' if match[1]=='album' else 'playlists')
            return self.client.play_collection(ctype,urllib.parse.unquote(match[3]),aliases,index)
        start=max(0,index-LIST_BEFORE)
        rows=self.page(route,start,LIST_BEFORE+LIST_AFTER+1)['items']
        songs=[(r['id'],r['route'].split('/')[1],urllib.parse.unquote(r['route'].split('/')[2]))
               for r in rows if r['route'].startswith('play/') and r['route'].count('/')==2]
        here=next((i for i,v in enumerate(songs) if v[2]==key),None)
        if here is None:return None
        before=[v[1:] for v in songs[:here]];after=[v[1:] for v in songs[here+1:]]
        if not before and not after:return None
        result=self.client.control('play-item',kind+':'+key)
        result['fill']=lambda cancel:self.client.fill_queue(aliases,before,after,cancel)
        return result
