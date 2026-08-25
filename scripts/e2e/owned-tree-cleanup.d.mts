export function removeOwnedTreeWithoutFollowingReparsePoints(
  root: string,
  options?: {
    beforeEnumerate?: (path: string) => void | Promise<void>
    onOperationEnd?: () => void
    onOperationStart?: () => void
  },
): Promise<void>
