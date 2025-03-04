#!/usr/bin/env python3

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

from networkx.readwrite import json_graph

from collections import OrderedDict
import hashlib
import operator
import shlex
from functools import reduce
from http.client import RemoteDisconnected
from lxml import etree
from mako import exceptions
from mako.template import Template
from munch import Munch
from pytablewriter import MarkdownTableWriter
from urllib.error import HTTPError
from urllib.request import urlopen, urlretrieve, Request
import glob
import itertools
import json, jsonpatch, jsonpath_ng
import mako
import networkx as nx
import os
import sys
import re
import sys
import tarfile
import tempfile
import zipfile
import fnmatch
import sqlite3

root = os.path.join(os.path.dirname(__file__), '..')

print('parsing Zotero/Juris-M schemas')
SCHEMA = Munch(root = os.path.join(root, 'schema'))
ITEMS = os.path.join(root, 'gen/items')
TYPINGS = os.path.join(root, 'gen/typings')

os.makedirs(SCHEMA.root, exist_ok=True)
os.makedirs(ITEMS, exist_ok=True)
os.makedirs(TYPINGS, exist_ok=True)

def readurl(url):
  req = Request(url)
  if ('api.github.com' in url) and (token := os.environ.get('GITHUB_TOKEN', None)):
    req.add_header('Authorization', f'token {token}')
  return urlopen(req).read().decode('utf-8')

class fetch(object):
  def __init__(self, client):
    self.schema = os.path.join(SCHEMA.root, f'{client}.json')

    if client == 'zotero':
      releases = [
        ref['ref'].split('/')[-1]
        for ref in
        json.loads(readurl('https://api.github.com/repos/zotero/zotero/git/refs/tags'))
      ]
      releases += [
        rel['version']
        for rel in
        json.loads(urlopen("https://www.zotero.org/download/client/manifests/release/updates-linux-x86_64.json").read().decode("utf-8"))
        if not rel['version'] in releases
      ]
      releases = [rel for rel in releases if int(rel.split('.')[0]) >= 5]
      releases = sorted(releases, key=lambda r: [int(n) for n in r.replace('m', '.').split('.')])
      self.update(
        client=client,
        releases=releases,
        download='https://www.zotero.org/download/client/dl?channel=release&platform=linux-x86_64&version={version}',
        jarpath='Zotero_linux-x86_64/zotero.jar',
        schema='resource/schema/global/schema.json'
      )
    elif client == 'jurism':
      releases = [
        ref['ref'].split('/')[-1].replace('v', '')
        for ref in
        json.loads(readurl('https://api.github.com/repos/juris-m/zotero/git/refs/tags'))
      ]
      releases += [
        rel
        for rel in
        readurl('https://github.com/Juris-M/assets/releases/download/client%2Freleases%2Fincrementals-linux/incrementals-release-linux').strip().split("\n")
        if rel != '' and rel not in releases
      ]
      releases = [rel for rel in releases if rel.startswith('5.') and 'm' in rel and not 'beta' in rel]
      releases = sorted(releases, key=lambda r: [int(n) for n in r.replace('m', '.').split('.')])
      self.update(
        client=client,
        releases=releases,
        download='https://github.com/Juris-M/assets/releases/download/client%2Frelease%2F{version}/Jurism-{version}_linux-x86_64.tar.bz2',
        jarpath='Jurism_linux-x86_64/jurism.jar',
        schema='resource/schema/global/schema-jurism.json'
      )
    else:
      raise ValueError(f'Unknown client {client}')

  def hash(self, schema):
    #print(schema.keys())
    #'version', 'itemTypes', 'meta', 'csl', 'locales', 'release', 'hash'
    return hashlib.sha512(json.dumps({ k: v for k, v in schema.items() if k in ('itemTypes', 'meta', 'csl')}, sort_keys=True).encode('utf-8')).hexdigest()

  def update(self, client, releases, download, jarpath, schema):
    hashes_cache = os.path.join(SCHEMA.root, 'hashes.json')
    itemtypes = os.path.join(SCHEMA.root, f'{client}-type-ids.json')

    if os.path.exists(hashes_cache):
      with open(hashes_cache) as f:
        hashes = json.load(f, object_hook=OrderedDict)
    else:
      hashes = OrderedDict()
    if not client in hashes:
      hashes[client] = OrderedDict()

    current = releases[-1]
    if current not in hashes[client]:
      ood = f'{current} not in f{client}.json'
    elif not os.path.exists(self.schema):
      ood = f'{self.schema} does not exist'
    elif not os.path.exists(itemtypes):
      ood = f'{itemtypes} does not exist'
    else:
      return
    if 'CI' in os.environ:
      raise ValueError(f'{self.schema} out of date: {ood}')

    print('  updating', os.path.basename(self.schema))

    for release in releases:
      if release != current and release in hashes[client]: continue

      with tempfile.NamedTemporaryFile() as tarball:
        print('    downloading', download.format(version=release))
        try:
          urlretrieve(download.format(version=release), tarball.name)

          tar = tarfile.open(tarball.name, 'r:bz2')

          jar = tar.getmember(jarpath)
          print('      extracting', jar.name)
          jar.name = os.path.basename(jar.name)
          tar.extract(jar, path=os.path.dirname(tarball.name))

          jar = zipfile.ZipFile(os.path.join(os.path.dirname(tarball.name), jar.name))
          itt = fnmatch.filter(jar.namelist(), f'**/system-*-{client}.sql')
          assert len(itt) <= 1, itt
          if len(itt) == 1:
            itt = itt[0]
          else:
            itt = fnmatch.filter(jar.namelist(), '**/system-*.sql')
            assert len(itt) == 1, itt
            itt = itt[0]
          with jar.open(itt) as f, open(itemtypes, 'wb') as i:
            i.write(f.read())
          try:
            with jar.open(schema) as f:
              client_schema = json.load(f)
              with open(self.schema, 'w') as f:
                json.dump(client_schema, f, indent='  ')
              hashes[client][release] = self.hash(client_schema)
            print('      release', release, 'schema', client_schema['version'], 'hash', hashes[client][release])

            if (client == 'zotero'):
              with jar.open('resource/schema/dateFormats.json') as f:
                dateFormats = json.load(f)
              with open(os.path.join('schema', 'dateFormats.json'), 'w') as f:
                json.dump(dateFormats, f, indent='  ')
          except KeyError:
            hashes[client][release] = None
            print('      release', release, 'does not have a bundled schema')

        except HTTPError as e:
          if e.code in [ 403, 404 ]:
            print('      release', release, 'not available')
            hashes[client][release] = None
          else:
            raise e

      with open(hashes_cache, 'w') as f:
        json.dump(hashes, f, indent='  ')

  def __enter__(self):
    self.f = open(self.schema)
    return self.f

  def __exit__(self, type, value, traceback):
    self.f.close()

