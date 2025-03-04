const path = require('path')
const fs = require('fs')
const exists = fs.existsSync
const esbuild = require('esbuild')
const exec = require('child_process').exec
const glob = require('glob-promise')
const crypto = require('crypto')
const branch = require('git-branch')

const loader = require('./setup/loaders')
const shims = require('./setup/shims')

function execShellCommand(cmd) {
  console.log(cmd)
  return new Promise((resolve, reject) => {
    exec(cmd, (error, stdout, stderr) => {
      if (error) {
        console.warn(error)
      }
      resolve(stdout? stdout : stderr)
    })
  })
}

async function bundle(config) {
  config = {
    bundle: true,
    format: 'iife',
    // define: { BigInt: 'Number' },
    target: ['firefox60'],
    inject: [],
    ...config,
  }

  config.inject.push('./setup/loaders/globals.js')

  const metafile = config.metafile
  config.metafile = true

  let target
  if (config.outfile) {
    target = config.outfile
  }
  else if (config.entryPoints.length === 1 && config.outdir) {
    target = path.join(config.outdir, path.basename(config.entryPoints[0]))
  }
  else {
    target = `${config.outdir} [${config.entryPoints.join(', ')}]`
  }
  console.log('* bundling', target)
  // console.log('  aliasing BigInt to Number for https://github.com/benjamn/ast-types/issues/750')
  const meta = (await esbuild.build(config)).metafile
  if (typeof metafile === 'string') await fs.promises.writeFile(metafile, JSON.stringify(meta, null, 2))
}

async function rebuild() {
  // plugin code
  await bundle({
    entryPoints: [ 'content/better-bibtex.ts' ],
    plugins: [
      loader.trace('plugin'),
      loader.patcher('setup/patches'),
      // loader.bibertool,
      loader.peggy,
      loader.__dirname,
      loader.ajv,
      shims
    ],
    outdir: 'build/content',
    banner: { js: 'if (!Zotero.BetterBibTeX) {\n' },
    footer: { js: '\n}' },
    metafile: 'gen/plugin.json',
    external: [
      'zotero/itemTree',
    ]
  })

  // worker code
  const vars = [ 'Zotero', 'workerJob', 'environment', 'DOMParser' ]
  const globalName = vars.join('__')
  await bundle({
    entryPoints: [ 'content/worker/zotero.ts' ],
    globalName,
    plugins: [
      loader.trace('worker'),
      loader.patcher('setup/patches'),
      // loader.bibertool,
      // loader.peggy,
      loader.ajv,
      loader.__dirname,
      shims
    ],
    outdir: 'build/content/worker',
    footer: {
      // make these var, not const, so they get hoisted and are available in the global scope. See logger.ts
      js: `var { ${vars.join(', ')} } = ${globalName};`,
    },
    metafile: 'gen/worker.json',
    external: [ 'jsdom' ],
  })

  // translators
  for (const translator of (await glob('translators/*.json')).map(tr => path.parse(tr))) {
    const header = require('./' + path.join(translator.dir, translator.name + '.json'))
    const vars = ['Translator']
      .concat((header.translatorType & 1) ? ['detectImport', 'doImport'] : [])
      .concat((header.translatorType & 2) ? ['doExport'] : [])

    const globalName = translator.name.replace(/ /g, '') + '__' + vars.join('__')
    const outfile = path.join('build/resource', translator.name + '.js')

    // https://esbuild.github.io/api/#write
    // https://esbuild.github.io/api/#outbase
    // https://esbuild.github.io/api/#working-directory
    await bundle({
      entryPoints: [path.join(translator.dir, translator.name + '.ts')],
      globalName,
      plugins: [
        // loader.trace('translators'),
        loader.bibertool,
        // loader.peggy,
        loader.__dirname,
        loader.ajv,
        shims
      ],
      outfile,
      banner: { js: `
        if (typeof ZOTERO_TRANSLATOR_INFO === 'undefined') var ZOTERO_TRANSLATOR_INFO = {}; // declare if not declared
        Object.assign(ZOTERO_TRANSLATOR_INFO, ${JSON.stringify(header)}); // assign new data
      `},
      // make these var, not const, so they get hoisted and are available in the global scope. See logger.ts
      footer: { js: `var { ${vars.join(', ')} } = ${globalName};` },
      metafile: `gen/${translator.name}.json`,
    })

    const source = await fs.promises.readFile(outfile, 'utf-8')
    const checksum = crypto.createHash('sha256')
    checksum.update(source)
    if (!header.configOptions) header.configOptions = {}
    header.configOptions.hash = checksum.digest('hex')
    header.lastUpdated = (new Date).toISOString().replace(/T.*/, '')
    await fs.promises.writeFile(path.join('build/resource', translator.name + '.json'), JSON.stringify(header, null, 2))
  }

  if (await branch() === 'headless') {
    let node_modules = loader.node_modules('setup/patches')
    await bundle({
      platform: 'node',
      // target: ['node12'],
      // inject: [ './headless/inject.js' ],
      plugins: [node_modules.plugin, loader.patcher('setup/patches'), loader.bibertool, loader.peggy ],
      bundle: true,
      globalName: 'Headless',
      entryPoints: [ 'headless/zotero.ts' ],
      outfile: 'gen/headless/zotero.js',
      banner: {
        js: 'var ZOTERO_CONFIG = { GUID: "zotero@" };\n',
      },
      footer: {
        js: 'const { Zotero, DOMParser } = Headless;\n'
      },
      metafile: 'gen/headless/zotero.json',
    })
    let external = node_modules.external

    node_modules = loader.node_modules('setup/patches')
    await bundle({
      platform: 'node',
      // target: ['node12'],
      // inject: [ './headless/inject.js' ],
      plugins: [node_modules.plugin, loader.patcher('setup/patches'), loader.bibertool, loader.peggy ],
      bundle: true,
      globalName: 'Headless',
      entryPoints: [ 'headless/index.ts' ],
      outfile: 'gen/headless/index.js',
      metafile: 'gen/headless/index.json',
      banner: {
        js: await fs.promises.readFile('gen/headless/zotero.js', 'utf-8')
      }
    })
    external = [...new Set(external.concat(node_modules.external))].sort()

    const package_json = JSON.parse(await fs.promises.readFile('package.json', 'utf-8'))
    const move = Object.keys(package_json.dependencies).filter(pkg => !external.includes(pkg))
    if (move.length) {
      console.log('  the following packages should be moved to devDependencies')
      for (const pkg of move.sort()) {
        console.log('  *', pkg)
      }
    }
  }
}

rebuild().catch(err => {
  console.log(err)
  process.exit(1)
})
