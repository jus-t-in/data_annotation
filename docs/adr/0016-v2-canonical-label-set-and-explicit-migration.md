# V2 规范标签集合与显式迁移

V2 自动输出、编辑器下拉选项和结构校验严格只接受 `STILL`、`WALKING`、`BEND`、`SQUAT_DESCENT`、`SQUAT_HOLD`、`SQUAT_ASCENT`、`HIGH_KNEE_ALTERNATING`、`STANDING_ADJUSTMENT`、`OTHER` 这九个活动标签及 V2 地形标签。V1 的 `SQUAT`、`HIGH_KNEE_SINGLE`、`TURNING_LEFT`、`TURNING_RIGHT`、`SHUFFLE` 只在显式训练数据迁移导出时按 V2 说明处理，不回写旧标注文件；编辑器打开含旧标签的历史文件时必须标为待迁移并阻止正式保存，不能静默改名。
