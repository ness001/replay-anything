import { test, expect } from 'claude-code/testing'

const band = {
  plugin: 'replay-anything',
  surface: 'terminal' as const,
  component: 'AbovePrompt' as const,
  props: {
    hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100,
    scroll: { offset: 0, bodyRows: 10 }, view: {},
  },
}

for (const surface of ['terminal', 'desktop'] as const) {
  test(`recording persists across turns until explicitly stopped (${surface})`, async ($, on) => {
    const submitted: string[] = []
    const compacted: string[] = []
    let launches = 0
    on('ui.render', { component: 'AbovePrompt' }, () => ({
      type: 'Text', props: {}, children: ['Another mod'],
    }))
    on('session.compact', ($, e) => {
      compacted.push(e.instructions ?? '')
      return { messages: e.messages }
    })
    on('session.end', ($, e) => ({ sessionId: e.sessionId }))
    on('process.run', () => {
      launches++
      return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    })
    on('tool.call', { tool: 'Bash' }, () => ({ result: { stdout: '', stderr: '', interrupted: false } }))
    on('prompt.submit', ($, e) => {
      submitted.push(e.text)
      return { text: e.text }
    })
    on('classic.Stop', () => ({}))
    const ui = await $.ui.mount({ ...band, surface })
    expect(await ui.find({ type: 'Text', text: 'Another mod' })).toBeDefined()
    expect((await ui.find({ type: 'Text', text: /OFF/ }))?.props.color).toBe('gray')
    const unrelated = await $.tool.call({ tool: 'Bash', command: 'git status' })
    expect(unrelated.context).toBeUndefined()
    const browser = await $.tool.call({ tool: 'Bash', command: 'agent-browser snapshot -i' })
    expect(browser.context?.length).toBe(1)
    expect(launches).toBe(0)
    expect(submitted.length).toBe(0)
    await ui.press({ key: 'start-recording' })
    await $.tool.call({ tool: 'Bash', command: 'agent-browser open https://example.com', tool_use_id: 'recorded-open' })
    await $.classic.Stop({ stop_hook_active: false })
    await $.session.compact({
      trigger: 'manual', messages: [{ role: 'user', text: 'Keep recording.', toolUses: [] }],
      instructions: 'Keep user requirements.',
    })
    expect(compacted[0]).toContain('Keep user requirements.')
    await $.tool.call({ tool: 'Bash', command: 'agent-browser snapshot -i' })
    expect((await ui.find({ type: 'Text', text: /RECORDING/ }))?.props.color).toBe('red')
    await ui.press({ key: 'stop-recording' })
    expect((await ui.find({ type: 'Text', text: /OFF/ }))?.props.color).toBe('gray')
    expect(submitted.length).toBe(1)
    expect(submitted[0]).toContain('recorded-open')
    expect(launches).toBe(0)
    await ui.press({ key: 'start-recording' })
    await $.tool.call({ tool: 'Bash', command: 'git status' })
    await ui.press({ key: 'stop-recording' })
    expect(submitted.length).toBe(1)
    await ui.press({ key: 'start-recording' })
    await $.session.end({ reason: 'clear', sessionId: 'test', resume: { id: 'test' } })
    expect((await ui.find({ type: 'Text', text: /OFF/ }))?.props.color).toBe('gray')
    expect(submitted.length).toBe(1)
    await ui.unmount()
  })
}
