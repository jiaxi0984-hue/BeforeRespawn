"""学习软件名称搜索与官网目录；内置核实项和用户确认项分开保存。"""
import copy
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote, urlsplit
import urllib.request
import xml.etree.ElementTree as ET
import ipaddress
from mode_config import valid_web_url
from state_store import JsonStore

ROOT = Path(__file__).resolve().parent


def online_candidates(query):
    """公开搜索只返回待核实候选，不把广告或搜索首条自动认证为官网。"""
    query=str(query).strip()
    if not query or len(query)>80:raise ValueError('请输入 1–80 字的软件名称')
    request=urllib.request.Request('https://www.bing.com/search?format=rss&q='+quote(query+' 官网 official'),
        headers={'User-Agent':'Mozilla/5.0','Accept':'application/rss+xml, application/xml'})
    with urllib.request.urlopen(request,timeout=6) as response:
        data=response.read(300001)
    if len(data)>300000:raise ValueError('搜索响应过大')
    root=ET.fromstring(data);key=normalized_name(query);results=[];seen=set()
    blocked={'bing.com','baidu.com','zhihu.com','php.cn','bilibili.com','csdn.net','softonic.com','pcsoft.com.cn'}
    for item in root.findall('./channel/item'):
        title=item.findtext('title') or '';url=item.findtext('link') or ''
        try:
            parts=urlsplit(url);host=(parts.hostname or '').lower()
            if not valid_web_url(url) or not host or host=='localhost' or host.endswith('.local'):continue
            try:
                if not ipaddress.ip_address(host).is_global:continue
            except ValueError:pass
            if any(host==b or host.endswith('.'+b) for b in blocked):continue
            if Path(parts.path).suffix.lower() in {'.exe','.msi','.zip','.apk','.dmg'}:continue
            if key not in normalized_name(title) and key not in normalized_name(host):continue
        except ValueError:continue
        if host in seen:continue
        seen.add(host);results.append(dict(title=title[:180],url=url,domain=host,verification='unverified'))
        if len(results)>=8:break
    return results


def normalized_name(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value)).casefold() if c.isalnum())


def installer_name(value):
    value = unicodedata.normalize('NFKC', str(value)).strip()
    value = re.sub(r'\s*[（(](?:x64|x86|64[- ]?bit|32[- ]?bit|windows)[）)]', '', value, flags=re.I)
    value = re.sub(r'\s*(?:(?:版本|version|v)\s*)?\d+(?:\.\d+){0,5}(?:[-_a-z0-9.]*)?\s*$', '', value, flags=re.I)
    return normalized_name(value)


class LearningCatalog:
    def __init__(self, path=None, user_path=None):
        data=json.loads(Path(path or ROOT/'learning_catalog.json').read_text(encoding='utf-8'))
        self.entries=copy.deepcopy(data['entries'])
        self.updated_on=data['updated_on']
        self.user_store=JsonStore(user_path or ROOT/'runtime'/'catalog-overrides.json')
        try:
            users=self.user_store.read().get('entries', [])
            for entry in users if isinstance(users,list) else []:
                if not isinstance(entry,dict) or not all(isinstance(entry.get(k),str) and entry[k] for k in ('id','name','official_url')):continue
                if not valid_web_url(entry['official_url']) or any(e['id']==entry['id'] for e in self.entries):continue
                self.entries.append(dict(entry,windows=None,aliases=[],category='用户确认',verification='user_confirmed',
                    note='此官网由用户确认，未计入内置已核实目录。',sources=[entry['official_url']]))
        except (OSError,ValueError,TypeError):
            pass
        seen=set()
        for entry in self.entries:
            if entry['id'] in seen or not valid_web_url(entry['official_url']):
                raise ValueError('软件库含重复标识或无效官网')
            if entry.get('study_url') and not valid_web_url(entry['study_url']):
                raise ValueError('软件库含无效学习网页')
            seen.add(entry['id'])

    def search(self, query='', windows_only=False):
        key=normalized_name(query)
        scored=[]
        for entry in self.entries:
            if windows_only and entry.get('windows') is not True:
                continue
            names=[normalized_name(x) for x in [entry['name'],*entry.get('aliases',[])]]
            if not key:score=1
            elif key in names:score=100
            elif len(key)>=2 and any(n.startswith(key) for n in names):score=80
            elif len(key)>=2 and any(key in n for n in names):score=60
            else:continue
            scored.append((score,entry))
        return [copy.deepcopy(e) for s,e in sorted(scored,key=lambda item:(-item[0],item[1]['name']))]

    def resolve(self, query):
        key=normalized_name(query)
        exact=[e for e in self.search(query) if key in [normalized_name(x) for x in [e['name'],*e.get('aliases',[])]]]
        return exact[0] if len(exact)==1 else None

    def identify_installation(self, name, product_name=''):
        names={installer_name(name),installer_name(product_name)}-{''}
        matches=[entry for entry in self.entries if entry.get('windows') is True and names.intersection(
            normalized_name(n) for n in [entry['name'],*entry.get('aliases',[])])]
        return copy.deepcopy(matches[0]) if len(matches)==1 else None

    def web_target(self, entry):
        return dict(id='catalog-web-'+entry['id'],name=entry['name']+'官网',adapter='web',
                    url=entry.get('study_url') or entry['official_url'],pin=True,catalog_id=entry['id'])

    def search_target(self, query):
        query=str(query).strip()
        if not query or len(query)>80:raise ValueError('请输入 1–80 字的软件名称')
        key=hashlib.sha256(normalized_name(query).encode()).hexdigest()[:12]
        return dict(id='official-search-'+key,name='官网搜索：'+query,adapter='web',
            url='https://www.bing.com/search?q='+quote(query+' 官网'),pin=False)

    def remember(self, name, url):
        if not name.strip() or self.resolve(name):raise ValueError('名称为空或已在目录中，请直接选择已收录项目')
        if not valid_web_url(url):raise ValueError('请输入不含账号密码的 HTTPS 官网地址')
        if Path(urlsplit(url).path).suffix.lower() in {'.exe','.msi','.zip','.apk','.dmg'}:
            raise ValueError('请保存官网网页，不要保存安装包链接')
        entry=dict(id='user-'+hashlib.sha256((normalized_name(name)+url).encode()).hexdigest()[:12],
            name=name.strip(),aliases=[],official_url=url,study_url='',windows=None,category='用户确认',
            note='此官网由用户确认，未计入内置已核实目录。',sources=[url],verification='user_confirmed')
        def update(data):
            entries=data.setdefault('entries',[])
            if not any(normalized_name(e['name'])==normalized_name(name) for e in entries):entries.append(entry)
        self.user_store.transact(update)
        self.entries.append(entry)
        return entry
