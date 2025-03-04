declare const Zotero: any

import { Translator } from './lib/translator'
import type { TranslatorHeader } from './lib/translator'
export { Translator }

declare var ZOTERO_TRANSLATOR_INFO: TranslatorHeader // eslint-disable-line no-var

import * as itemfields from '../gen/items/items'
const version = require('../gen/version.js')
import { stringify } from '../content/stringify'
import { log } from '../content/logger'
import { normalize } from './lib/normalize'

const chunkSize = 0x100000

export function detectImport(): boolean {
  let str
  let json = ''
  while ((str = Zotero.read(chunkSize)) !== false) {
    json += str
    if (json[0] !== '{') return false
  }

  let data
  try {
    data = JSON.parse(json)
  }
  catch (err) {
    return false
  }

  if (!data.config || (data.config.id !== ZOTERO_TRANSLATOR_INFO.translatorID)) return false
  return true
}

export async function doImport(): Promise<void> {
  Translator.init('import')

  let str
  let json = ''
  while ((str = Zotero.read(chunkSize)) !== false) {
    json += str
  }

  const data = JSON.parse(json)
  if (!data.items || !data.items.length) return

  const items = new Set
  for (const source of (data.items as any[])) {
    itemfields.simplifyForImport(source)

    // I do export these but the cannot be imported back
    delete source.relations
    delete source.citekey
    delete source.citationKey

    delete source.uri
    delete source.key
    delete source.version
    delete source.libraryID
    delete source.collections
    delete source.autoJournalAbbreviation

    if (source.creators) {
      for (const creator of source.creators) {
        // if .name is not set, *both* first and last must be set, even if empty
        if (!creator.name) {
          creator.lastName = creator.lastName || ''
          creator.firstName = creator.firstName || ''
        }
      }
    }

    // clear out junk data
    for (const [field, value] of Object.entries(source)) {
      if ((value ?? '') === '') delete source[field]
    }
    // validate tests for strings
    if (Array.isArray(source.extra)) source.extra = source.extra.join('\n')
    // marker so BBT-JSON can be imported without extra-field meddling
    if (source.extra) source.extra = `\x1BBBT\x1B${source.extra}`

    const error = itemfields.valid.test(source)
    if (error) throw new Error(error)

    const item = new Zotero.Item()
    Object.assign(item, source)

    for (const att of item.attachments || []) {
      if (att.url) delete att.path
      delete att.relations
      delete att.uri
    }
    await item.complete()
    items.add(source.itemID)
    Zotero.setProgress(items.size / data.items.length * 100) // eslint-disable-line no-magic-numbers
  }
  Zotero.setProgress(100) // eslint-disable-line no-magic-numbers

  const collections: any[] = Object.values(data.collections || {})
  for (const collection of collections) {
    collection.zoteroCollection = new Zotero.Collection()
    collection.zoteroCollection.type = 'collection'
    collection.zoteroCollection.name = collection.name
    collection.zoteroCollection.children = collection.items.filter(id => {
      if (items.has(id)) return true
      log.error(`Collection ${collection.key} has non-existent item ${id}`)
      return false
    }).map(id => ({type: 'item', id}))
  }
  for (const collection of collections) {
    if (collection.parent && data.collections[collection.parent]) {
      data.collections[collection.parent].zoteroCollection.children.push(collection.zoteroCollection)
    }
    else {
      if (collection.parent) log.error(`Collection ${collection.key} has non-existent parent ${collection.parent}`)
      collection.parent = false
    }
  }
  for (const collection of collections) {
    if (collection.parent) continue
    collection.zoteroCollection.complete()
  }
}

export function doExport(): void {
  Translator.init('export')

  let item
  const data = {
    config: {
      id: ZOTERO_TRANSLATOR_INFO.translatorID,
      label: ZOTERO_TRANSLATOR_INFO.label,
      preferences: Translator.preferences,
      options: Translator.options,
    },
    version: {
      zotero: Zotero.Utilities.getVersion(),
      bbt: version,
    },
    collections: Translator.collections,
    items: [],
  }

  const validAttachmentFields = new Set([ 'relations', 'uri', 'itemType', 'title', 'path', 'tags', 'dateAdded', 'dateModified', 'seeAlso', 'mimeType' ])

  while ((item = Zotero.nextItem())) {
    if (Translator.options.dropAttachments && item.itemType === 'attachment') continue

    if (!Translator.preferences.testing) {
      const [ , kind, lib, key ] = item.uri.match(/^https?:\/\/zotero\.org\/(users|groups)\/((?:local\/)?[^/]+)\/items\/(.+)/)
      item.select = (kind === 'users') ? `zotero://select/library/items/${key}` : `zotero://select/groups/${lib}/items/${key}`
    }

    delete item.collections

    itemfields.simplifyForExport(item, { dropAttachments: Translator.options.dropAttachments})
    item.relations = item.relations ? (item.relations['dc:relation'] || []) : []

    for (const att of item.attachments || []) {
      if (Translator.options.exportFileData && att.saveFile && att.defaultPath) {
        att.saveFile(att.defaultPath, true)
        att.path = att.defaultPath
      }
      else if (att.localPath) {
        att.path = att.localPath
      }

      if (!Translator.preferences.testing) {
        const [ , kind, lib, key ] = att.uri.match(/^https?:\/\/zotero\.org\/(users|groups)\/((?:local\/)?[^/]+)\/items\/(.+)/)
        att.select = (kind === 'users') ? `zotero://select/library/items/${key}` : `zotero://select/groups/${lib}/items/${key}`
      }

      if (!att.path) continue // amazon/googlebooks etc links show up as atachments without a path

      att.relations = att.relations ? (att.relations['dc:relation'] || []) : []
      for (const field of Object.keys(att)) {
        if (!validAttachmentFields.has(field)) {
          delete att[field]
        }
      }
    }

    data.items.push(item)
  }

  if (Translator.preferences.testing) normalize(data)

  Zotero.write(stringify(data, null, '  '))
}
