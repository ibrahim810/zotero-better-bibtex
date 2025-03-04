/* eslint-disable no-case-declarations */
import EDTF = require('edtf')
import edtfy = require('edtfy')
// import * as CSL from 'citeproc'

// import escapeStringRegexp = require('escape-string-regexp')

import * as months from '../gen/dateparser-months.json'

import { getLocaleDateOrder } from '../submodules/zotero-utilities/date'

export type ParsedDate = {
  type?: 'date' | 'open' | 'verbatim' | 'season' | 'interval' | 'list'
  year?: number
  month?: number
  day?: number
  orig?: ParsedDate
  verbatim?: string
  from?: ParsedDate
  to?: ParsedDate
  dates?: ParsedDate[]
  season?: number
  uncertain?: boolean
  approximate?: boolean
}

const months_re = new RegExp(Object.keys(months).sort((a, b) => b.length - a.length).join('|'), 'i')

const Season = new class {
  private ranges = [
    [ 13, 14, 15, 16 ], // eslint-disable-line no-magic-numbers
    [ 21, 22, 23, 24 ], // eslint-disable-line no-magic-numbers
  ]

  public fromMonth(month: number): number {
    for (const range of this.ranges) {
      if (range.includes(month)) return (month - range[0]) + 1
    }
    return undefined
  }

  public seasonize(date: ParsedDate): ParsedDate {
    const season = this.fromMonth(date.month)
    if (date.type === 'date' && typeof season === 'number') {
      date.type = 'season'
      date.season = season
      delete date.month
    }
    return date
  }
}

function doubt(date: ParsedDate, state: { uncertain: boolean, approximate: boolean }): ParsedDate {
  if (state.uncertain) date.uncertain = true
  if (state.approximate) date.approximate = true
  return date
}

function normalize_edtf(date: any): ParsedDate {
  let year, month, day

  switch (date.type) {
    case 'Date':
      [ year, month, day ] = date.values
      if (typeof month === 'number') month += 1
      return doubt({ type: 'date', year, month, day}, {approximate: date.approximate || date.unspecified, uncertain: date.uncertain })

    case 'Interval':
      // eslint-disable-next-line no-magic-numbers
      if (date.values.length !== 2) throw new Error(JSON.stringify(date))
      const from: ParsedDate = date.values[0] ? normalize_edtf(date.values[0]) : { type: 'open' }
      const to: ParsedDate = date.values[1] ? normalize_edtf(date.values[1]) : { type: 'open' }
      return { type: 'interval', from, to }

    case 'Season':
      [ year, month ] = date.values
      if (typeof Season.fromMonth(month) !== 'number') throw new Error(`Unexpected season ${month}`)
      return Season.seasonize({ type: 'date', year, month })

    case 'List':
      return { type: 'list', dates: date.values.map(normalize_edtf) }

    default:
      throw new Error(JSON.stringify(date))
  }
}

function upgrade_edtf(date: string): string {
  return date
    .replace(/unknown/g, '')
    .replace(/u/g, 'X')
    .replace(/(\?~)|(~\?)/g, '%')
    .replace(/open/g, '')
    .replace(/\.\./g, '')
    .replace(/y/g, 'Y')
}

function is_valid_month(month: number, allowseason: boolean) {
  if (month >= 1 && month <= 12) return true // eslint-disable-line no-magic-numbers
  if (allowseason && Season.fromMonth(month)) return true

  return false
}

function has_valid_month(date: ParsedDate) {
  return date.type === 'date' && typeof date.month === 'number' && is_valid_month(date.month, true)
}

function is_valid_date(date: ParsedDate) {
  if (date.type !== 'date') return true
  if (typeof date.year !== 'number') return false
  date = {...date}
  if (typeof date.month === 'number' && Season.fromMonth(date.month)) {
    if (typeof date.day !== 'undefined') return false
    date.month = 1
  }
  const d = new Date(`${date.year}-${date.month || 1}-${date.day || 1}`)
  return (d instanceof Date) && !isNaN(d as unknown as number)
}

