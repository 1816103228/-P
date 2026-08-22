// 语音通话纯工具：回声判断、base64 转换、句子切分（从原 voice_page.html 移植）。
//
// 回声判断原则：宁可偶尔把"疑似回声"的用户插话忽略（用户可再说一遍），
// 也绝不让小P 的回声漏判（否则小P 自问自答/被自己打断）。

/** 识别文本是否为某播报文本的回声（容忍 ASR 同音/漏字/插入误差）。 */
export function echoMatch(t, b) {
  if (!t || !b) return false
  // 1-2 字的插话（"好""继续""停"）不足以判定为回声：放行，避免把用户打断吞掉
  if (t.length < 3) return false
  // 1）完全子串：识别文本是播报文本的连续片段 → 回声（≥3 字才判定）
  if (b.indexOf(t) >= 0) return true
  if (t.length < 8) {
    // 2a）短-中文本（<8 字）：在 b 中找与 t 等长、至多 1 字差异的连续片段
    let s = 0
    while ((s = b.indexOf(t[0], s)) >= 0) {
      const seg = b.substr(s, t.length)
      if (seg.length === t.length) {
        let diff = 0
        for (let i = 0; i < t.length; i++) {
          if (seg[i] !== t[i]) diff++
        }
        if (diff <= 1) return true
      }
      s++
    }
    // 2b）子序列覆盖 ≥70%（漏字/插入虚词）
    let j = 0
    let n = 0
    for (let i = 0; i < t.length; i++) {
      j = b.indexOf(t[i], j)
      if (j < 0) break
      n++
      j++
    }
    if (n / t.length >= 0.7) return true
    return false
  }
  // 3）最长连续公共片段 ≥ max（5，识别文本75%）→ 回声
  const need = Math.max(5, Math.floor(t.length * 0.75))
  let best = 0
  for (let i = 0; i < t.length && best < need; i++) {
    for (let j = 0; j < b.length && best < need; j++) {
      let k = 0
      while (i + k < t.length && j + k < b.length && t[i + k] === b[j + k]) k++
      if (k > best) best = k
    }
  }
  if (best >= need) return true
  // 4）字符重合率兜底（ASR 严重变形时）：≥8 字文本重合率 ≥0.75
  let hit = 0
  for (let i = 0; i < t.length; i++) {
    if (b.indexOf(t[i]) >= 0) hit++
  }
  return hit / t.length >= 0.75
}

/** 归一化：只保留中日韩文字母数字，用于回声比对。 */
export function normalizeForEcho(text) {
  return (text || '').replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, '')
}

export function bytesToB64(bytes) {
  let bin = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
  }
  return btoa(bin)
}

export function base64ToBytes(b64) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

/** 与服务端一致的模式切换触发词（避免"模拟面试是什么"这类提问误切模式）。 */
export const MOCK_START_RE =
  /(开始面试|开始模拟面试|^(?:我想|我要)?模拟面试(?:吧|一下|下|了)?$|(?:我想|我要|来|做|进行|试试|开启|帮我|开始一[场次]).{0,4}模拟面试)/

export function calcRms(data) {
  let sum = 0
  for (let i = 0; i < data.length; i++) {
    const s = data[i]
    sum += s * s
  }
  return Math.sqrt(sum / data.length)
}
