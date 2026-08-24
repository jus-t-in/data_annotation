# V2 地形集合与合法组合状态

V2 地形标签严格限定为 `LEVEL`、`ASCENT`、`DESCENT`、`INCLINE`。`LEVEL` 允许全部九个 V2 活动标签；`ASCENT` 与 `DESCENT` 只允许 `WALKING`、`STILL`；`INCLINE` 允许 `WALKING`、`STILL` 以及 T05 换场中的 `OTHER`。活动和地形候选分开生成，非法组合不自动修正，保持上一确认的合法组合并产生阻断 QA；跑步机具体角度继续只写入受控备注，不扩展地形枚举。