class jsonpath:
  finders = {}

  @classmethod
  def parse(cls, path):
    if not path in cls.finders: cls.finders[path] = jsonpath_ng.parse(path)
    return cls.finders[path]

def patch(s, *ps):
  # field/type order doesn't matter for BBT
  for it in s['itemTypes']:
    assert 'creatorTypes' in it
    # assures primary is first
    assert len(it['creatorTypes'])== 0 or [ct['creatorType'] for ct in it['creatorTypes'] if ct.get('primary', False)] == [it['creatorTypes'][0]['creatorType']]

  s['itemTypes'] = {
    itemType['itemType']: {
      'itemType': itemType['itemType'],
      'fields': { field['field']: field.get('baseField', field['field']) for field in itemType['fields'] },
      'creatorTypes': [ct['creatorType'] for ct in itemType['creatorTypes'] ]
    }
    for itemType in s['itemTypes']
  }
  del s['locales']

  for p in ps:
    print('  applying', p)
    with open(os.path.join(SCHEMA.root, p)) as f:
      s = jsonpatch.apply_patch(s, json.load(f))
  return s

class ExtraFields:
  def __init__(self):
    self.changeid = 0
    self.dg = nx.DiGraph()
    self.color = Munch(
      zotero='#33cccc',
      csl='#99CC00',
      label='#C0C0C0',
      removed='#666666',
      added='#0000FF'
    )

  def make_label(self, field):
    label = field.replace('_', ' ').replace('-', ' ')
    label = re.sub(r'([a-z])([A-Z])', r'\1 \2', label)
    label = label.lower()
    return label

  def add_label(self, domain, name, label):
    assert domain in ['csl', 'zotero'], (domain, name, label)
    assert type(name) == str
    assert type(label) == str

    for label in [label, self.make_label(label)]:
      attrs = {
        'domain': 'label',
        'name': label,
        'graphics': {'h': 30.0, 'w': 7 * len(label), 'hasFill': 0, 'outline': self.color.label},
      }
      if re.search(r'[-_A-Z]', label): attrs['LabelGraphics'] = { 'color': self.color.label }

      assert self.dg.has_node(f'{domain}:{name}'), f'missing {domain}:{name} for label:{label} =>'
      self.dg.add_node(f'label:{label}', **attrs)
      self.dg.add_edge(f'label:{label}', f'{domain}:{name}', graphics={ 'targetArrow': 'standard' })

  def add_mapping(self, from_, to, reverse=True):
    mappings = [(from_, to)]
    if reverse: mappings.append((to, from_))
    for from_, to in mappings:
      self.dg.add_edge(':'.join(from_), ':'.join(to), graphics={ 'targetArrow': 'standard' })

  def add_var(self, domain, name, type_, client):
    assert domain in ['csl', 'zotero']
    assert type(name) == str
    assert type_ in ['name', 'date', 'text']

    node_id = f'{domain}:{name}'

    if node_id in self.dg.nodes:
      assert self.dg.nodes[node_id]['type'] == type_, (domain, name, self.dg.nodes[node_id]['type'], type_)
    else:
      self.dg.add_node(node_id, domain=domain, name=name, type=type_, graphics={'h': 30.0, 'w': 7 * len(name), 'fill': self.color[domain]})
    self.dg.nodes[node_id][client] = True

  def load(self, schema, client):
    print('  loading', client)
    typeof = {}
    for field, meta in schema.meta.fields.items():
      typeof[field] = meta.type

    # add nodes & edges
    baseFields = {}
    for field, baseField in {str(f.path): f.value for f in jsonpath.parse('$.itemTypes.*.fields.*').find(schema)}.items():
      baseFields[field] = baseField
      self.add_var(domain='zotero', name=baseField, type_=typeof.get(baseField, 'text'), client=client)

    for field in jsonpath.parse('$.itemTypes.*.creatorTypes[*]').find(schema):
      self.add_var(domain='zotero', name=field.value, type_='name', client=client)

    for fields in jsonpath.parse('$.csl.fields.text').find(schema):
      for csl, zotero in fields.value.items():
        self.add_var(domain='csl', name=csl, type_='text', client=client)
        for field in zotero:
          self.add_var(domain='zotero', name=field, type_='text', client=client)
          self.add_mapping(from_=('csl', csl), to=('zotero', field))

    for fields in jsonpath.parse('$.csl.fields.date').find(schema):
      for csl, zotero in fields.value.items():
        self.add_var(domain='csl', name=csl, type_='date', client=client)
        if type(zotero) == str: zotero = [zotero] # juris-m has a list here, zotero strings
        for field in zotero:
          self.add_var(domain='zotero', name=field, type_='date', client=client)
          self.add_mapping(from_=('csl', csl), to=('zotero', field))

    for zotero, csl in schema.csl.names.items():
      self.add_var(domain='csl', name=csl, type_='name', client=client)
      self.add_var(domain='zotero', name=zotero, type_='name', client=client)
      self.add_mapping(from_=('csl', csl), to=('zotero', zotero))

    for field, type_ in schema.csl.unmapped.items():
      if type_ != 'type': self.add_var(domain='csl', name=field, type_=type_, client=client)

    for locale in jsonpath.parse('$.locales.*.fields.*').find(schema):
      name = str(locale.path)
      label = locale.value
      # no multiline fields
      if name in [ 'abstractNote', 'extra' ]: continue
      print(label, '=>', name)

    # add labels
    for node, data in list(self.dg.nodes(data=True)):
      if data['domain'] == 'label': continue # how is this possible?
      self.add_label(domain=data['domain'], name=data['name'], label=data['name'])

    for field, baseField in {str(f.path): f.value for f in jsonpath.parse('$.itemTypes.*.fields.*').find(schema)}.items():
      if field == baseField: continue
      self.add_label(domain='zotero', name=baseField, label=field)

    for alias, field in schema.csl.alias.items():
      self.add_label(domain='csl', name=field, label=alias)

    # translations
    # for name, label in [(str(f.path), f.value) for f in jsonpath.parse('$.locales.*.fields.*').find(schema)]:
    #   name = baseFields.get(name, name)
    #   if name in ['dateAdded', 'dateModified', 'itemType']: continue
    #   self.add_label(domain='zotero', name=name, label=label)
    #
    # for name, label in {str(f.path): f.value for f in jsonpath.parse('$.locales.*.creatorTypes.*').find(schema)}.items():
    #   name = baseFields.get(name, name)
    #   self.add_label(domain='zotero', name=name, label=label)

  def add_change(self, label, change):
    if not label or label == '':
      return str(change)
    else:
      return ','.join(label.split(',') + [ str(change) ])

  def save(self):
    stringizer = lambda x: self.dg.nodes[x]['name'] if x in self.dg.nodes else x

    # remove multi-line text fields
    for node, data in list(self.dg.nodes(data=True)):
      if data['domain'] + '.' + data['name'] in [ 'zotero.abstractNote', 'zotero.extra', 'csl.abstract', 'csl.note' ]:
        self.dg.remove_node(node)

    # remove two or more incoming var edges, as that would incur overwrites (= data loss)
    removed = set()
    for node, data in self.dg.nodes(data=True):
      incoming = reduce(lambda acc, edge: acc[self.dg.nodes[edge[0]]['domain']].append(edge) or acc, self.dg.in_edges(node), Munch(zotero=[], csl=[], label=[]))
      for domain, edges in incoming.items():
        if domain == 'label' or len(edges) < 2: continue

        self.changeid += 1
        for edge in edges:
          removed.add(edge)
          self.dg.edges[edge].update({
            'removed': True,
            'label': self.add_change(self.dg.edges[edge].get('label'), self.changeid),
            'graphics': { 'style': 'dashed', 'fill': self.color.removed, 'targetArrow': 'standard' },
            'LabelGraphics': { 'color': self.color.label },
          })

    # hop-through labels. Memorize here which labels had a direct connection *before any expansion*
    labels = {
      label: set([self.dg.nodes[edge[1]]['domain'] for edge in self.dg.out_edges(label)])
      for label, data in self.dg.nodes(data=True)
      if data['domain'] == 'label' and not re.search(r'[-_A-Z]', data['name']) # a label but not a shadow label
    }
    for u, vs in dict(nx.all_pairs_dijkstra_path(self.dg, weight=lambda u, v, d: None if d.get('removed', False) else 1)).items():
      # only interested in shortest paths that originate in a label
      if not u in labels: continue

      for v, path in vs.items():
        if u == v: continue # no loops obviously
        if self.dg.has_edge(u, v): continue # already in place
        if len(path) != 3: continue # only consider one-step hop-through

        # TODO: label already has direct edge to the hop-through domain -- this entails fanning out the data unnecesarily
        if self.dg.nodes[v]['domain'] in labels[u]: continue

        self.changeid += 1
        for edge in zip(path, path[1:]):
          self.dg.edges[edge].update({
            'label': self.add_change(self.dg.edges[edge].get('label'), self.changeid),
          })
        self.dg.add_edge(u, v, label=str(self.changeid), added=True, graphics={ 'style': 'dashed', 'fill': self.color.added, 'targetArrow': 'standard' })

    for u, vs in dict(nx.all_pairs_shortest_path(self.dg)).items():
      if self.dg.nodes[u]['domain'] != 'label': continue
      for v, path in vs.items():
        # length of 3 means potential hop-through node
        if u != v and len(path) == 3 and len(set(zip(path, path[1:])).intersection(removed)) > 0:
          #print('removed', path)
          pass

    #for i, sg in enumerate(nx.weakly_connected_components(self.dg)):
    #  nx.draw(self.dg.subgraph(sg), with_labels=True)
    #  plt.savefig(f'{i}.png')

    mapping = {}
    for label, data in list(self.dg.nodes(data=True)):
      if data['domain'] != 'label': continue
      name = data['name']

      var_nodes = [var for _, var in self.dg.out_edges(label)]
      if len(var_nodes) == 0:
        self.dg.remove_node(label)
      else:
        for var_id in var_nodes:
          var = self.dg.nodes[var_id]
          # print('debug:', name, var_id, var)
          if not name in mapping: mapping[name] = {}
          assert mapping[name].get('type') in (None, var['type']), (var_id, mapping[name].get('type'), var)
          mapping[name]['type'] = var['type']

          domain = var['domain']
          if not domain in mapping[name]: mapping[name][domain] = []
          mapping[name][domain].append(var['name'])

    # ensure names don't get mapped to multiple fields
    for var, mapped in mapping.items():
      if mapped['type'] != 'name': continue
      assert len(mapped.get('zotero', [])) <= 1, (var, mapped)
      assert len(mapped.get('csl', [])) <= 1, (var, mapped)

    # docs
    with open(os.path.join(root, 'site/layouts/shortcodes/extra-fields.md'), 'w') as f:
      writer = MarkdownTableWriter()
      writer.headers = ['label', 'type', 'zotero/jurism', 'csl']
      writer.value_matrix = []
      doc = {}
      for label, data in self.dg.nodes(data=True):
        if not ' ' in label or data['domain'] != 'label': continue
        name = data['name']
        doc[name] = {'zotero': [], 'csl': []}
        for _, to in self.dg.out_edges(label):
          data = self.dg.nodes[to]

          if not 'type' in doc[name]:
            doc[name]['type'] = data['type']
          else:
            assert doc[name]['type'] == data['type']

          if data.get('zotero', False) == data.get('jurism', False):
            postfix = ''
          elif data.get('zotero'):
            postfix = '\u00B2'
          else:
            postfix = '\u00B9'
          doc[name][data['domain']].append(data['name'].replace('_', '\\_') + postfix)
      for label, data in sorted(doc.items(), key=lambda x: x[0]):
        writer.value_matrix.append((f'**{label}**', data['type'], ' / '.join(sorted(data['zotero'])), ' / '.join(sorted(data['csl']))))
      writer.stream = f
      writer.write_table()

    with open(os.path.join(ITEMS, 'extra-fields.json'), 'w') as f:
      json.dump(mapping, f, sort_keys=True, indent='  ')

    # remove phantom labels for clarity
    for label in [node for node, data in self.dg.nodes(data=True) if data['domain'] == 'label' and 'LabelGraphics' in data]:
      self.dg.remove_node(label)
    nx.write_gml(self.dg, 'mapping.gml', stringizer)
    #with open('extra-fields-graph.json', 'w') as f:
    #  json.dump(json_graph.node_link_data(self.dg, {"link": "edges", "source": "from", "target": "to"}), f)
    #  # https://github.com/vasturiano/3d-force-graph
    # https://neo4j.com/developer-blog/visualizing-graphs-in-3d-with-webgl/

    #with open('mapping.json', 'w') as f:
    #  data = nx.readwrite.json_graph.node_link_data(self.dg)
    #  for node in data['nodes']:
    #    node.pop('graphics', None)
    #    node.pop('type', None)
    #    node['label'] = node.pop('name')
    #  for link in data['links']:
    #    link.pop('graphics', None)
    #    link.pop('LabelGraphics', None)
    #  json.dump(data, f, indent='  ')