// swap day/month for our American friends
function swap_day_month(day: number, month: number, fix_only = false): number[] {
  if (!day) return [ undefined, month ]

  if (!is_valid_month(month, false) && is_valid_month(day, false)) return [month, day]
  if (!fix_only && getLocaleDateOrder() === 'mdy' && is_valid_month(day, false)) return [month, day]
  return [day, month]
}

function stripTime(date: string): string {
  return date.replace(/(\s+|T)[0-9]{2}:[0-9]{2}(:[0-9]{2}(Z|\+[0-9]{2}:?[0-9]{2})?)?$/, '')
}

export function parse(value: string): ParsedDate {
  const date = parseToDate(value, false)

  /*
  if (date.type === 'verbatim') {
    const csl = CSL.DateParser.parseDateToObject(value)
    if (typeof csl.year === 'number') {
      if (csl.day_end === csl.day) delete csl.day_end
      if (csl.month_end === csl.month) delete csl.month_end
      if (csl.year_end === csl.year) delete csl.year_end
      const from = { type: 'date', year: csl.year, month: csl.month, day: csl.day }
      const to = { type: 'date', year: csl.year_end, month: csl.month_end, day: csl.day_end }
      if (is_valid_date(from) && (!to.year || is_valid_date(to))) {
        return to.year ? { type: 'interval', from: Season.seasonize(from as ParsedDate), to: Season.seasonize(to as ParsedDate) } : Season.seasonize(from as ParsedDate)
      }
    }
  }
  */

  return date
}

