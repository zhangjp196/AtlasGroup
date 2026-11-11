import type { Conversation, Role, Team, TeamMember } from '@/api/types'

/** 一轮流式回复里的「发言角色」身份（群聊里一轮会有多个）。 */
export interface LiveSpeaker {
  id: string
  name: string
  avatar: string
  sub: string
}

export interface SpeakerEntry {
  speaker?: string
}

function clean(value: unknown): string {
  return String(value ?? '').trim()
}

/** 角色 / 群成员 → 展示用的发言者身份。 */
export function speakerOf(person: TeamMember | Role | null | undefined,
                          fallback = '助手'): LiveSpeaker {
  const name = clean(person?.name) || fallback
  const sub = [clean(person?.title), clean(person?.department)]
    .filter(Boolean)
    .join(' · ')
  return {
    id: clean(person?.id),
    name,
    avatar: clean(person?.avatar),
    sub,
  }
}

/** 按 id 找角色；角色库没有时回落到群组的主持 / 成员（临时角色）。 */
export function findPerson(roles: Role[], teams: Team[],
                           id: string): TeamMember | Role | null {
  const key = clean(id)
  if (!key) return null
  const role = roles.find((item) => item.id === key)
  if (role) return role
  for (const team of teams) {
    for (const person of [team.leader, ...(team.members || [])]) {
      if (person && clean(person.id) === key) return person
    }
  }
  return null
}

/**
 * 这一轮**最可能**先开口的角色。
 *
 * 与后端 `_group_reply_stream` 的路由一致：显式 @ 的成员 > 主持 > 首个成员；
 * 不是群聊就取会话绑定的角色。它只用于「start 事件到达前先把气泡挂到正确的
 * 名字上」，真实的发言者仍以后端 start 事件为准。
 */
export function expectedSpeaker(conversation: Conversation | null,
                                teams: Team[],
                                roles: Role[],
                                mentions: string[] = []): LiveSpeaker | null {
  if (!conversation) return null
  const team = conversation.team_id
    ? teams.find((item) => item.id === conversation.team_id)
    : undefined
  if (team) {
    const people = [team.leader, ...(team.members || [])]
      .filter(Boolean) as TeamMember[]
    const wanted = mentions.map((name) => clean(name).toLowerCase()).filter(Boolean)
    const mentioned = wanted.length
      ? people.find((person) => wanted.includes(clean(person.name).toLowerCase()))
      : undefined
    const person = mentioned || team.leader || people[0]
    if (person) return speakerOf(person)
    // 群组还没配角色：至少别显示成「助手」，用群名占位。
    return { id: '', name: clean(team.name) || '会话', avatar: clean(team.avatar), sub: '' }
  }
  const role = conversation.role_id
    ? roles.find((item) => item.id === conversation.role_id)
    : undefined
  return role ? speakerOf(role) : null
}

/**
 * 把流式轨迹按「发言角色」切成连续块。
 *
 * 群聊一轮里主持 / 成员依次开口，以前它们共用一个气泡，角色名会中途跳变，
 * 几个人的输出还混在一起。切成块之后每块单独渲染头像和名字，顺序保持原样。
 */
export function groupBySpeaker<T extends SpeakerEntry>(
  entries: T[],
): Array<{ speaker: string; entries: T[] }> {
  const groups: Array<{ speaker: string; entries: T[] }> = []
  for (const entry of entries) {
    const speaker = clean(entry.speaker)
    const last = groups[groups.length - 1]
    if (last && last.speaker === speaker) last.entries.push(entry)
    else groups.push({ speaker, entries: [entry] })
  }
  return groups
}

// ── 打字机式平滑 ────────────────────────────────────────────────────────
// 上游网关经常「卡几秒 → 一次吐一大段」，直接渲染就是一段一段闪。前端把已收到的
// 正文当成落后于目标的游标，按固定节拍往前推：小段自然跟上，大段用约
// REVEAL_CATCH_UP_TICKS 个节拍追平（不会越拖越远）。
export const REVEAL_INTERVAL_MS = 40
// 每拍至少推进的字数（≈150 字/秒）；差异化加速：每次再吃掉剩余量的 1/5。
// 实测节奏：120 字的一整段约 0.4s 放完，300 字约 0.6s，几个字的增量当拍就到。
export const REVEAL_MIN_CHARS = 6
export const REVEAL_STEP_DIVISOR = 5

/** 这一次节拍从 backlog 里推进多少字符（0 = 已经追平）。 */
export function revealStep(backlog: number): number {
  const remain = Math.max(0, Math.floor(Number(backlog) || 0))
  if (!remain) return 0
  return Math.min(remain, Math.max(REVEAL_MIN_CHARS, Math.ceil(remain / REVEAL_STEP_DIVISOR)))
}
