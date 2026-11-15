import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'
import type { Recording } from '../types'
import { finishMessage, recordingContext } from './messages'

const initial: Recording = { active: false, generation: 0, calls: [] }
const recording = atom({ plugin: 'replay-anything', key: 'recording' } as const, initial)
const browserCommand = /(?:^|[\s;&|("'])(?:playwright-cli|agent-browser|@playwright\/cli)(?:@[^\s]+|\.cmd|\.exe)?(?=\s|$)/

async function start($: EngineInterface) {
  await update($, recording, previous => previous.active ? previous :
    { active: true, generation: previous.generation + 1, calls: [] })
}

async function stop($: EngineInterface) {
  let calls: string[] = []
  await update($, recording, previous => {
    calls = previous.active ? previous.calls : []
    return { ...previous, active: false }
  })
  if (calls.length) {
    try {
      const submitted = await $.prompt.submit({ text: finishMessage(calls) })
      if (submitted.drop !== undefined) {
        $.ui.toast('Recording stopped. Finalization was blocked; ask to save the recorded browser work.')
      }
    } catch {
      $.ui.toast('Recording stopped. Ask to save the recorded browser work as a recipe.')
    }
  }
}

export const register: Register = on => {
  on('prompt.submit', async ($, e, next) => {
    const state = await read($, recording)
    return next(state.active ? { ...e, context: [...(e.context ?? []), recordingContext] } : e)
  })

  on('session.compact', async ($, e, next) => {
    const state = await read($, recording)
    if (!state.active || e.agentId !== undefined) return next(e)
    return next({ ...e, instructions: [e.instructions,
      'Replay-Anything recording is still active across turns. Preserve the browser operations, ' +
      'stable selectors, inputs (never secrets), outcomes and tool call IDs needed to build its recipe. ' +
      'Do not end recording or finalize it during compaction. Recording call IDs: ' + state.calls.join(', '),
    ].filter(Boolean).join('\n') })
  })

  on('session.end', async ($, e, next) => {
    await update($, recording, previous => ({ ...previous, active: false }))
    return next(e)
  })

  on('classic.PreToolUse', { tool_name: 'Bash', tool_input: { command: browserCommand } }, async ($, e, next) => {
    const result = await next(e)
    return {
      ...result,
      additionalContext: [...(result.additionalContext ?? []),
        'Before driving this site, call Replay-Anything list_recipes. Use run_recipe if one fits; ' +
        'drive the browser only when none fits or replay fails with step_failed.'],
    }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const existing = await next(e)
    const state = await read($, recording)
    const { Box, Text, Button } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        {existing}
        <Box>
          <Text>Replay-Anything: </Text>
          <Text color={state.active ? 'red' : 'gray'}>
            {state.active ? '● RECORDING  ' : 'OFF  '}
          </Text>
          {state.active
            ? <Button key="stop-recording" label="Stop recording" onPress={() => stop($)} />
            : <Button key="start-recording" label="Start recording" onPress={() => start($)} />}
        </Box>
      </Box>
    )
  })

  on('tool.call', { tool: 'Bash', command: browserCommand }, async ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    const state = await read($, recording)
    if (state.active && e.tool_use_id) {
      await update($, recording, current => current.active && current.generation === state.generation
        ? { ...current, calls: [...current.calls, e.tool_use_id!] }
        : current)
    }
    const result = await next(e)
    if (result.deny !== undefined) return result
    return { ...result, context: [...(result.context ?? []), state.active ? recordingContext :
      'Replay-Anything recording is OFF. Before further browser operations, check list_recipes for a saved recipe; ' +
      'use run_recipe when one fits. Do not auto-save browser work while recording is off.'] }
  })
}