with fetch('zotero') as z, fetch('jurism') as j:
  print('  writing extra-fields')
  ef = ExtraFields()

  SCHEMA.zotero = Munch.fromDict(patch(json.load(z), 'schema.patch', 'zotero.patch'))
  SCHEMA.jurism = Munch.fromDict(patch(json.load(j), 'schema.patch', 'jurism.patch'))

  #with open('schema.json', 'w') as f:
  #  json.dump(SCHEMA.jurism, f, indent='  ')

  # test for inconsistent basefield mapping
  for schema in ['jurism', 'zotero']:
    fieldmap = {}
    for field_path, field, baseField in [(str(f.full_path), str(f.path), f.value) for f in jsonpath.parse(f'$.itemTypes.*.fields.*').find(SCHEMA[schema])]:
      if not field in fieldmap:
        fieldmap[field] = baseField
      else:
        assert baseField == fieldmap[field], (schema, field_path, baseField, fieldmap[field])

  ef.load(SCHEMA.jurism, 'jurism')
  ef.load(SCHEMA.zotero, 'zotero')
  ef.save()

  with open(os.path.join(SCHEMA.root, 'hashes.json')) as f:
    min_version = {}
    hashes = json.load(f, object_hook=OrderedDict)
    for client in hashes.keys():
      releases = [rel for rel, h in hashes[client].items() if h is not None]
      current = releases[-1]
      min_version[client] = current
      for rel in reversed(releases):
        if hashes[client][rel] != hashes[client][current]:
          break
        else:
          min_version[client] = rel

    print('******** UGLY HACK FOR #2099 *********')
    min_version={ client: '5.2.7182818284590452' if ver == '6.0' else ver for client, ver in min_version.items() }
    with open(os.path.join(root, 'schema', 'supported.json'), 'w') as f:
      json.dump(min_version, f)

