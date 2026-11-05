import { describe, expect, it } from 'vitest'
import { isDaytime, normalizeSchedule, scheduledTheme } from './theme'

describe('theme scheduling', () => {
  it('normalizes invalid schedules', () => {
    const schedule = normalizeSchedule({ mode: 'nope', interval: 1, dayStart: '99:99' })
    expect(schedule.mode).toBe('manual')
    expect(schedule.interval).toBe(5)
    expect(schedule.dayStart).toBe('06:00')
  })

  it('detects daytime and picks the day theme', () => {
    const schedule = normalizeSchedule({ mode: 'daynight', dayStart: '06:00', nightStart: '19:00' })
    const noon = new Date('2026-11-04T12:00:00')
    expect(isDaytime(schedule, noon)).toBe(true)
    expect(scheduledTheme(schedule, 'dark', noon)).toBe(schedule.day)
  })
})
