import { execSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import pngToIco from 'png-to-ico'

const SIZES = [16, 24, 32, 48, 64, 128, 256]
mkdirSync('build/icon-res', { recursive: true })

const ps = [
  "Add-Type -AssemblyName System.Drawing",
  "$src = [System.Drawing.Image]::FromFile((Resolve-Path 'assets/icon.png').Path)",
  ...SIZES.flatMap((s) => [
    `$bmp = New-Object System.Drawing.Bitmap(${s}, ${s})`,
    '$g = [System.Drawing.Graphics]::FromImage($bmp)',
    '$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic',
    `$g.DrawImage($src, 0, 0, ${s}, ${s})`,
    '$g.Dispose()',
    `$bmp.Save((Join-Path (Resolve-Path 'build/icon-res').Path 'icon-${s}.png'), [System.Drawing.Imaging.ImageFormat]::Png)`,
    '$bmp.Dispose()',
  ]),
  '$src.Dispose()',
].join('\n')
writeFileSync('build/gen-icon.ps1', ps, 'utf8')
execSync('powershell -NoProfile -ExecutionPolicy Bypass -File build/gen-icon.ps1', { stdio: 'inherit' })

const ico = await pngToIco(SIZES.map((s) => `build/icon-res/icon-${s}.png`))
const { writeFile } = await import('node:fs/promises')
await writeFile('assets/icon.ico', ico)
console.log(`assets/icon.ico generated (${SIZES.join('/')}px)`)
