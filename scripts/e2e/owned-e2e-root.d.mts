export function initializeOwnedE2ERoot(path: string): string
export function initializeDefaultE2ERoot(path: string): string
export function assertSafeExistingE2EPath(path: string): void
export function initializeOwnedE2EPaths(rootPath: string, artifactsPath: string): { e2eRoot: string; artifactsRoot: string }
export function validateOwnedE2EPaths(paths: { e2eRoot: string; artifactsRoot: string }): void
