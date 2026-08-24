# 按证据表达 T05 跑步机上坡与换场

T05 自动标注把真实平地和跑步机 `0°` 表达为 `LEVEL`，把 `10°/15°/25°` 统一表达为 `INCLINE`，不根据固定顺序猜具体角度；相应 `WALKING` 区间边界写入受控备注“条件=跑步机·平地·`3 km/h`”或“条件=跑步机·上坡·`3 km/h`；角度=待人工复核”。正坡度调坡等待保持 `STILL · INCLINE`，跨入或跨出运行跑带为 `OTHER · INCLINE`，形成持续步态后才为 `WALKING · INCLINE`；最终从跑步机下到真实平地按主要承重面转移切换到 `OTHER · LEVEL`，站稳后为 `STILL · LEVEL`。自动结果不生成固定步数、周期数、时长或 `STEADY_GAIT_START/END` 窗口。