function parseToDate(value: string, as_single_date: boolean): ParsedDate {
  value = (value || '').trim()
  let date: ParsedDate

  let m: RegExpMatchArray

  if (value === 'today') {
    const now = new Date
    return { type: 'date', year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate() }
  }

  if (value === '') return { type: 'open' }

  // https://forums.zotero.org/discussion/73729/name-and-year-import-issues-with-new-nasa-ads#latest
  if (m = (/^(-?[0-9]+)-00-00$/.exec(value) || /^(-?[0-9]+)\/00\/00$/.exec(value) || /^(-?[0-9]+-[0-9]+)-00$/.exec(value))) return parseToDate(m[1], true)

  // https://github.com/retorquere/zotero-better-bibtex/issues/1513
  // eslint-disable-next-line no-magic-numbers
  if ((m = (/^([0-9]+) (de )?([a-z]+) (de )?([0-9]+)$/i).exec(value)) && (m[2] || m[4]) && (months[m[3].toLowerCase()])) return parseToDate(`${m[1]} ${m[3]} ${m[5]}`, true)

  // '30-Mar-2020'
  if (m = (/^([0-9]+)-([a-z]+)-([0-9]+)$/i).exec(value)) {
    let [ , day, month, year ] = m
    if (parseInt(day) > 31 && parseInt(year) < 31) [ day, year ] = [ year, day ] // eslint-disable-line no-magic-numbers
    date = parseToDate(`${month} ${day} ${year}`, true)
    // eslint-disable-next-line @typescript-eslint/no-unsafe-return
    if (date.type === 'date') return date
  }

  // '[origdate] date'
  if (!as_single_date && (m = /^\[(.+)\]\s*(.+)$/.exec(value))) {
    const [ , _orig, _date ] = m
    date = parseToDate(_date, true)
    const orig = parseToDate(_orig, true)
    // eslint-disable-next-line @typescript-eslint/no-unsafe-return
    if (date.type === 'date' && orig.type === 'date') return {...date, ...{ orig } }
  }

  // 'date [origdate]'
  if (!as_single_date && (m = /^(.+)\s*\[(.+)\]$/.exec(value))) {
    const [ , _date, _orig ] = m
    date = parseToDate(_date, true)
    const orig = parseToDate(_orig, true)
    // eslint-disable-next-line @typescript-eslint/no-unsafe-return
    if (date.type === 'date' && orig.type === 'date') return {...date, ...{ orig } }
  }

  // '[origdate]'
  if (!as_single_date && (m = /^\[(.+)\]$/.exec(value))) {
    const [ , _orig ] = m
    const orig = parseToDate(_orig, true)
    if (orig.type === 'date') return { ...{ orig } }
  }

  // 747 'jan 20-22 1977'
  if (!as_single_date && (m = /^([a-zA-Z]+)\s+([0-9]+)(?:--|-|–)([0-9]+)[, ]\s*([0-9]+)$/.exec(value))) {
    const [ , month, day1, day2, year ] = m

    const from = parseToDate(`${month} ${day1} ${year}`, true)
    const to = parseToDate(`${month} ${day2} ${year}`, true)

    if (from.type === 'date' && to.type === 'date') return { type: 'interval', from, to }
  }

  // 747, January 30–February 3, 1989
  if (!as_single_date && (m = /^([a-zA-Z]+\s+[0-9]+)(?:--|-|–)([a-zA-Z]+\s+[0-9]+)[, ]\s*([0-9]+)$/.exec(value))) {
    const [ , date1, date2, year ] = m

    const from = parseToDate(`${date1} ${year}`, true)
    const to = parseToDate(`${date2} ${year}`, true)

    if (from.type === 'date' && to.type === 'date') return { type: 'interval', from, to }
  }

  // 746, 22-26 June 2015, 29 June-1 July 2011
  if (!as_single_date && (m = /^([0-9]+)\s*([a-zA-Z]+)?\s*(?:--|-|–)\s*([0-9]+)\s+([a-zA-Z]+)\s+([0-9]+)$/.exec(value))) {
    const [ , day1, month1, day2, month2, year ] = m

    const from = parseToDate(`${month1 || month2} ${day1} ${year}`, true)
    const to = parseToDate(`${month2} ${day2} ${year}`, true)

    if (from.type === 'date' && to.type === 'date') return { type: 'interval', from, to }
  }

  // July-October 1985
  if (!as_single_date && (m = (/^([a-z]+)(?:--|-|–)([a-z]+)(?:--|-|–|\s+)([0-9]+)$/i).exec(value))) {
    const [ , month1, month2, year ] = m

    const from = parseToDate(`${month1} ${year}`, true)
    const to = parseToDate(`${month2} ${year}`, true)

    if (from.type === 'date' && to.type === 'date') return { type: 'interval', from, to }
  }

  const state = {approximate: false, uncertain: false}
  const exactish = stripTime(value.replace(/[~?]+$/, match => {
    state.approximate = match.indexOf('~') >= 0
    state.uncertain = match.indexOf('?') >= 0
    return ''
  }).replace(/\s+/g, ' '))

  // these assume a sensible y/m/d format by default. There's no sane way to guess between y/d/m and y/m/d, and y/d/m is
  // just wrong. https://en.wikipedia.org/wiki/Date_format_by_country
  if (m = /^(-?[0-9]{3,})([-\s/.])([0-9]{1,2})(\2([0-9]{1,2}))?$/.exec(exactish)) {
    const [ , _year, , _month, , _day ] = m
    const year = parseInt(_year)
    const [day, month] = swap_day_month(parseInt(_day), parseInt(_month), true)

    // if (!month && !day) return doubt({ type: 'date', year }, state)
    if (!day && has_valid_month(date = { type: 'date', year, month })) return Season.seasonize(doubt(date, state))
    if (is_valid_date(date = { type: 'date', year, month, day })) return doubt(date, state)
  }

  // https://github.com/retorquere/zotero-better-bibtex/issues/1112
  if (m = /^([0-9]{1,2})\s+([0-9]{1,2})\s*,\s*([0-9]{4,})$/.exec(exactish)) {
    const [ , _day, _month, _year ] = m
    const year = parseInt(_year)
    const [day, month] = swap_day_month(parseInt(_day), parseInt(_month))

    if (!month && !day) return doubt({ type: 'date', year }, state)
    if (!day && has_valid_month(date = { type: 'date', year, month })) return Season.seasonize(doubt(date, state))
    if (is_valid_date(date = { type: 'date', year, month, day })) return doubt(date, state)
  }

  if (m = /^([0-9]{1,2})([-\s/.])([0-9]{1,2})(\2([0-9]{3,}))$/.exec(exactish)) {
    const [ , _day, , _month, , _year ] = m
    const year = parseInt(_year)
    const [day, month] = swap_day_month(parseInt(_day), parseInt(_month))

    if (!month && !day) return doubt({ type: 'date', year }, state)
    if (!day && has_valid_month(date = { type: 'date', year, month })) return Season.seasonize(doubt(date, state))
    if (is_valid_date(date = { type: 'date', year, month, day })) return doubt(date, state)
  }

  if (m = /^([0-9]{1,2})[-\s/.]([0-9]{3,})$/.exec(exactish)) {
    const [ , _month, _year ] = m
    const month = parseInt(_month)
    const year = parseInt(_year)

    if (!month) return doubt({ type: 'date', year }, state)
    if (has_valid_month(date = { type: 'date', year, month })) return Season.seasonize(doubt(date, state))
  }

  if (m = /^([0-9]{3,})[-\s/.]([0-9]{1,2})$/.exec(exactish)) {
    const [ , _year, _month ] = m
    const year = parseInt(_year)
    const month = parseInt(_month)

    if (!month) return doubt({ type: 'date', year }, state)
    if (has_valid_month(date = { type: 'date', year, month })) return Season.seasonize(doubt(date, state))
  }

  if (exactish.match(/^-?[0-9]{3,}$/)) {
    return doubt({ type: 'date', year: parseInt(exactish) }, state)
  }

  try {
    // https://github.com/inukshuk/edtf.js/issues/5
    const edtf = normalize_edtf(EDTF.parse(upgrade_edtf(stripTime(value.replace(/_|--/, '/')))))
    if (edtf) return edtf
  }
  catch (err) {
  }

  try {
    const edtf = normalize_edtf(EDTF.parse(edtfy(value
      .normalize('NFC')
      .replace(/\. /, ' ') // 8. july 2011
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return
      .replace(months_re, _ => months[_.toLowerCase()] || _)
    )))
    if (edtf) return edtf
  }
  catch (err) {
  }

  // https://github.com/retorquere/zotero-better-bibtex/issues/868
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  if (m = /^([0-9]{3,})\s([^0-9]+)(?:\s+([0-9]+))?$/.exec(value.normalize('NFC').replace(months_re, _ => months[_.toLowerCase()] || _))) {
    const [ , year, month, day ] = m
    if (months[month]) {
      try {
        const edtf = normalize_edtf(EDTF.parse(edtfy(`${day || ''} ${month} ${year}`.trim())))
        if (edtf) return edtf
      }
      catch (err) {
      }
    }
  }

  if (!as_single_date) { // try ranges
    for (const sep of ['--', '-', '/', '_', '–']) {
      const split = value.split(sep)
      if (split.length === 2) {
        const from = parseToDate(split[0], true)
        if (from.type !== 'date' && from.type !== 'season') continue
        const to = parseToDate(split[1], true)
        if (to.type !== 'date' && to.type !== 'season') continue
        return { type: 'interval', from, to }
      }
    }
  }

  return { type: 'verbatim', verbatim: value }
}

function testEDTF(value: string): boolean {
  try {
    return (EDTF.parse(value, { level: 1 }) as boolean)
  }
  catch (err) {
    return false
  }
}

export function isEDTF(value: string, minuteLevelPrecision = false): boolean {
  value = upgrade_edtf(value)

  return testEDTF(value) || (minuteLevelPrecision && testEDTF(`${value}:00`))
}

export function strToISO(str: string): string {
  let date = parse(str)
  if (date.type === 'interval') date = date.from

  if (typeof date.year !== 'number') return ''

  let iso = `${date.year}`.padStart(4, '0') // eslint-disable-line no-magic-numbers

  if (typeof date.month === 'number') {
    const month = `${date.month}`.padStart(2, '0')
    iso += `-${month}`
    if (date.day) {
      const day = `${date.day}`.padStart(2, '0')
      iso += `-${day}`
    }
  }

  return iso
}