print('  writing creators')
creators = {'zotero': {}, 'jurism': {}}
for creatorTypes in jsonpath.parse('*.itemTypes.*.creatorTypes').find(SCHEMA):
  if len(creatorTypes.value) == 0: continue
  client, itemType = operator.itemgetter(0, 2)(str(creatorTypes.full_path).split('.'))

  if not itemType in creators[client]: creators[client][itemType] = []
  for creatorType in creatorTypes.value:
    creators[client][itemType].append(creatorType)
with open(os.path.join(ITEMS, 'creators.json'), 'w') as f:
  json.dump(creators, f, indent='  ', default=lambda x: list(x))

def template(tmpl):
  return Template(filename=os.path.join(root, 'setup/templates', tmpl))

print('  writing typing for serialized item')
with open(os.path.join(TYPINGS, 'serialized-item.d.ts'), 'w') as f:
  fields = sorted(list(set(field.value for field in jsonpath.parse('*.itemTypes.*.fields.*').find(SCHEMA))))
  itemTypes = sorted(list(set(field.value for field in jsonpath.parse('*.itemTypes.*.itemType').find(SCHEMA))))
  print(template('items/serialized-item.d.ts.mako').render(fields=fields, itemTypes=itemTypes).strip(), file=f)

print('  writing field simplifier')
with open(os.path.join(ITEMS, 'items.ts'), 'w') as f:
  valid = Munch(type={}, field={})
  for itemType in jsonpath.parse('*.itemTypes.*.itemType').find(SCHEMA):
    client = str(itemType.full_path).split('.')[0]
    itemType = itemType.value

    if not itemType in valid.type:
      valid.type[itemType] = client
      if itemType == 'note':
        valid.field[itemType] = {field: 'true' for field in 'itemType tags note id itemID dateAdded dateModified'.split(' ')}
      elif itemType == 'attachment':
        valid.field[itemType] = {field: 'true' for field in 'itemType tags id itemID dateAdded dateModified'.split(' ')}
      else:
        valid.field[itemType] = {field: 'true' for field in 'itemType creators tags attachments notes seeAlso id itemID dateAdded dateModified multi'.split(' ')}
    elif valid.type[itemType] != client:
      valid.type[itemType] = 'true'

  for field in jsonpath.parse('*.itemTypes.*.fields.*').find(SCHEMA):
    client, itemType = operator.itemgetter(0, 2)(str(field.full_path).split('.'))
    for field in [str(field.path), field.value]:
      if not field in valid.field[itemType]:
        valid.field[itemType][field] = client
      elif valid.field[itemType][field] != client:
        valid.field[itemType][field] = 'true'

  jsonschema = sqlite3.connect(':memory:')
  jsonschema.execute('CREATE TABLE valid (client, itemType, field)')
  for itemType, fields in valid.field.items():
    for field, validfor in fields.items():
      validfor = ['zotero', 'jurism'] if validfor == 'true' else [validfor]
      for client in validfor:
        jsonschema.execute('INSERT INTO valid (client, itemType, field) VALUES (?, ?, ?)', (client, itemType, field))

  for client in ['zotero', 'jurism']:
    schema = {
      'type': 'object',
      'discriminator': { 'propertyName': 'itemType' },
      'required': ['itemType'],
      'oneOf': [],
      '$defs': {
        'attachments': {
          'type': 'array',
          'items': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
              'path': { 'type': 'string' },
              'accessDate': { 'type': 'string' },
              'contentType': { 'type': 'string' },
              'itemType': { 'type': 'string' },
              'mimeType': { 'type': 'string' },
              'key': { 'type': 'string' },
              'linkMode': { 'type': 'string' },
              'title': { 'type': 'string' },
              'uri': { 'type': 'string' },
              'url': { 'type': 'string' },
            }
          }
        },

        'creators': {
          'type': 'array',
          'items': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
              'creatorType': { 'type': 'string' },
              'firstName': { 'type': 'string' },
              'lastName': { 'type': 'string' },
              'fieldMode': { 'type': 'number' },
              'multi': { 'type': 'object' },
            }
          }
        },

        'notes': {
          'type': 'array',
          'items': { 'type': 'string' },
        },

        'tags': {
          'type': 'array',
          'items': {
            'oneOf': [
              {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                  'tag': { 'type': 'string' },
                  'type': { 'type': 'number' },
                },
                'required': ['tag'],
              },
              { 'type': 'string' }
            ],
          },
        },

        'edition': {
          'oneOf': [
            { 'type': 'string' },
            { 'type': 'number' },
          ]
        },

        'multi': { 'type': 'object' },
        'seeAlso': { 'type': 'array' },

      }
    }
    for itemType, in jsonschema.execute('SELECT DISTINCT itemType FROM valid WHERE client = ? ORDER BY itemType', (client,)):
      schema['oneOf'].append({
        'type': 'object',
        'additionalProperties': False,
        'properties': {
           'itemType': { 'const': itemType },
         },
      })
      for field, in jsonschema.execute('SELECT field FROM valid WHERE client = ? AND itemType = ? ORDER BY field', (client, itemType)):
        if field == 'itemType': continue
        assert field not in schema['oneOf'][-1]['properties'], (itemType, field)

        if field in schema['$defs']:
          schema['oneOf'][-1]['properties'][field] = { '$ref': '#/$defs/' + field }
        elif field in ['itemID']:
          schema['oneOf'][-1]['properties'][field] = { 'type': 'number' }
        else:
          schema['oneOf'][-1]['properties'][field] = { 'type': 'string' }

    with open(os.path.join(ITEMS, client + '.schema'), 'w') as v:
      json.dump(schema, v, indent='  ')

  # map aliases to base names
  DG = nx.DiGraph()
  for field in jsonpath.parse('*.itemTypes.*.fields.*').find(SCHEMA):
    client = str(field.full_path).split('.')[0]
    baseField = field.value
    field = str(field.path)
    if field == baseField: continue

    if not (data := DG.get_edge_data(field, baseField, default=None)):
      DG.add_edge(field, baseField, client=client)
    elif data['client'] != client:
      DG.edges[field, baseField]['client'] = 'both'
  aliases = {}
  for field, baseField, client in DG.edges.data('client'):
    if not client in aliases: aliases[client] = {}
    if not baseField in aliases[client]: aliases[client][baseField] = []
    aliases[client][baseField].append(field)

  # map names to basenames
  names = Munch(field={}, type={})
  names.field['dateadded'] = Munch(jurism='dateAdded', zotero='dateAdded')
  names.field['datemodified'] = Munch(jurism='dateModified', zotero='dateModified')
  labels = {}
  for field in jsonpath.parse('*.itemTypes.*.fields.*').find(SCHEMA):
    client, itemType = operator.itemgetter(0, 2)(str(field.full_path).split('.'))
    baseField = field.value
    field = str(field.path)
    for section, field, name in [('field', field.lower(), baseField), ('field', baseField.lower(), baseField), ('type', itemType.lower(), itemType)]:
      if not field in names[section]:
        names[section][field] = Munch.fromDict({ client: name })
      elif not client in names[section][field]:
        names[section][field][client] = name
      else:
        assert names[section][field][client] == name, (client, section, field, names[section][field][client], name)

      if name == 'numPages':
        label = 'Number of pages'
      else:
        label = name[0].upper() + re.sub('([a-z])([A-Z])', lambda m: m.group(1) + ' ' + m.group(2).lower(), re.sub('[-_]', ' ', name[1:]))
      if not field in labels:
        labels[field] = Munch.fromDict({ client: label })
      elif not client in labels[field]:
        labels[field][client] = label
      else:
        assert labels[field][client] == label, (client, field, labels[field][client], label)

  try:
    print(template('items/items.ts.mako').render(names=names, labels=labels, valid=valid, aliases=aliases, schemas=SCHEMA).strip(), file=f)
  except:
    print(exceptions.text_error_template().render())
  #stringizer = lambda x: DG.nodes[x]['name'] if x in DG.nodes else x
  #nx.write_gml(DG, 'fields.gml') # , stringizer)

print('  writing csl-types')
with open(os.path.join(ITEMS, 'csl-types.json'), 'w') as f:
  types = set()
  for type_ in jsonpath.parse('*.csl.types.*').find(SCHEMA):
    types.add(str(type_.full_path).split('.')[-1])
  for type_ in jsonpath.parse('*.csl.unmapped.*').find(SCHEMA):
    if type_.value == 'type': types.add(str(type_.full_path).split('.')[-1])
  json.dump(list(types), f)
