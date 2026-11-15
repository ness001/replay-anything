export type Recording = {
  active: boolean
  generation: number
  calls: string[]
}

declare module 'claude-code' {
  interface PluginState {
    'replay-anything': { recording: Recording }
  }
}
