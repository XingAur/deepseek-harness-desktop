import { cpSync, mkdirSync } from 'node:fs'

mkdirSync('dist', { recursive: true })
cpSync('src/renderer', 'dist/renderer', { recursive: true })
cpSync('fixtures', 'dist/fixtures', { recursive: true })
